import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from plate_history import RETENTION_SECONDS, record_result, load_history, prune_history, record_clip_result, replay_progress
from record_protocol import atomic_json, read_json


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 10 * RETENTION_SECONDS

    def test_retains_negative_and_confirmed_and_deduplicates(self):
        good = dict(id="a", timestamp=self.now, confirmed=True, text="ABC123")
        bad = dict(id="b", timestamp=self.now-2, confirmed=False, outcome="no_plate")
        for event in (good, bad, good):
            self.assertTrue(record_result(self.root, event, self.now))
        self.assertEqual([x["id"] for x in load_history(self.root, self.now)], ["a", "b"])

    def test_expiry_removes_owned_results_and_preserves_video(self):
        video = self.root/"sample.mp4"
        video.write_bytes(b"fixture")
        event = dict(id="a", timestamp=self.now, confirmed=True)
        record_result(self.root, event, self.now)
        atomic_json(self.root/"runtime/plate_events/a.json", event)
        self.assertEqual(prune_history(self.root, self.now+RETENTION_SECONDS+1), 2)
        self.assertTrue(video.exists())
        self.assertFalse(load_history(self.root, self.now+RETENTION_SECONDS+1))

    def test_rejects_expired_future_invalid_and_unknown_outcomes(self):
        for stamp in (self.now-RETENTION_SECONDS-1, self.now+61, float("nan")):
            self.assertFalse(record_result(self.root, dict(id="a", timestamp=stamp, confirmed=True), self.now))
        self.assertFalse(record_result(self.root, dict(id="a", timestamp=self.now, outcome="pending"), self.now))

    def test_legacy_events_loaded_without_copy(self):
        atomic_json(self.root/"runtime/plate_events/a.json", dict(id="a", timestamp=self.now, confirmed=True))
        self.assertEqual(len(load_history(self.root, self.now)), 1)

    def test_path_and_event_id_cannot_escape_result_folder(self):
        record_result(self.root, dict(id="../../escape", timestamp=self.now, confirmed=True,
                                      source_video="C:\\private\\clip.mp4"), self.now)
        self.assertEqual(load_history(self.root, self.now)[0]["source_video"], "clip.mp4")
        self.assertEqual(len(list((self.root/"runtime/plate_history").glob("*.json"))), 1)

    def test_completed_clip_has_negative_or_error_not_fake_plate(self):
        state = self.root/"runtime/plate_recognition/state.json"
        atomic_json(state, dict(status="READY", last_sample_at=self.now+20,
                               updated_at=self.now+30, detections=0))
        record_clip_result(self.root, "first.mp4", self.now, 30, now=self.now+30)
        atomic_json(state, dict(status="ERROR", last_sample_at=self.now+20))
        record_clip_result(self.root, "failed.mp4", self.now+1, 30, now=self.now+31)
        results = {e["source_video"]:e for e in load_history(self.root, self.now+31)}
        self.assertEqual(results["first.mp4"]["outcome"], "no_plate")
        self.assertEqual(results["failed.mp4"]["outcome"], "error")
        self.assertNotIn("text", results["first.mp4"])

    def test_completed_clip_enriches_confirmed_result_without_negative(self):
        record_result(self.root, dict(id="a", timestamp=self.now+1, confirmed=True, text="ABC123"), self.now+3)
        record_clip_result(self.root, "clip.mp4", self.now, 3, now=self.now+3)
        result = load_history(self.root, self.now+3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_video"], "clip.mp4")

    def test_confirmed_replay_replaces_old_negative_summary(self):
        record_result(self.root, dict(id="old", timestamp=self.now, confirmed=False,
                                     outcome="no_plate", source_video="clip.mp4"), self.now)
        record_result(self.root, dict(id="new", timestamp=self.now, confirmed=True,
                                     text="ABC123", source_video="clip.mp4"), self.now)
        self.assertEqual([e["id"] for e in load_history(self.root, self.now)], ["new"])

    def test_seven_day_boundary_is_inclusive_until_expired(self):
        record_result(self.root, dict(id="boundary", timestamp=self.now-RETENTION_SECONDS,
                                     confirmed=False, outcome="no_plate"), self.now)
        self.assertEqual(prune_history(self.root, self.now), 0)
        self.assertEqual(len(load_history(self.root, self.now)), 1)
        self.assertEqual(prune_history(self.root, self.now+1), 1)

    def test_unreadable_history_cannot_be_reported_as_no_plate(self):
        event_path = self.root/"runtime/plate_events/locked.json"
        atomic_json(event_path, dict(id="locked", timestamp=self.now+1, confirmed=True, text="ABC123"))
        atomic_json(self.root/"runtime/plate_recognition/state.json",
                    dict(status="READY", last_sample_at=self.now+20, updated_at=self.now+30, detections=0))
        def transient_read(path, default=None):
            return default if Path(path) == event_path else read_json(path, default)
        with patch("plate_history.read_json", side_effect=transient_read):
            record_clip_result(self.root, "clip.mp4", self.now, 30, now=self.now+30)
        summary = next(event for event in load_history(self.root, self.now+30) if not event.get("confirmed"))
        self.assertEqual(summary["outcome"], "error")
        self.assertTrue(event_path.exists())

    def test_missing_malformed_stale_or_waiting_state_never_claims_no_plate(self):
        state_path = self.root/"runtime/plate_recognition/state.json"
        base = dict(status="READY", last_sample_at=self.now+20, updated_at=self.now+30, detections=0)
        for index, state in enumerate((None, [], {}, dict(base, updated_at=self.now-1),
                                       dict(base, status="WAITING"), dict(base, detections=-1))):
            atomic_json(state_path, state)
            record_clip_result(self.root, "failed%d.mp4" % index, self.now, 30, now=self.now+30)
        self.assertTrue(all(event["outcome"] == "error" for event in load_history(self.root, self.now+30)))

    def test_unconfirmed_candidate_is_insufficient_and_does_not_queue_telegram(self):
        atomic_json(self.root/"runtime/plate_recognition/state.json",
                    dict(status="READY", last_sample_at=self.now+20, updated_at=self.now+30, detections=1))
        record_clip_result(self.root, "clip.mp4", self.now, 30, now=self.now+30)
        result = load_history(self.root, self.now+30)[0]
        self.assertEqual(result["outcome"], "insufficient")
        self.assertNotIn("text", result)
        self.assertFalse((self.root/"runtime/plate_events").exists())
        self.assertFalse((self.root/"runtime/telegram_plate_delivery").exists())

    def test_prune_preserves_unrecognized_json_and_only_expires_valid_receipts(self):
        unknown = self.root/"runtime/plate_history/unknown.json"
        corrupt = self.root/"runtime/plate_history/corrupt.json"
        receipt = self.root/"runtime/telegram_plate_delivery/sent.json"
        other = self.root/"runtime/telegram_plate_delivery/unknown.json"
        atomic_json(unknown, dict(timestamp=self.now-RETENTION_SECONDS-1))
        corrupt.write_text("{", encoding="utf-8")
        atomic_json(receipt, dict(event_id="sent", sent_at=self.now-RETENTION_SECONDS-1))
        atomic_json(other, dict(sent_at=self.now-RETENTION_SECONDS-1))
        self.assertEqual(prune_history(self.root, self.now), 0)
        self.assertTrue(unknown.exists())
        self.assertTrue(corrupt.exists())
        self.assertTrue(other.exists())
        self.assertFalse(receipt.exists())

    def test_replay_progress_aggregates_two_current_shards_and_pauses_stale_work(self):
        folder = self.root/"runtime/plate_replay"
        atomic_json(folder/"progress-0-of-1.json", dict(shard_index=0, shard_count=1, total=100,
                    complete=100, skipped=0, pending=0, errors=0, status="COMPLETE", updated_at=self.now-100))
        atomic_json(folder/"progress-0-of-2.json", dict(shard_index=0, shard_count=2, total=5,
                    complete=1, skipped=1, pending=1, errors=0, status="RUNNING", updated_at=self.now-5))
        atomic_json(folder/"progress-1-of-2.json", dict(shard_index=1, shard_count=2, total=4,
                    complete=2, skipped=1, pending=0, errors=1, status="COMPLETE_WITH_ERRORS", updated_at=self.now))
        progress = replay_progress(self.root, self.now)
        self.assertEqual({key:progress[key] for key in ("total", "processed", "pending", "errors")},
                         dict(total=9, processed=6, pending=1, errors=1))
        self.assertTrue(progress["running"])
        self.assertEqual(progress["status"], "RUNNING")
        progress = replay_progress(self.root, self.now+31)
        self.assertFalse(progress["running"])
        self.assertTrue(progress["stale_running"])
        self.assertEqual(progress["status"], "PAUSED")

    def test_replay_progress_ignores_corrupt_or_invalid_numeric_state(self):
        self.assertEqual(replay_progress(self.root, self.now), {})
        folder = self.root/"runtime/plate_replay"
        valid = dict(shard_index=0, shard_count=1, total=5, complete=1, skipped=0,
                     pending=0, errors=0, status="RUNNING", updated_at=self.now)
        for value in ([], dict(valid, complete=True), dict(valid, total=-1),
                      dict(valid, complete=6), dict(valid, updated_at="nan"), dict(valid, shard_count=2)):
            atomic_json(folder/"progress-0-of-1.json", value)
            self.assertEqual(replay_progress(self.root, self.now), {})
        (folder/"progress-0-of-1.json").write_text("{", encoding="utf-8")
        self.assertEqual(replay_progress(self.root, self.now), {})


if __name__ == "__main__":
    unittest.main()
