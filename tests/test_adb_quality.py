"""Quality confirmation tolerates Android popup transitions without blind taps."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import adb_capture


def screen(*nodes, package="com.tvt.superliveplus"):
    root = ET.Element("hierarchy")
    for attrs in nodes:
        ET.SubElement(root, "node", dict(package=package, **attrs))
    return root


BUTTON = {"resource-id": "com.tvt.superliveplus:id/iv_bottom_definition_land", "bounds": "[0,0][20,20]"}
LIVE = {"resource-id": "com.tvt.superliveplus:id/live_video", "bounds": "[0,0][20,20]"}
HIGH = {"text": "最高", "bounds": "[0,0][10,10]"}
LOW = {"text": "低", "bounds": "[0,10][10,20]"}
WHITE = np.full((20, 20, 3), 255, np.uint8)
CYAN = np.full((20, 20, 3), (220, 190, 30), np.uint8)


class QualityTransitionTests(unittest.TestCase):
    def setUp(self):
        self.client = adb_capture.AdbClient({"adb_path": "mock-adb"})
        self.client.tap = Mock()
        self.client.run = Mock()
        self.client.quality_screenshot = Mock(return_value=CYAN)
        sleeper = patch.object(adb_capture.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_waits_for_toolbar_and_popup_animation_without_repeated_taps(self):
        self.client.ui = Mock(side_effect=[screen(LIVE), screen(LIVE), screen(BUTTON),
            screen(BUTTON), screen(HIGH, LOW), screen(HIGH, LOW), screen(BUTTON)])
        self.assertTrue(self.client.highest_quality())
        self.assertEqual(self.client.tap.call_count, 2)
        self.client.run.assert_called_once_with("shell", "input", "keyevent", "4")

    def test_selection_that_auto_closes_reopens_once_for_confirmation(self):
        self.client.quality_screenshot.side_effect = [WHITE, CYAN]
        self.client.ui = Mock(side_effect=[screen(HIGH, LOW), screen(BUTTON),
            screen(HIGH, LOW), screen(HIGH, LOW), screen(BUTTON)])
        self.assertTrue(self.client.highest_quality())
        self.assertEqual(self.client.tap.call_count, 2)
        self.assertEqual(self.client.tap.call_args_list[0].args[0].get("text"), "最高")
        self.assertEqual(self.client.tap.call_args_list[1].args[0].get("resource-id"), BUTTON["resource-id"])

    def test_waits_for_delayed_selected_color_in_open_popup(self):
        self.client.quality_screenshot.side_effect = [WHITE, WHITE, CYAN]
        menu = screen(HIGH, LOW)
        self.client.ui = Mock(side_effect=[menu, menu, menu, menu, screen(BUTTON)])
        self.assertTrue(self.client.highest_quality())
        self.client.tap.assert_called_once()

    def test_second_back_only_after_popup_is_still_observed(self):
        menu = screen(HIGH, LOW)
        self.client.ui = Mock(side_effect=[menu, menu, menu, menu, screen(BUTTON)])
        self.assertTrue(self.client.highest_quality())
        self.assertEqual(self.client.run.call_count, 2)
        self.client.tap.assert_not_called()

    def test_failed_cleanup_snapshot_does_not_turn_verification_into_success(self):
        self.client.ui = Mock(side_effect=[screen(HIGH, LOW), RuntimeError("ADB offline")])
        self.assertFalse(self.client.highest_quality())
        self.client.run.assert_not_called()

    def test_app_switch_during_menu_animation_never_receives_back_or_row_tap(self):
        other = screen(HIGH, LOW, package="example.unrelated")
        self.client.ui = Mock(side_effect=[screen(BUTTON), other, other])
        with self.assertRaisesRegex(RuntimeError, "Highest quality option"):
            self.client.highest_quality()
        self.assertEqual(self.client.tap.call_count, 1)
        self.client.run.assert_not_called()


class QualityFrameTests(unittest.TestCase):
    def test_confirmation_uses_video_compositor_when_screencap_omits_popup(self):
        import cv2
        client = adb_capture.AdbClient({})
        client.run = Mock(return_value=b'fixture-h264-stream')
        client.screenshot = Mock(return_value=WHITE)
        ok, encoded = cv2.imencode('.png', CYAN)
        self.assertTrue(ok)
        with patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='mock-ffmpeg'), \
             patch.object(adb_capture.subprocess, 'run', return_value=Mock(returncode=0, stdout=encoded.tobytes())) as decode:
            frame = client.quality_screenshot()
        self.assertTrue(adb_capture.selected_cyan_text(frame, [0, 0, 10, 10]))
        client.screenshot.assert_not_called()
        self.assertIn('screenrecord', client.run.call_args.args)
        self.assertEqual(decode.call_args.kwargs['input'], b'fixture-h264-stream')

    def test_undecodable_quality_video_does_not_confirm(self):
        client = adb_capture.AdbClient({})
        client.run = Mock(return_value=b'fixture-h264-stream')
        for result in (Mock(returncode=1, stdout=b''), Mock(returncode=0, stdout=b'invalid png')):
            with patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='mock-ffmpeg'), \
                 patch.object(adb_capture.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'cannot be decoded'):
                    client.quality_screenshot()


if __name__ == "__main__":
    unittest.main()
