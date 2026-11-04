"""Recorder bridges a short ADB quality-switch frame gap."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from recorder import Recorder


class RecorderInitialCaptureTests(unittest.TestCase):
    def test_retries_transient_stale_frame_before_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder.__new__(Recorder)
            recorder.stop = Path(temporary) / "stop.flag"
            recorder.adb_reader = object()
            recorder.log = Mock()
            frame = object()
            recorder.capture = Mock(side_effect=[RuntimeError("ADB frame is stale"), frame])
            actual, sct, hwnd = recorder.capture_first_frame(
                object(), 0, max_failures=5, retry_seconds=0.001)
            self.assertIs(actual, frame)
            self.assertIsNotNone(sct)
            self.assertEqual(recorder.capture.call_count, 2)
            self.assertIn("EVENT_CAPTURE_RETRY=1", recorder.log.call_args_list[0].args[0])

    def test_stop_prevents_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Recorder.__new__(Recorder)
            recorder.stop = Path(temporary) / "stop.flag"
            recorder.stop.write_text("STOP", encoding="ascii")
            recorder.adb_reader = object()
            recorder.log = Mock()
            recorder.capture = Mock(side_effect=RuntimeError("ADB frame is stale"))
            with self.assertRaises(RuntimeError):
                recorder.capture_first_frame(
                    object(), 0, max_failures=5, retry_seconds=0.001)
            self.assertEqual(recorder.capture.call_count, 1)


if __name__ == "__main__":
    unittest.main()
