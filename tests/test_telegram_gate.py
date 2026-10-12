"""Persistent Telegram quiet-period policy; no network or live runtime access."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_protocol import atomic_json, telegram_send_allowed, MIN_RECORD_SECONDS, MAX_RECORD_SECONDS


class TelegramGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()

    def write(self, name, payload):
        atomic_json(self.runtime / name, payload)

    def test_standalone_queue_allowed_and_constants_match_clip_contract(self):
        self.assertTrue(telegram_send_allowed(self.root, now=100))
        self.assertEqual(MIN_RECORD_SECONDS, 120)
        self.assertEqual(MAX_RECORD_SECONDS, 300)

    def test_pending_file_blocks_even_if_empty_or_malformed(self):
        (self.runtime / "record_request.json").write_text("broken", encoding="utf-8")
        self.assertFalse(telegram_send_allowed(self.root, now=1000))

    def test_quiet_deadline_survives_stop_failure_and_missing_state(self):
        self.write("telegram_quiet.json", {"request_id": "one", "quiet_until": 220})
        for status in (None, "STOPPED", "ERROR", "PAUSED", "IDLE"):
            if status:
                self.write("recording_state.json", {"status": status})
            with self.subTest(status=status):
                self.assertFalse(telegram_send_allowed(self.root, now=219.999))
                self.assertTrue(telegram_send_allowed(self.root, now=220))

    def test_starting_always_blocks_and_recording_requires_elapsed_minimum(self):
        for request_id in (None, "one"):
            self.write("recording_state.json", {"status": "STARTING", "request_id": request_id})
            self.assertFalse(telegram_send_allowed(self.root, now=1000))
        for elapsed, allowed in ((0, False), (119.999, False), (120, True), (299, True), (300, True)):
            self.write("recording_state.json", {"status": "RECORDING", "request_id": "one", "elapsed_seconds": elapsed})
            with self.subTest(elapsed=elapsed):
                self.assertEqual(telegram_send_allowed(self.root, now=1000), allowed)

    def test_malformed_existing_guards_fail_closed(self):
        for name, values in (
            ("telegram_quiet.json", [None, {}, [], {"quiet_until": 0},
                                    {"request_id": "one", "quiet_until": "bad"},
                                    {"request_id": "one", "quiet_until": True}]),
            ("recording_state.json", [None, {}, [], {"status": "UNKNOWN"},
                                     {"status": "RECORDING", "request_id": "one"},
                                     {"status": "RECORDING", "request_id": "one", "elapsed_seconds": "bad"},
                                     {"status": "RECORDING", "request_id": "one", "elapsed_seconds": True}]),
        ):
            for value in values:
                self.write(name, value)
                with self.subTest(name=name, value=value):
                    self.assertFalse(telegram_send_allowed(self.root, now=1000))
            (self.runtime / name).write_text("{broken", encoding="utf-8")
            self.assertFalse(telegram_send_allowed(self.root, now=1000))
            (self.runtime / name).unlink()

    def test_default_wall_clock_honors_persisted_deadline(self):
        self.write("telegram_quiet.json", {"request_id": "one", "quiet_until": 220})
        with patch("record_protocol.time.time", return_value=219):
            self.assertFalse(telegram_send_allowed(self.root))
        with patch("record_protocol.time.time", return_value=220):
            self.assertTrue(telegram_send_allowed(self.root))


if __name__ == "__main__":
    unittest.main()
