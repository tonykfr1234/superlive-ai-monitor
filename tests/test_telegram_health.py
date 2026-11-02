import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_protocol import atomic_json
from telegram_health import (
    INTERVAL,
    MESSAGE,
    TAIPEI,
    TelegramSilenceReminder,
    activity_slot,
    recently_observed,
    record_success,
    send_reminder,
)


class ActivityReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sender = Mock()
        self.set_time(2)
        self.worker = self.make()

    def set_time(self, hour, minute=0, second=0, day=2):
        self.now = datetime(
            2026, 11, day, hour, minute, second, tzinfo=TAIPEI
        ).timestamp()

    def make(self):
        return TelegramSilenceReminder(
            self.root,
            clock=lambda: self.now,
            sender=self.sender,
            log=lambda _: None,
        )

    def test_daytime_slots_and_exact_quiet_boundaries(self):
        self.set_time(5, 59, 59)
        self.assertIsNone(activity_slot(self.now))
        self.assertFalse(self.worker.tick())
        expected = {
            6: 6, 7: 6, 8: 8, 9: 8, 10: 10, 11: 10, 12: 12, 13: 12,
            14: 14, 15: 14, 16: 16, 17: 16, 18: 18, 19: 18,
            20: 20, 21: 20, 22: 22,
        }
        for hour, slot_hour in expected.items():
            self.set_time(hour)
            slot = datetime(2026, 11, 2, slot_hour, tzinfo=TAIPEI).timestamp()
            self.assertEqual(activity_slot(self.now), slot)
        self.set_time(23)
        self.assertIsNone(activity_slot(self.now))
        self.assertFalse(self.worker.tick())
        self.sender.assert_not_called()

    def test_each_daytime_slot_alerts_at_most_once(self):
        self.set_time(6)
        self.assertTrue(self.worker.tick())
        self.set_time(7, 59, 59)
        self.assertFalse(self.worker.tick())
        self.set_time(8)
        self.assertTrue(self.worker.tick())
        self.set_time(22)
        self.assertTrue(self.worker.tick())
        self.set_time(23)
        self.assertFalse(self.worker.tick())
        self.set_time(6, 0, 0, 3)
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.sender.call_count, 4)

    def test_activity_in_the_last_two_hours_suppresses_alert(self):
        self.set_time(7)
        atomic_json(
            self.root / "runtime/person_vehicle_activity.json",
            {"last_seen_at": self.now - 60},
        )
        self.set_time(8)
        self.assertFalse(self.worker.tick())
        self.sender.assert_not_called()
        self.set_time(10)
        self.assertTrue(self.worker.tick())

    def test_exactly_two_hours_old_is_not_recent(self):
        self.set_time(8)
        atomic_json(
            self.root / "runtime/person_vehicle_activity.json",
            {"last_seen_at": self.now - INTERVAL},
        )
        self.assertFalse(recently_observed(self.root, self.now))
        self.assertTrue(self.worker.tick())

    def test_missing_malformed_or_future_activity_fails_closed_to_alert(self):
        for value in (None, "bad", self.now + 1):
            with self.subTest(value=value):
                path = self.root / "runtime/person_vehicle_activity.json"
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_json(path, {"last_seen_at": value})
                self.assertFalse(recently_observed(self.root, self.now))

    def test_other_telegram_success_does_not_reset_activity_check(self):
        self.set_time(6)
        for kind in ("plate", "video", "report"):
            record_success(self.root, kind, self.now)
        self.assertTrue(self.worker.tick())

    def test_restart_preserves_checked_slot(self):
        self.set_time(6)
        self.assertTrue(self.worker.tick())
        self.worker = self.make()
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.sender.call_count, 1)

    def test_failed_send_retries_after_five_minutes_not_during_night(self):
        self.set_time(22, 58)
        self.sender.side_effect = RuntimeError("network")
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.sender.call_count, 1)
        self.set_time(23, 5)
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.sender.call_count, 1)
        self.set_time(6, 0, 0, 3)
        self.sender.side_effect = None
        self.assertTrue(self.worker.tick())

    def test_disabled_or_stopped_worker_sends_nothing(self):
        self.set_time(6)
        atomic_json(self.root / "config/telegram_idle_alert.json", {"enabled": False})
        self.assertFalse(self.worker.tick())
        atomic_json(self.root / "config/telegram_idle_alert.json", {"enabled": True})
        (self.root / "runtime/stop.flag").write_text("STOP", encoding="ascii")
        self.assertFalse(self.worker.tick())
        self.sender.assert_not_called()

    def test_old_state_is_migrated_without_losing_start_time(self):
        path = self.root / "runtime/telegram_silence.json"
        atomic_json(
            path,
            {
                "started_at": self.now - INTERVAL - 1,
                "last_alert_at": self.now,
                "retry_at": 0,
            },
        )
        self.worker = self.make()
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["policy"], "daytime_activity_v2")
        self.assertEqual(saved["started_at"], self.now - INTERVAL - 1)
        self.assertNotIn("last_alert_at", saved)

    def test_first_check_requires_two_hours_of_monitoring(self):
        self.set_time(6)
        atomic_json(
            self.root / "runtime/telegram_silence.json",
            {"started_at": self.now},
        )
        worker = self.make()
        self.assertFalse(worker.tick())
        self.set_time(8)
        self.assertTrue(worker.tick())

    def test_message_explicitly_asks_user_to_check_monitor(self):
        self.assertIn("請查看監視器", MESSAGE)

    def test_api_failure_is_not_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": False}
        requests = Mock()
        requests.post.return_value = response
        with patch.dict(sys.modules, {"requests": requests}), patch(
            "telegram_video.load_credentials", return_value=("test", "test")
        ):
            with self.assertRaises(RuntimeError):
                send_reminder(self.root)


if __name__ == "__main__":
    unittest.main()
