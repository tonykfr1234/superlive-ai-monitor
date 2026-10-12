"""Offline recorder/queue tests. No camera, Telegram account, or network needed."""
import json
import sys
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import recorder as recorder_module
import telegram_video
from record_protocol import atomic_json, read_json, telegram_send_allowed
from recorder import FrameTimeline, Recorder, rotation_candidates, valid_request


class FakeClock:
    def __init__(self):
        self.now = 100.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += max(0.00001, seconds)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        atomic_json(self.root / "config" / "recording_config.json", {
            "record_root": str(self.root / "recordings"), "target_fps": 10,
            "segment_minutes": 30, "capture_max_failures": 5,
            "rotation_start_used_percent": 80, "rotation_stop_used_percent": 78})
    def tearDown(self):
        self.temporary.cleanup()

    def test_request_age_and_malformed_values(self):
        self.assertTrue(valid_request({"request_id": "a", "created_at": 100}, 130))
        for request in (None, {}, {"request_id": "a", "created_at": 99},
                        {"request_id": "a", "created_at": float("nan")},
                        {"request_id": "a", "created_at": 150},
                        {"request_id": 1, "created_at": 130}):
            self.assertFalse(valid_request(request, 130))

    def test_slow_capture_does_not_shorten_playback(self):
        timeline = FrameTimeline(10, 100)
        for now in (100, 100.7, 102.3, 168.2, 399.9, 400):
            timeline.written += timeline.due(now)
        self.assertTrue(timeline.done(400))
        self.assertFalse(timeline.done(399.99))
        self.assertEqual(timeline.written, 3000)
        self.assertEqual(timeline.due(450), 0)

    def make_fake_recorder(self, real_video=False, stop_after=None, end_after=None, end_reason="NO_MOVEMENT"):
        recorder = Recorder(self.root)
        clock = FakeClock()
        recorder.log = Mock()
        recorder.publish = Mock()
        recorder.heartbeat = Mock()
        recorder.rotate = Mock(return_value=True)
        recorder.find_window = Mock(return_value=1)
        recorder.mss = SimpleNamespace(MSS=lambda: SimpleNamespace(close=Mock()))
        writer = Mock()
        writer.isOpened.return_value = True
        writer.released = False
        def writer_factory(path, *args):
            Path(path).write_bytes(b"test video")
            return writer
        def release():
            writer.released = True
        writer.release.side_effect = release
        if real_video:
            import cv2
            import numpy as np
            recorder.cv2 = cv2
            frames = [np.zeros((120, 160, 3), dtype=np.uint8), np.zeros((160, 120, 3), dtype=np.uint8)]
        else:
            recorder.cv2 = SimpleNamespace(VideoWriter=writer_factory, VideoWriter_fourcc=lambda *x: 0,
                resize=lambda frame, size: SimpleNamespace(shape=(size[1], size[0], 3)))
            frames = [SimpleNamespace(shape=(120, 160, 3)), SimpleNamespace(shape=(160, 120, 3))]
        count = [0]
        def capture(*_):
            # Deliberately slower than 10 FPS, alternating dimensions.
            clock.now += 0.18
            count[0] += 1
            if stop_after and clock.now >= 100 + stop_after:
                recorder.stop.touch()
            if end_after and clock.now >= 100 + end_after and not recorder.end_request_file.exists():
                signal = {"request_id": "one", "created_at": clock.now, "reason": end_reason}
                if end_reason == "MOVEMENT":
                    signal["next_request"] = {"request_id": "two", "created_at": clock.now,
                                              "count": 1, "confidence": 0.9, "roi": None}
                atomic_json(recorder.end_request_file, signal)
            return frames[count[0] % 2]
        recorder.capture = capture
        queued = []
        def enqueue(path, request, duration, complete):
            if not real_video:
                self.assertTrue(writer.released, "MP4 must close before queue persistence")
            queued.append((path, duration, complete, clock.now))
        recorder.uploader = SimpleNamespace(enqueue=enqueue)
        return recorder, clock, writer, queued

    def test_record_exactly_five_minutes_despite_legacy_config_and_resize(self):
        recorder, clock, writer, queued = self.make_fake_recorder()
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        self.assertEqual(writer.write.call_count, 3000)
        self.assertEqual(queued[0][1], 300)
        self.assertTrue(queued[0][2])
        self.assertGreaterEqual(queued[0][3], 400)
        shapes = {call.args[0].shape for call in writer.write.call_args_list}
        self.assertEqual(len(shapes), 1)

    def test_stop_preserves_and_queues_partial_clip(self):
        recorder, clock, writer, queued = self.make_fake_recorder(stop_after=3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertFalse(recorder.record({"request_id": "one"}))
        self.assertTrue(writer.released)
        self.assertFalse(queued[0][2])
        self.assertGreater(queued[0][1], 0)
        self.assertLess(queued[0][1], 300)

    def test_recorder_rejects_early_end_but_accepts_minimum_and_late_rollover(self):
        recorder, clock, _, _ = self.make_fake_recorder()
        successor = {"request_id": "two", "created_at": 100, "count": 1, "confidence": 0.9, "roi": None}
        for reason in ("NO_MOVEMENT", "MOVEMENT"):
            signal = {"request_id": "one", "created_at": 100, "reason": reason, "next_request": successor}
            for elapsed, accepted in ((119.9, False), (120, True), (299, True), (300, False), (float("nan"), False)):
                with self.subTest(reason=reason, elapsed=elapsed), patch.object(recorder_module.time, "time", return_value=100):
                    recorder.next_request = None
                    self.assertEqual(recorder.accept_end(signal, "one", elapsed), accepted)
                    self.assertEqual(recorder.next_request is not None, accepted and reason == "MOVEMENT")

    def test_an_early_signal_cannot_shorten_a_clip_and_expires_before_minimum(self):
        recorder, clock, _, queued = self.make_fake_recorder(end_after=3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        self.assertEqual(queued[0][1], 300)

    def test_manual_partial_clip_remains_silent_until_original_deadline(self):
        recorder, clock, _, queued = self.make_fake_recorder(stop_after=3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertFalse(recorder.record({"request_id": "one"}))
        quiet = read_json(recorder.quiet_file)
        self.assertAlmostEqual(quiet["quiet_until"], 220.18)
        self.assertLess(queued[0][1], 4)
        self.assertFalse(queued[0][2])
        self.assertFalse(telegram_send_allowed(self.root, now=quiet["quiet_until"]-0.001))
        self.assertTrue(telegram_send_allowed(self.root, now=quiet["quiet_until"]))

    def test_capture_failure_preserves_partial_video_and_quiet_deadline(self):
        recorder, clock, _, queued = self.make_fake_recorder()
        recorder.config["capture_max_failures"] = 1
        original_capture = recorder.capture
        calls = [0]
        def capture(*args):
            calls[0] += 1
            if calls[0] > 2:
                raise RuntimeError("synthetic capture failure")
            return original_capture(*args)
        recorder.capture = capture
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep), \
                self.assertRaisesRegex(RuntimeError, "partial clip preserved"):
            recorder.record({"request_id": "one"})
        self.assertEqual(len(queued), 1)
        self.assertFalse(queued[0][2])
        self.assertGreater(queued[0][1], 0)
        self.assertLess(queued[0][1], 120)
        quiet = read_json(recorder.quiet_file)
        self.assertFalse(telegram_send_allowed(self.root, now=clock.now))
        self.assertTrue(telegram_send_allowed(self.root, now=quiet["quiet_until"]))

    def test_pre_cap_movement_processed_after_cap_keeps_successor(self):
        recorder, _, _, _ = self.make_fake_recorder()
        signal = {"request_id": "one", "created_at": 400, "reason": "MOVEMENT",
                  "created_monotonic": 399.9,
                  "next_request": {"request_id": "two", "created_at": 400,
                                   "count": 1, "confidence": 0.9, "roi": None}}
        with patch.object(recorder_module.time, "time", return_value=400):
            self.assertTrue(recorder.accept_end(signal, "one", 300.05, started=100))
            self.assertEqual(recorder.next_request["request_id"], "two")
            for stamp in (400, 219.9, float("nan"), True, None):
                signal["created_monotonic"] = stamp
                self.assertFalse(recorder.accept_end(signal, "one", 300.05, started=100))

    def test_five_minute_limit_wins_over_simultaneous_movement_rollover(self):
        recorder, clock, writer, queued = self.make_fake_recorder(end_after=300.1, end_reason="MOVEMENT")
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        self.assertEqual(writer.write.call_count, 3000)
        self.assertEqual(queued[0][1], 300)
        self.assertIsNone(recorder.next_request)

    def test_movement_successor_is_reserved_before_enqueue_and_has_no_idle_gap(self):
        recorder, clock, _, queued = self.make_fake_recorder(end_after=120.3, end_reason="MOVEMENT")
        recorder.uploader.start, recorder.uploader.stop = Mock(), Mock()
        states = []
        original_publish = Recorder.publish.__get__(recorder, Recorder)
        def publish(status=None, **extra):
            original_publish(status, **extra)
            if status:
                states.append((status, recorder.request_id))
            if status == "IDLE":
                self.assertEqual(len([item for item in states if item[0] == "IDLE"]), 1)
                atomic_json(recorder.request_file, {"request_id": "one", "created_at": clock.now})
            if status == "STARTING" and recorder.request_id:
                self.assertFalse(telegram_send_allowed(self.root, now=clock.now))
        recorder.publish = publish
        original_capture = recorder.capture
        successor_captures = [0]
        def capture(*args):
            frame = original_capture(*args)
            if recorder.request_id == "two":
                successor_captures[0] += 1
                if successor_captures[0] == 2:
                    recorder.stop.touch()
            return frame
        recorder.capture = capture
        original_enqueue = recorder.uploader.enqueue
        def enqueue(path, request, duration, complete):
            if request["request_id"] == "one":
                state = read_json(recorder.state_file)
                self.assertEqual((state["status"], state["request_id"]), ("STARTING", "two"))
                self.assertFalse(telegram_send_allowed(self.root, now=clock.now))
            original_enqueue(path, request, duration, complete)
        recorder.uploader.enqueue = enqueue
        dependencies = {"cv2": recorder.cv2, "mss": recorder.mss, "numpy": SimpleNamespace(),
                        "psutil": SimpleNamespace(pid_exists=lambda _: False),
                        "win32gui": SimpleNamespace(), "win32process": SimpleNamespace()}
        with patch.dict(sys.modules, dependencies), \
                patch.object(recorder_module.ctypes, "windll", SimpleNamespace(shcore=SimpleNamespace(SetProcessDpiAwareness=Mock())), create=True), \
                patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            recorder.run()
        self.assertEqual([state for state in states if state[0] == "IDLE"], [("IDLE", None)])
        self.assertIn(("RECORDING", "two"), states)
        self.assertEqual(len(queued), 2)
        self.assertTrue(queued[0][2])
        self.assertFalse(queued[1][2])
        self.assertLess(queued[1][1], 1)
        quiet = read_json(recorder.quiet_file)
        self.assertEqual(quiet["request_id"], "two")
        self.assertFalse(telegram_send_allowed(self.root, now=clock.now))
        self.assertTrue(telegram_send_allowed(self.root, now=quiet["quiet_until"]))

    def test_confirmed_inactivity_completes_and_queues_a_short_event(self):
        recorder, clock, writer, queued = self.make_fake_recorder(end_after=120.3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        self.assertTrue(writer.released)
        self.assertEqual(len(queued), 1)
        self.assertTrue(queued[0][2], "Inactivity is a natural completion, not an interruption")
        self.assertGreater(queued[0][1], 0)
        self.assertGreaterEqual(queued[0][1], 120)
        self.assertLess(queued[0][1], 121)
        self.assertEqual(queued[0][1], writer.write.call_count / recorder.fps)
        self.assertLessEqual(abs(queued[0][1] - (clock.now - 100.18)), 1 / recorder.fps)
        self.assertFalse(recorder.stop.exists(), "Ending one event must keep the overall monitor alive")
        self.assertEqual(read_json(recorder.end_request_file)["request_id"], "one")

    def test_old_malformed_and_other_event_signals_do_not_end_recording(self):
        requests = [
            {"request_id": "old", "created_at": 100, "reason": "NO_MOVEMENT"},
            {"request_id": "one", "created_at": 89, "reason": "NO_MOVEMENT"},
            {"request_id": "one", "created_at": 9999, "reason": "NO_MOVEMENT"},
            {"request_id": "one", "created_at": 100, "reason": "NO_PERSON"},
            {"request_id": "one", "created_at": "bad", "reason": "NO_MOVEMENT"},
        ]
        for signal in requests:
            with self.subTest(signal=signal):
                recorder, clock, writer, queued = self.make_fake_recorder()
                atomic_json(recorder.end_request_file, signal)
                with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                        patch.object(recorder_module.time, "time", clock.monotonic), \
                        patch.object(recorder_module.time, "sleep", clock.sleep):
                    self.assertTrue(recorder.record({"request_id": "one"}))
                self.assertEqual(writer.write.call_count, 3000)
                self.assertEqual(queued[0][1], 300)

    def test_retained_end_signal_cannot_stop_the_next_event(self):
        recorder, clock, _, queued = self.make_fake_recorder(end_after=120.3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
            self.assertTrue(recorder.record({"request_id": "two"}))
        self.assertGreaterEqual(queued[0][1], 120)
        self.assertLess(queued[0][1], 121)
        self.assertEqual(queued[1][1], 300)

    def test_end_signal_replacement_is_not_deleted_during_completion(self):
        recorder, clock, writer, queued = self.make_fake_recorder(end_after=120.3)
        original_enqueue = recorder.uploader.enqueue
        newer = {"request_id": "next-event", "created_at": 103, "reason": "NO_MOVEMENT"}
        def enqueue(*args):
            atomic_json(recorder.end_request_file, newer)
            original_enqueue(*args)
        recorder.uploader.enqueue = enqueue
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        self.assertEqual(read_json(recorder.end_request_file), newer)

    def test_run_returns_to_idle_after_inactivity_without_stopping_service(self):
        recorder, clock, _, queued = self.make_fake_recorder(end_after=120.3)
        recorder.uploader.start = Mock()
        recorder.uploader.stop = Mock()
        states = []
        def publish(status=None, **extra):
            if status is None:
                return
            states.append(status)
            if status == "IDLE":
                if states.count("IDLE") == 1:
                    atomic_json(recorder.request_file, {"request_id": "one", "created_at": clock.now})
                else:
                    self.assertFalse(recorder.stop.exists())
                    self.assertIsNone(recorder.request_id)
                    recorder.stop.touch()  # End only the synthetic test harness.
        recorder.publish = publish
        dependencies = {
            "cv2": recorder.cv2, "mss": recorder.mss, "numpy": SimpleNamespace(),
            "psutil": SimpleNamespace(pid_exists=lambda _: False),
            "win32gui": SimpleNamespace(), "win32process": SimpleNamespace(),
        }
        with patch.dict(sys.modules, dependencies), \
                patch.object(recorder_module.ctypes, "windll", SimpleNamespace(shcore=SimpleNamespace(SetProcessDpiAwareness=Mock()))), \
                patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            recorder.run()
        self.assertEqual(states, ["STARTING", "IDLE", "STARTING", "RECORDING", "IDLE", "STOPPED"])
        self.assertTrue(queued[0][2])
        self.assertGreaterEqual(queued[0][1], 120)
        self.assertLess(queued[0][1], 121)
        recorder.uploader.start.assert_called_once()
        recorder.uploader.stop.assert_called_once()

    def test_inactivity_clip_is_a_playable_mp4_with_actual_short_duration(self):
        try:
            import cv2
            import numpy
        except ImportError:
            self.skipTest("Optional real-video check requires OpenCV and numpy")
        recorder, clock, _, queued = self.make_fake_recorder(real_video=True, end_after=120.3)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), \
                patch.object(recorder_module.time, "time", clock.monotonic), \
                patch.object(recorder_module.time, "sleep", clock.sleep):
            self.assertTrue(recorder.record({"request_id": "one"}))
        video = cv2.VideoCapture(str(queued[0][0]))
        try:
            self.assertTrue(video.isOpened())
            self.assertTrue(video.read()[0])
            self.assertAlmostEqual(video.get(cv2.CAP_PROP_FRAME_COUNT) / video.get(cv2.CAP_PROP_FPS), queued[0][1])
            self.assertGreater(queued[0][1], 0)
            self.assertGreaterEqual(queued[0][1], 120)
            self.assertLess(queued[0][1], 121)
            self.assertTrue(queued[0][2])
        finally:
            video.release()

    def test_actual_mp4_has_300_seconds_and_fixed_dimensions(self):
        try:
            import cv2
            import numpy
        except ImportError:
            self.skipTest("Optional real-video check requires OpenCV and numpy")
        recorder, clock, _, queued = self.make_fake_recorder(real_video=True)
        with patch.object(recorder_module.time, "monotonic", clock.monotonic), patch.object(recorder_module.time, "sleep", clock.sleep):
            recorder.record({"request_id": "one"})
        video = cv2.VideoCapture(str(queued[0][0]))
        try:
            self.assertTrue(video.isOpened())
            self.assertEqual(video.get(cv2.CAP_PROP_FRAME_COUNT), 3000)
            self.assertEqual(video.get(cv2.CAP_PROP_FPS), 10)
            self.assertEqual(video.get(cv2.CAP_PROP_FRAME_WIDTH), 120)
            self.assertEqual(video.get(cv2.CAP_PROP_FRAME_HEIGHT), 160)
            self.assertTrue(video.read()[0])
        finally:
            video.release()

    def test_rotation_excludes_active_and_durable_pending_files(self):
        queue = telegram_video.TelegramVideoQueue(self.root, Mock())
        directory = self.root / "recordings"
        directory.mkdir(exist_ok=True)
        paths = [directory / ("SuperLive_" + name + ".mp4") for name in ("pending", "old", "active")]
        for path in paths:
            path.write_bytes(b"video")
        queue.enqueue(paths[0], {"request_id": "one"}, 300, True)
        prepared = paths[0].with_name(paths[0].stem + "_telegram.mp4")
        encoding = prepared.with_name(prepared.stem + ".encoding.mp4")
        prepared.write_bytes(b"video")
        encoding.write_bytes(b"video")
        protected = queue.protected_paths() | {paths[2]}
        self.assertEqual(rotation_candidates(directory, protected), [paths[1]])


class UploadQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name)
        self.log = Mock()
        self.queue = telegram_video.TelegramVideoQueue(self.root, self.log)
        self.path = self.root / "SuperLive_clip.mp4"
        self.path.write_bytes(b"video")
        self.manifest = self.queue.enqueue(self.path, {"request_id": "one", "count": 2}, 300, True)
    def tearDown(self):
        self.temporary.cleanup()

    def test_failed_upload_remains_retryable_without_logging_token(self):
        self.queue.prepare = Mock(return_value=self.path)
        self.queue.send = Mock(side_effect=RuntimeError("https://api.telegram.org/botSECRET_TOKEN/sendVideo"))
        with patch.object(telegram_video, "load_credentials", return_value=("SECRET_TOKEN", "fake-chat")):
            self.queue.process_once(now=100)
        job = read_json(self.manifest)
        self.assertEqual(job["attempts"], 1)
        self.assertGreater(job["retry_at"], 100)
        self.assertIn(self.path.resolve(), self.queue.protected_paths())
        self.assertNotIn("SECRET_TOKEN", str(self.log.call_args_list))
        self.queue.process_once(now=100)
        self.assertEqual(self.queue.send.call_count, 1)

    def test_restart_loads_pending_job_and_success_removes_manifest(self):
        restarted = telegram_video.TelegramVideoQueue(self.root, self.log)
        restarted.prepare = Mock(return_value=self.path)
        restarted.send = Mock()
        with patch.object(telegram_video, "load_credentials", return_value=("fake-token", "fake-chat")):
            restarted.process_once(now=100)
        restarted.send.assert_called_once()
        self.assertFalse(self.manifest.exists())
        self.assertTrue(self.path.exists())
        self.assertFalse(restarted.protected_paths())

    def test_missing_telegram_config_retains_local_clip_and_job(self):
        self.queue.prepare = Mock()
        self.queue.send = Mock()
        with patch.object(telegram_video, "load_credentials", return_value=("", "")):
            self.queue.process_once(now=100)
        self.queue.send.assert_not_called()
        self.queue.prepare.assert_not_called()
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.path.exists())

    def test_stop_terminates_and_reaps_owned_encoder_child(self):
        started = threading.Event()
        processes = []
        errors = []
        popen = subprocess.Popen
        def sleeping_encoder(*args, **kwargs):
            process = popen([sys.executable, "-c", "import time; time.sleep(300)"], **kwargs)
            processes.append(process)
            started.set()
            return process
        def prepare():
            try:
                self.queue.prepare(read_json(self.manifest))
            except InterruptedError:
                errors.append("cancelled")
        fake_ffmpeg = SimpleNamespace(get_ffmpeg_exe=lambda: "unused-test-encoder")
        with patch.dict(sys.modules, imageio_ffmpeg=fake_ffmpeg), patch.object(telegram_video.subprocess, "Popen", sleeping_encoder):
            self.queue.thread = threading.Thread(target=prepare)
            self.queue.thread.start()
            self.assertTrue(started.wait(5))
            self.queue.stop()
        self.assertFalse(self.queue.thread.is_alive())
        self.assertIsNotNone(processes[0].poll())
        self.assertEqual(errors, ["cancelled"])
        self.assertTrue(self.manifest.exists())


if __name__ == "__main__":
    unittest.main()
