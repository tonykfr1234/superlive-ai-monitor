"""Keyboard-only preview operations using synthetic frames; no desktop capture."""
import sys
import unittest
from itertools import permutations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from preview_ui import PreviewController, PreviewLayout, render_preview


class PreviewKeyboardTests(unittest.TestCase):
    def controller(self, dashboard=True, order=None):
        c = PreviewController()
        c.plate_enabled = True
        c.layout = PreviewLayout.create(960, 540, 800, 760, show_plates=True,
                                        dashboard=dashboard, panel_order=order)
        return c

    def focus(self, c, name):
        for _ in range(len(c.layout.buttons)):
            c.key(9)
            if c.focused_button == name:
                return
        self.fail(f'Cannot reach {name} using Tab')

    def test_tab_order_follows_visual_order_after_every_card_rearrangement(self):
        for order in permutations(('camera', 'plates', 'latest', 'controls')):
            c = self.controller(order=order)
            expected = [name for name, rect in sorted(c.layout.buttons.items(),
                        key=lambda item: (item[1].y, item[1].x))
                        if name not in ('confirm', 'cancel')]
            visited = []
            for _ in expected:
                c.key(9)
                visited.append(c.focused_button)
            self.assertEqual(visited, expected)
            c.key(9)
            self.assertEqual(c.focused_button, expected[0])
            c.key('backtab')
            self.assertEqual(c.focused_button, expected[-1])

    def test_settings_and_review_are_reachable_without_mouse(self):
        for name in ('settings', 'alert_settings', 'review_plate'):
            for key in (13, 32):
                c = self.controller()
                self.focus(c, name)
                self.assertEqual(c.key(key).kind, name)
                self.assertIsNone(c.focused_button)

    def test_disabled_actions_are_skipped_and_cannot_activate_stale_focus(self):
        c = self.controller()
        c.plate_enabled = False
        for _ in range(12):
            c.key(9)
            self.assertNotIn(c.focused_button, ('plates', 'confirm', 'cancel'))
        c.plate_enabled = True
        self.focus(c, 'plates')
        c.plate_enabled = False
        self.assertIsNone(c.key(13))
        self.assertFalse(c.plate_other_expanded)

    def test_select_then_enter_still_saves_roi_and_pointer_clears_focus(self):
        for dashboard in (False, True):
            c = self.controller(dashboard=dashboard)
            self.focus(c, 'select')
            self.assertEqual(c.key(13).kind, 'selection_started')
            c.draft = [[.1, .1], [.8, .1], [.5, .8]]
            self.assertEqual(c.key(13).kind, 'save')
            self.focus(c, 'stop')
            c.key(ord('r'))
            self.assertIsNone(c.focused_button)
            c.draft = [[.1, .1], [.8, .1], [.5, .8]]
            self.assertEqual(c.key(13).kind, 'save')
            self.focus(c, 'stop')
            c.mouse('down', 0, 0)
            self.assertIsNone(c.focused_button)

    def test_focus_ring_is_visible_inside_button_without_changing_source(self):
        frame = np.full((540, 960, 3), 80, np.uint8)
        for dashboard in (False, True):
            c = self.controller(dashboard=dashboard)
            before = render_preview(frame, c, 'IDLE')
            self.focus(c, 'settings' if dashboard else 'stop')
            after = render_preview(frame, c, 'IDLE')
            rect = c.layout.buttons[c.focused_button]
            x, y = rect.x+rect.width//2, rect.y
            self.assertTupleEqual(tuple(after[y, x]), (119, 208, 255))
            self.assertFalse(np.array_equal(before, after))
            self.assertTrue(np.all(frame == 80))


if __name__ == '__main__':
    unittest.main()
