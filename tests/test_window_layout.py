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

    def test_apply_moves_only_the_two_given_windows(self):
        calls = []
        gui = SimpleNamespace(
            IsWindow=lambda hwnd: hwnd in (11, 22),
            IsIconic=lambda hwnd: False,
            ShowWindow=Mock(),
            SetWindowPos=lambda *args: calls.append(args),
        )
        api = SimpleNamespace(
            MonitorFromWindow=lambda hwnd, flag: 7,
            GetMonitorInfo=lambda handle: {"Work": (0, 0, 2558, 1153)},
        )
        self.assertTrue(apply_default_window_layout(11, 22, gui=gui, api=api))
        self.assertEqual([call[0] for call in calls], [11, 22])
        gui.ShowWindow.assert_not_called()

    def test_invalid_or_tiny_work_area_fails_closed(self):
        gui = SimpleNamespace(IsWindow=lambda hwnd: True)
        api = SimpleNamespace(
            MonitorFromWindow=lambda hwnd, flag: 7,
            GetMonitorInfo=lambda handle: {"Work": (0, 0, 200, 200)},
        )
        self.assertFalse(apply_default_window_layout(11, 22, gui=gui, api=api))


class MemuRenderFitTests(unittest.TestCase):
    def test_memu_render_area_becomes_16_by_9(self):
        placed = {11: (1767, 7, 784, 449)}  # MEmu's current, user-sized window
        def set_pos(hwnd, _, x, y, width, height, flags):
            placed[hwnd] = (x, y, width, height)
        def window_rect(hwnd):
            x, y, width, height = placed[hwnd]
            return x, y, x + width, y + height
        def client(hwnd):
            # MEmu: 33 px title bar and a 44 px side toolbar around the render widget.
            x, y, width, height = placed[11]
            return 0, 0, width - 44, height - 33
        gui = SimpleNamespace(
            IsWindow=lambda hwnd: True, IsIconic=lambda hwnd: False, ShowWindow=Mock(),
            SetWindowPos=set_pos, GetWindowRect=window_rect,
            EnumChildWindows=lambda hwnd, callback, _: callback(99, None) if hwnd == 11 else None,
            IsWindowVisible=lambda hwnd: True, GetWindowText=lambda hwnd: "RenderWindowWindow",
            ClientToScreen=lambda hwnd, point: (placed[11][0] + 1, placed[11][1] + 32),
            GetClientRect=client)
        api = SimpleNamespace(MonitorFromWindow=lambda hwnd, flag: 7,
                              GetMonitorInfo=lambda handle: {"Work": (0, 0, 2558, 1153)})
        planned = right_column_layout((0, 0, 2558, 1153))[0]
        self.assertTrue(apply_default_window_layout(11, 22, gui=gui, api=api))
        x, y, width, height = placed[11]
        self.assertAlmostEqual((width - 44) / (height - 33), 16 / 9, delta=0.01)
        self.assertEqual(x + width, planned[0] + planned[2])
        self.assertLessEqual(height, planned[3])

    def test_unmeasurable_emulator_keeps_planned_rectangle(self):
        from window_layout import fit_render_aspect
        gui = SimpleNamespace(EnumChildWindows=Mock(side_effect=OSError))
        self.assertIsNone(fit_render_aspect(11, (0, 0, 800, 500), gui))


if __name__ == "__main__":
    unittest.main()
