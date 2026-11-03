"""Synthetic geometry and Win32 boundary tests; no real windows are moved."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from window_layout import apply_default_window_layout, right_column_layout


class WindowLayoutTests(unittest.TestCase):
    def assert_right_column(self, work):
        emulator, monitor = right_column_layout(work)
        self.assertEqual(emulator[0], monitor[0])
        self.assertEqual(emulator[2], monitor[2])
        self.assertGreaterEqual(emulator[1], work[1])
        self.assertLessEqual(emulator[1] + emulator[3], monitor[1])
        self.assertLessEqual(monitor[0] + monitor[2], work[2])
        self.assertLessEqual(monitor[1] + monitor[3], work[3])
        self.assertGreater(emulator[3], 0)
        self.assertGreater(monitor[3], 0)
        return emulator, monitor

    def test_common_resolutions_align_right_without_overlap(self):
        for work in ((0, 0, 1920, 1040), (0, 0, 2558, 1153), (0, 0, 1366, 728)):
            with self.subTest(work=work):
                emulator, monitor = self.assert_right_column(work)
                self.assertEqual(emulator[2], monitor[2])
                self.assertGreater(monitor[1], emulator[1] + emulator[3])

    def test_secondary_monitor_offsets_are_respected(self):
        emulator, monitor = self.assert_right_column((-2560, 0, 0, 1400))
        self.assertGreaterEqual(emulator[0], -2560)
        self.assertLessEqual(monitor[0] + monitor[2], 0)

    def test_apply_moves_only_the_monitor_never_the_emulator(self):
        calls = []
        gui = SimpleNamespace(
            IsWindow=lambda hwnd: hwnd in (11, 22),
            IsIconic=lambda hwnd: True,  # even a minimized emulator is left alone
            ShowWindow=Mock(),
            SetWindowPos=lambda *args: calls.append(args),
        )
        api = SimpleNamespace(
            MonitorFromWindow=lambda hwnd, flag: 7,
            GetMonitorInfo=lambda handle: {"Work": (0, 0, 2558, 1153)},
        )
        self.assertTrue(apply_default_window_layout(11, 22, gui=gui, api=api))
        self.assertEqual([call[0] for call in calls], [22])
        self.assertEqual(calls[0][2:6], right_column_layout((0, 0, 2558, 1153))[1])
        gui.ShowWindow.assert_called_once_with(22, 9)

    def test_invalid_or_tiny_work_area_fails_closed(self):
        gui = SimpleNamespace(IsWindow=lambda hwnd: True)
        api = SimpleNamespace(
            MonitorFromWindow=lambda hwnd, flag: 7,
            GetMonitorInfo=lambda handle: {"Work": (0, 0, 200, 200)},
        )
        self.assertFalse(apply_default_window_layout(11, 22, gui=gui, api=api))


class UserSizedCaptureTests(unittest.TestCase):
    def test_capture_follows_any_user_chosen_memu_size(self):
        from capture_region import capture_frame
        import numpy as np
        for width, height in ((784, 449), (1200, 700), (520, 330)):
            with self.subTest(size=(width, height)):
                gui = SimpleNamespace(
                    GetClientRect=lambda hwnd: (0, 0, width - 44, height - 33),
                    ClientToScreen=lambda hwnd, point: (100 + point[0] + 1, 50 + point[1] + 32),
                    EnumChildWindows=lambda hwnd, callback, _: callback(99, None),
                    IsWindowVisible=lambda hwnd: True,
                    GetWindowText=lambda hwnd: "RenderWindowWindow")
                grabbed = []
                sct = SimpleNamespace(grab=lambda box: grabbed.append(box) or np.zeros(
                    (box["height"], box["width"], 4), np.uint8))
                cv2 = SimpleNamespace(COLOR_BGRA2BGR=0, cvtColor=lambda image, code: image[:, :, :3])
                frame, info = capture_frame(sct, 11, gui, cv2, np)
                self.assertEqual(frame.shape[:2], (height - 33, width - 44))
                self.assertEqual(info.method, "memu_render_child")


if __name__ == "__main__":
    unittest.main()
