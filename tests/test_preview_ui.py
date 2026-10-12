"""Offline UI geometry and interaction tests; no camera or Telegram access."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from preview_ui import PreviewController, PreviewLayout, render_preview, status_lines


class PreviewLayoutTests(unittest.TestCase):
    def test_all_information_and_buttons_fit_one_bottom_footer(self):
        for width, height in ((960, 720), (1440, 900), (640, 480), (320, 300)):
            with self.subTest(size=(width, height)):
                layout = PreviewLayout.create(1920, 1080, width, height)
                self.assertEqual(layout.footer.y + layout.footer.height, height)
                self.assertLessEqual(layout.video.y + layout.video.height, layout.footer.y)
                self.assertAlmostEqual(layout.video.width/layout.video.height, 1920/1080, delta=0.025)
                for rect in layout.buttons.values():
                    self.assertGreaterEqual(rect.y, layout.footer.y)
                    self.assertLessEqual(rect.y+rect.height, height)
                    self.assertGreaterEqual(rect.x, 0)
                    self.assertLessEqual(rect.x+rect.width, width)
                if width >= 640:
                    self.assertEqual(len({rect.y for rect in layout.buttons.values()}), 1)
                    self.assertLessEqual(layout.footer.height, 160)

    def test_letterbox_offsets_and_display_scale_map_to_normalized_camera_roi(self):
        for source in ((1920, 1080), (720, 1280), (800, 800)):
            layout = PreviewLayout.create(*source, 1000, 700)
            x = layout.video.x + layout.video.width * 0.25
            y = layout.video.y + layout.video.height * 0.75
            self.assertEqual(layout.image_point(x, y), (0.25, 0.75))
            self.assertEqual(layout.image_point(-100, 9000, clamp=True), (0.0, 1.0))
            self.assertIsNone(layout.image_point(20, layout.footer.y+5))
            if layout.video.x:
                self.assertIsNone(layout.image_point(layout.video.x-1, layout.video.y+5))


class PreviewInteractionTests(unittest.TestCase):
    def setUp(self):
        self.old_roi = [0.1, 0.2, 0.8, 0.9]
        self.controller = PreviewController(self.old_roi)
        self.controller.layout = PreviewLayout.create(1920, 1080, 960, 720)

    def point(self, x, y):
        video = self.controller.layout.video
        return video.x+x*video.width, video.y+y*video.height

    def click(self, name):
        rect = self.controller.layout.buttons[name]
        point = (rect.x+rect.width//2, rect.y+rect.height//2)
        first = self.controller.mouse("down", *point)
        second = self.controller.mouse("up", *point)
        return first or second

    def vertex(self, x, y):
        self.controller.mouse("down", *self.point(x, y))
        self.controller.mouse("up", *self.point(x, y))

    def polygon(self, points=((0.2, 0.3), (0.7, 0.3), (0.7, 0.8), (0.4, 0.5), (0.2, 0.8))):
        for x, y in points:
            self.vertex(x, y)

    def assertPointsAlmostEqual(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for point, wanted in zip(actual, expected):
            for coordinate, value in zip(point, wanted):
                self.assertAlmostEqual(coordinate, value)

    def test_select_immediately_pauses_then_clicks_save_a_concave_polygon(self):
        action = self.click("select")
        self.assertEqual(action.kind, "selection_started")
        self.assertTrue(self.controller.selecting)
        self.polygon()
        action = self.click("confirm")
        self.assertEqual(action.kind, "save")
        self.assertPointsAlmostEqual(action.roi, [[0.2, 0.3], [0.7, 0.3], [0.7, 0.8], [0.4, 0.5], [0.2, 0.8]])
        self.assertFalse(self.controller.selecting)
        # Persistence is acknowledged by the monitor only after save succeeds.
        self.assertEqual(self.controller.roi, self.old_roi)

    def test_cancel_and_escape_preserve_previous_roi(self):
        for cancel in (lambda: self.click("cancel"), lambda: self.controller.key(27)):
            self.controller.key(ord("R"))
            self.polygon()
            self.assertEqual(cancel().kind, "selection_cancelled")
            self.assertEqual(self.controller.roi, self.old_roi)
            self.assertFalse(self.controller.selecting)
            self.assertIsNone(self.controller.draft)
        self.assertEqual(self.controller.key(27).kind, "stop")

    def test_footer_letterbox_and_pointer_movement_cannot_add_vertices(self):
        self.controller.key(ord("r"))
        self.controller.layout = PreviewLayout.create(720, 1280, 960, 720)
        footer = self.controller.layout.footer
        self.controller.mouse("down", 1, footer.y+2)
        self.controller.mouse("move", *self.point(0.8, 0.8))
        self.controller.mouse("up", *self.point(0.8, 0.8))
        self.controller.mouse("down", self.controller.layout.video.x-1, 50)
        self.controller.mouse("up", *self.point(0.8, 0.8))
        self.assertEqual(self.controller.draft, [])
        self.assertIsNone(self.controller.key(13))
        self.assertTrue(self.controller.selecting)

    def test_resizing_between_clicks_preserves_normalized_vertices(self):
        self.controller.key(ord("r"))
        self.vertex(0.1, 0.2)
        self.controller.layout = PreviewLayout.create(1920, 1080, 320, 300)
        self.vertex(0.8, 0.3)
        self.controller.layout = PreviewLayout.create(1920, 1080, 1440, 900)
        self.vertex(0.3, 0.9)
        action = self.controller.key(13)
        self.assertPointsAlmostEqual(action.roi, [[0.1, 0.2], [0.8, 0.3], [0.3, 0.9]])

    def test_right_click_and_backspace_undo_one_vertex(self):
        self.controller.key(ord("r"))
        self.polygon()
        self.controller.mouse("right_down", *self.point(0.9, 0.9))
        self.assertEqual(len(self.controller.draft), 4)
        self.controller.key(8)
        self.assertEqual(len(self.controller.draft), 3)
        self.assertTrue(self.controller.can_confirm)
        self.controller.key(127)
        self.assertFalse(self.controller.can_confirm)
        for _ in range(4):
            self.controller.key(8)
        self.assertEqual(self.controller.draft, [])
        self.assertTrue(self.controller.selecting)

    def test_buttons_and_hotkeys_have_same_full_frame_and_stop_actions(self):
        self.assertEqual(self.click("full"), self.controller.key(ord("C")))
        self.assertEqual(self.click("stop"), self.controller.key(ord("Q")))
        self.controller.key(ord("r"))
        self.assertEqual(self.click("stop").kind, "stop")
        self.controller.key(ord("r"))
        action = self.click("full")
        self.assertEqual(action.kind, "save")
        self.assertIsNone(action.roi)
        self.assertFalse(self.controller.selecting)

    def test_fewer_than_three_vertices_cannot_save(self):
        self.assertIsNone(self.click("confirm"))
        self.controller.key(ord("r"))
        for x, y in ((0.1, 0.2), (0.7, 0.2)):
            self.vertex(x, y)
            self.assertFalse(self.controller.can_confirm)
            self.assertIsNone(self.click("confirm"))
            self.assertTrue(self.controller.selecting)
            self.assertEqual(self.controller.selection_error, "vertices")

    def test_crossing_duplicate_and_collinear_polygons_cannot_save(self):
        for points in (((0.1, 0.1), (0.8, 0.8), (0.1, 0.8), (0.8, 0.1)),
                       ((0.1, 0.2), (0.4, 0.2), (0.7, 0.2)),
                       ((0.1, 0.1), (0.8, 0.1), (0.8, 0.1), (0.2, 0.8))):
            with self.subTest(points=points):
                self.controller.key(ord("r"))
                self.polygon(points)
                self.assertFalse(self.controller.can_confirm)
                self.assertIsNone(self.controller.key(13))
                self.assertTrue(self.controller.selecting)
                self.assertEqual(self.controller.selection_error, "shape")
                self.assertEqual(self.controller.roi, self.old_roi)

    def test_polygon_inputs_and_save_result_do_not_share_mutable_vertices(self):
        saved = [[0.1, 0.1], [0.8, 0.1], [0.1, 0.8]]
        self.controller = PreviewController(saved)
        self.controller.layout = PreviewLayout.create(1920, 1080)
        saved[0][0] = 0.3
        self.assertEqual(self.controller.roi[0][0], 0.1)
        self.controller.key(ord("r"))
        self.polygon()
        draft = self.controller.draft
        action = self.controller.key(13)
        draft[0][0] = 0.9
        self.assertAlmostEqual(action.roi[0][0], 0.2)

    def test_selection_hints_explain_vertices_save_undo_and_invalid_shapes(self):
        with patch("preview_ui.ui_font", return_value=(None, True)):
            for width, height in ((960, 720), (320, 300)):
                self.controller.layout = PreviewLayout.create(1920, 1080, width, height)
                self.controller.key(ord("r"))
                lines = status_lines(self.controller, "IDLE", 0, None)
                self.assertEqual(len(lines), self.controller.layout.line_count)
                text = " ".join(lines)
                for hint in ("至少 3", "Enter", "右鍵", "Backspace", "Esc"):
                    self.assertIn(hint, text)
                self.polygon(((0.1, 0.2), (0.4, 0.2), (0.7, 0.2)))
                text = " ".join(status_lines(self.controller, "IDLE", 0, None))
                self.assertIn("區域無效", text)
                self.assertIn("Backspace", text)


class PreviewRenderingTests(unittest.TestCase):
    def test_renderer_preserves_source_and_puts_footer_outside_video(self):
        try:
            import numpy as np
            import PIL
        except ImportError:
            self.skipTest("Optional render check needs Pillow/numpy")
        frame = np.full((360, 640, 3), (30, 80, 120), dtype=np.uint8)
        original = frame.copy()
        controller = PreviewController()
        controller.layout = PreviewLayout.create(640, 360, 960, 720)
        preview = render_preview(frame, controller, "IDLE", 2, 15.3)
        self.assertTrue(np.array_equal(frame, original))
        self.assertEqual(preview.shape, (720, 960, 3))
        video = controller.layout.video
        self.assertTrue(np.array_equal(preview[video.y+video.height//2, video.x+video.width//2], frame[0, 0]))
        self.assertFalse(np.array_equal(preview[-1, -1], frame[0, 0]))

    def test_saved_polygon_outline_follows_edges_instead_of_bounding_rectangle(self):
        try:
            import numpy as np
            import PIL
        except ImportError:
            self.skipTest("Optional render check needs Pillow/numpy")
        frame = np.full((360, 640, 3), (30, 80, 120), dtype=np.uint8)
        original = frame.copy()
        controller = PreviewController([[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]])
        controller.layout = PreviewLayout.create(640, 360, 960, 720)
        preview = render_preview(frame, controller, "IDLE", 2, 15.3)
        edge_x, edge_y = controller.layout.canvas_point((0.5, 0.5))
        outside_x, outside_y = controller.layout.canvas_point((0.9, 0.6))
        self.assertTrue(np.array_equal(preview[edge_y, edge_x], (236, 206, 35)))
        self.assertTrue(np.array_equal(preview[outside_y, outside_x], frame[0, 0]))
        self.assertTrue(np.array_equal(frame, original))

    def test_draft_vertices_and_closed_edge_are_visible_without_changing_source(self):
        try:
            import numpy as np
            import PIL
        except ImportError:
            self.skipTest("Optional render check needs Pillow/numpy")
        frame = np.full((360, 640, 3), (30, 80, 120), dtype=np.uint8)
        original = frame.copy()
        controller = PreviewController()
        controller.layout = PreviewLayout.create(640, 360, 640, 480)
        controller.key(ord("r"))
        for point in ((0.1, 0.1), (0.9, 0.1), (0.1, 0.9)):
            video = controller.layout.video
            controller.mouse("down", video.x + point[0]*video.width, video.y + point[1]*video.height)
        preview = render_preview(frame, controller, "IDLE", None, None)
        for point in ((0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.1, 0.5)):
            x, y = controller.layout.canvas_point(point)
            self.assertTrue(np.array_equal(preview[y, x], (80, 208, 255)))
        self.assertTrue(np.array_equal(frame, original))


if __name__ == "__main__":
    unittest.main()
