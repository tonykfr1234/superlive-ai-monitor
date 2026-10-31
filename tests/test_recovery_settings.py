from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from setup_wizard import recovery_settings, save_settings
from record_protocol import atomic_json, read_json


class RecoverySettingsTests(unittest.TestCase):
    def test_exact_target_and_timing_survive_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = recovery_settings({'frame_roi': [.1, .1, .9, .9]}, True, ' Demo recorder ', '4', '60', '30')
            save_settings(folder, {'source': 'adb', 'recovery': cfg}, '', '', '', '', '')
            saved = read_json(Path(folder)/'config/capture_config.json')['recovery']
            self.assertEqual(saved, dict(enabled=True, device_name='Demo recorder', channel=4,
                                        stale_seconds=60, retry_seconds=30, frame_roi=[.1,.1,.9,.9]))

    def test_invalid_target_never_overwrites_existing_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config/capture_config.json'
            atomic_json(path, {'original': True})
            for update in ({'device_name': ''}, {'channel': 0}, {'channel': True}, {'channel': '2.5'},
                           {'stale_seconds': 0}, {'retry_seconds': 1}, {'frame_roi': [0,0,0,0]}):
                bad=dict(enabled=True, device_name='Demo', channel=4, stale_seconds=60, retry_seconds=30)
                bad.update(update)
                with self.assertRaises(ValueError):
                    save_settings(folder, {'recovery': bad}, '', '', '', '', '')
                self.assertEqual(read_json(path), {'original': True})

    def test_disabling_preserves_target_for_later_reenable(self):
        old=dict(enabled=True, device_name='Demo', channel=4, stale_seconds=60, retry_seconds=30)
        self.assertEqual(recovery_settings(old, False, '', '', '', ''), dict(old, enabled=False))
        self.assertTrue(old['enabled'])
