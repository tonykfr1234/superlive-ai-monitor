"""Capture integration and one-time selection migration; no live camera access."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_presence_monitor import monitor
from record_protocol import read_json
from recorder import Recorder


class ContentCaptureIntegrationTests(unittest.TestCase):
    def test_existing_selection_migrates_once_and_survives_reload(self):
        info = SimpleNamespace(parent_client_rect=(0, 0, 1000, 800), content_rect=(0, 80, 1000, 800))
        cfg = dict(monitor.DEFAULT_POLICY, roi=[.2, .2, .8, .8])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder, patch.object(monitor, 'POLICY_FILE', Path(folder)/'policy.json'):
            self.assertTrue(monitor.ensure_content_roi(cfg, info))
            migrated = list(cfg['roi'])
            self.assertAlmostEqual(migrated[1], 80/720)
            reloaded = read_json(monitor.POLICY_FILE)
            self.assertTrue(monitor.ensure_content_roi(reloaded, info))
            self.assertEqual(reloaded['roi'], migrated)

    def test_toolbar_only_selection_stays_blocked_until_user_saves(self):
        info = SimpleNamespace(parent_client_rect=(0, 0, 1000, 800), content_rect=(0, 80, 1000, 800))
        cfg = dict(monitor.DEFAULT_POLICY, roi=[.2, .01, .8, .05])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder, patch.object(monitor, 'POLICY_FILE', Path(folder)/'policy.json'):
            self.assertFalse(monitor.ensure_content_roi(cfg, info))
            self.assertTrue(cfg['roi_reselect_required'])
            reloaded = read_json(monitor.POLICY_FILE)
            self.assertFalse(monitor.ensure_content_roi(reloaded, info))
            self.assertIsNotNone(reloaded['roi'])
            monitor.save_roi(reloaded, [.1, .1, .9, .9])
            self.assertNotIn('roi_reselect_required', reloaded)
            self.assertTrue(monitor.ensure_content_roi(reloaded, info))

    def test_recorder_returns_the_shared_cropped_frame(self):
        # No Recorder constructor: it creates runtime folders and an upload queue.
        instance = object.__new__(Recorder)
        for name in ('win32gui', 'cv2', 'np', 'capture_resolver'):
            setattr(instance, name, object())
        expected = object()
        sct = object()
        with patch('recorder.capture_frame', return_value=(expected, object())) as crop:
            self.assertIs(instance.capture(sct, 42), expected)
            crop.assert_called_once_with(sct, 42, instance.win32gui, instance.cv2, instance.np,
                                         resolver=instance.capture_resolver)


if __name__ == '__main__':
    unittest.main()
