import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from telegram_health import TelegramSilenceReminder, record_success, send_reminder
from record_protocol import atomic_json

class SilenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10000
        self.sender = Mock()
        self.make()

    def make(self):
        self.worker = TelegramSilenceReminder(self.root, clock=lambda: self.now,
                                             sender=self.sender, log=lambda _: None)

    def test_one_hour_boundary_and_hourly_repeat(self):
        self.now += 3599
        self.assertFalse(self.worker.tick())
        self.now += 1
        self.assertTrue(self.worker.tick())
        self.assertFalse(self.worker.tick())
        self.now += 3600
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.sender.call_count, 2)

    def test_each_successful_notification_resets_clock(self):
        for kind in ('plate', 'video', 'report'):
            self.now += 3500
            record_success(self.root, kind, self.now)
            self.now += 200
            self.assertFalse(self.worker.tick())
        self.sender.assert_not_called()

    def test_restart_preserves_silence_clock(self):
        self.now += 3590
        self.make()
        self.now += 10
        self.assertTrue(self.worker.tick())
        self.make()
        self.assertFalse(self.worker.tick())

    def test_failed_send_retries_in_five_minutes_without_success_receipt(self):
        self.now += 3600
        self.sender.side_effect = RuntimeError('network')
        self.assertFalse(self.worker.tick())
        self.assertFalse((self.root / 'runtime/telegram_activity_reminder.json').exists())
        self.now += 299
        self.assertFalse(self.worker.tick())
        self.assertEqual(self.sender.call_count, 1)
        self.now += 1
        self.sender.side_effect = None
        self.assertTrue(self.worker.tick())

    def test_disabled_or_stopped_sends_nothing(self):
        self.now += 4000
        atomic_json(self.root / 'config/telegram_idle_alert.json', {'enabled': False})
        self.assertFalse(self.worker.tick())
        atomic_json(self.root / 'config/telegram_idle_alert.json', {'enabled': True})
        (self.root / 'runtime/stop.flag').write_text('STOP')
        self.assertFalse(self.worker.tick())
        self.sender.assert_not_called()

    def test_api_error_is_not_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': False}
        with patch('telegram_video.load_credentials', return_value=('test', 'test')), patch('requests.post', return_value=response):
            with self.assertRaises(RuntimeError):
                send_reminder(self.root)

    def test_quiet_period_does_not_suppress_health_reminder(self):
        atomic_json(self.root / 'runtime/telegram_quiet.json', {'quiet_until': self.now + 10000})
        self.now += 3600
        self.assertTrue(self.worker.tick())
