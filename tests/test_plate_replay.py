"""Private replay timing, resume and failure semantics without real AI models."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plate_recognition import TAIPEI
from plate_replay import (discover_videos, identity, path_shard, run_batch, scan_video,
                          source_busy, video_started_at)


NOW = datetime(2030, 6, 8, 12, tzinfo=TAIPEI).timestamp()


class Capture:
    def __init__(self, count=20, fps=10, readable=None, opened=True, timestamps=None):
        self.count, self.fps = count, fps
        self.readable = count if readable is None else readable
        self.opened, self.timestamps = opened, timestamps
        self.index = -1
        self.released = False

    def isOpened(self):
        return self.opened

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.count
        if prop == cv2.CAP_PROP_POS_MSEC:
            return (self.timestamps[self.index] if self.timestamps else self.index / self.fps) * 1000
        return 0

    def grab(self):
        self.index += 1
        return self.index < self.readable

    def retrieve(self):
        return True, np.full((4, 6, 3), self.index, np.uint8)

    def release(self):
        self.released = True


def observation(text="AX1239", confidence=0.9):
    return {"bbox": [1, 2, 4, 3], "detector_confidence": 0.8,
            "selected": {"raw_text": text, "confidence": confidence},
            "variants": [{"raw_text": text, "confidence": confidence,
                          "variant": "original", "source_region": [0, 0, 6, 4]}]}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # Windows CI may supply TEMP through its 8.3 short-path alias.
        self.root = Path(self.temporary.name).resolve()
        self.video = self.make_video("SuperLive_20300608_100000_example.mp4")
        self.history = []

    def tearDown(self):
        self.temporary.cleanup()

    def make_video(self, name, modified=NOW - 100):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fictional fixture")
        os.utime(path, (modified, modified))
        return path

    def writer(self, root, event, now=None):
        self.history.append(event)
        return True

    def batch(self, videos=None, **kwargs):
        options = dict(alpr_factory=lambda: object(), detector=lambda *args: [],
                       capture_factory=lambda _: Capture(), history_writer=self.writer,
                       clock=lambda: NOW, sleeper=lambda _: None, reporter=lambda _: None)
        options.update(kwargs)
        return run_batch(self.root, videos or [self.video], **options)

    def manifest(self):
        paths = list((self.root / "runtime" / "plate_replay" / "manifest").glob("*.json"))
        return [json.loads(path.read_text()) for path in paths]

    def test_filename_date_wins_over_mtime_and_unknown_name_uses_mtime(self):
        start, basis = video_started_at(self.video)
        self.assertEqual(start, NOW - 7200)
        self.assertEqual(basis, "recording_filename")
        unknown = self.make_video("example.mp4", NOW - 123)
        self.assertEqual(video_started_at(unknown), (NOW - 123, "file_mtime"))

    def test_discovery_prioritizes_explicit_copy_then_recent_originals(self):
        newest = self.make_video("SuperLive_20300608_110000_b.mp4")
        older = self.make_video("SuperLive_20300607_100000_b.MP4")
        self.make_video("SuperLive_20300501_100000_old.mp4")
        duplicate = self.make_video("SuperLive_20300608_110000_b_telegram.mp4")
        self.make_video("SuperLive_20300608_110000_other_telegram.mp4")
        found = discover_videos(self.root, [duplicate, self.video], now=NOW)
        self.assertEqual(found, [duplicate, self.video, newest, older])

    def test_real_source_times_and_raw_fuzzy_evidence_are_preserved(self):
        cap = Capture()
        result = scan_video(self.video, object(), ["AB1234"],
            detector=lambda *args: [observation()], capture_factory=lambda _: cap,
            sleeper=lambda _: None)
        self.assertTrue(cap.released)
        self.assertEqual(result["processed_frames"], 4)
        self.assertEqual(result["decoded_frames"], 20)
        self.assertEqual(result["outcome"], "confirmed")
        event = result["events"][0]
        self.assertEqual(event["raw_text"], "AX1239")
        self.assertEqual(event["matched"], "AB1234")
        self.assertEqual(event["distance"], 2)
        self.assertEqual(event["timestamp"], NOW - 7200 + 1)
        self.assertEqual(event["offset_seconds"], 1)
        self.assertEqual([row["offset_seconds"] for row in event["evidence"]], [0, 0.5, 1])
        self.assertTrue(all(row["variants"] for row in event["evidence"]))
        again = scan_video(self.video, object(), ["AB1234"],
            detector=lambda *args: [observation()], capture_factory=lambda _: Capture(),
            sleeper=lambda _: None)
        self.assertEqual(event["id"], again["events"][0]["id"])

    def test_missing_presentation_times_fall_back_to_frame_fps(self):
        result = scan_video(self.video, object(), detector=lambda *args: [observation()],
            capture_factory=lambda _: Capture(timestamps=[0] * 20), sleeper=lambda _: None)
        self.assertEqual(result["processed_frames"], 4)
        self.assertEqual(result["events"][0]["offset_seconds"], 1)

    def test_plate_box_without_readable_text_is_insufficient(self):
        candidate = observation()
        candidate["selected"] = None
        result = scan_video(self.video, object(), detector=lambda *args: [candidate],
            capture_factory=lambda _: Capture(), sleeper=lambda _: None)
        self.assertEqual(result["outcome"], "insufficient")
        self.assertEqual(result["events"], [])

    def test_incomplete_decode_is_error_even_after_valid_confirmation(self):
        self.batch(detector=lambda *args: [observation()],
                   capture_factory=lambda _: Capture(readable=16))
        self.assertTrue(self.history[0]["confirmed"])
        self.assertEqual(self.history[-1]["outcome"], "error")
        self.assertEqual(self.history[-1]["error"], "incomplete_decode")
        self.assertEqual(self.manifest()[0]["state"], "error")

    def test_model_failure_never_becomes_no_plate_and_is_retryable(self):
        first = self.batch(alpr_factory=Mock(side_effect=RuntimeError("private detail")))
        self.assertEqual(first["errors"], 1)
        self.assertEqual(self.history[-1]["outcome"], "error")
        self.assertNotIn("private detail", self.history[-1]["error"])
        self.assertEqual(self.batch()["complete"], 1)

    def test_each_video_has_its_own_consensus(self):
        other = self.make_video("SuperLive_20300608_100001_other.mp4")
        self.batch([self.video, other], detector=lambda *args: [observation()],
                   capture_factory=lambda _: Capture(count=10))
        self.assertEqual([event["outcome"] for event in self.history], ["insufficient"] * 2)

    def test_active_recording_and_recent_files_are_deferred(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "recording_state.json").write_text(json.dumps({
            "status": "RECORDING", "file": str(self.video), "updated_at": NOW}))
        self.assertTrue(source_busy(self.root, self.video, identity(self.video), NOW))
        self.assertEqual(self.batch()["pending"], 1)
        self.assertEqual(self.history, [])
        recent = self.make_video("recent.mp4", NOW - 2)
        self.assertEqual(self.batch([recent])["pending"], 1)

    def test_stale_recording_state_does_not_permanently_block_completed_file(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "recording_state.json").write_text(json.dumps({
            "status": "RECORDING", "file": str(self.video), "updated_at": NOW - 31}))
        self.assertFalse(source_busy(self.root, self.video, identity(self.video), NOW))
        self.assertEqual(self.batch()["complete"], 1)

    def test_source_changed_during_scan_publishes_nothing_and_retries(self):
        changed = [False]

        def changing_detector(*args):
            if not changed[0]:
                self.video.write_bytes(b"source changed during scan")
                os.utime(self.video, (NOW - 100, NOW - 100))
                changed[0] = True
            return [observation()]

        self.assertEqual(self.batch(detector=changing_detector)["pending"], 1)
        self.assertEqual(self.history, [])
        self.assertEqual(self.manifest()[0]["state"], "pending")
        self.assertEqual(self.batch()["complete"], 1)

    def test_resume_skips_finished_identity_and_rescans_modified_file(self):
        self.assertEqual(self.batch()["complete"], 1)
        factory = Mock(side_effect=AssertionError("must not create model"))
        self.assertEqual(self.batch(alpr_factory=factory)["skipped"], 1)
        factory.assert_not_called()
        self.video.write_bytes(b"changed contents")
        os.utime(self.video, (NOW - 100, NOW - 100))
        self.assertEqual(self.batch()["complete"], 1)

    def test_two_shards_have_disjoint_inputs_and_independent_progress(self):
        videos = [self.video] + [self.make_video("clip%d.mp4" % i) for i in range(3)]
        counts = [sum(path_shard(path, 2) == shard for path in videos) for shard in range(2)]
        self.assertEqual(self.batch(videos, shard_index=0, shard_count=2)["complete"], counts[0])
        self.assertEqual(self.batch(videos, shard_index=1, shard_count=2)["complete"], counts[1])
        self.assertEqual(len(self.manifest()), 4)
        state = self.root / "runtime" / "plate_replay"
        for shard in range(2):
            progress = json.loads((state / ("progress-%d-of-2.json" % shard)).read_text())
            self.assertEqual(progress["batch_total"], 4)
            self.assertEqual(progress["total"], counts[shard])
            self.assertEqual(progress["status"], "COMPLETE")
        self.assertEqual(len({row["source_video"] for row in self.history}), 4)

    def test_newer_recording_does_not_change_existing_shard_ownership(self):
        videos = [self.video] + [self.make_video("clip%d.mp4" % i) for i in range(5)]
        before = {str(path): path_shard(path, 2) for path in videos}
        newest = self.make_video("SuperLive_20300608_110000_new.mp4")
        discovered = discover_videos(self.root, [self.video], now=NOW)
        self.assertIn(newest, discovered)
        after = {str(path): path_shard(path, 2) for path in discovered if path in videos}
        self.assertEqual(before, after)

    def test_retention_rejection_is_recorded_without_claiming_history_written(self):
        old = self.make_video("SuperLive_20300501_100000_old.mp4")
        result = self.batch([old], history_writer=lambda *args, **kwargs: False)
        self.assertEqual(result["ignored_retention"], 1)
        self.assertEqual(result["history_written"], 0)
        self.assertEqual(self.manifest()[0]["ignored_retention"], 1)


if __name__ == "__main__":
    unittest.main()
