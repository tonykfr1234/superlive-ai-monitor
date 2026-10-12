"""Synthetic MuMu chrome tests. No live application or user screenshots."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import capture_region
from capture_region import CaptureRegionResolver, CaptureRegionUnavailable, capture_frame, detect_toolbar_top

try:
    import numpy as np
except ImportError:
    np = None


class FakeGui:
    def __init__(self, width, height, origin=(100, 200), children=None):
        self.width, self.height = width, height
        self.origin = origin
        self.children = children or {}
    def ClientToScreen(self, hwnd, point):
        return self.origin if hwnd == 1 else self.children[hwnd]["origin"]
    def GetClientRect(self, hwnd):
        size = (self.width, self.height) if hwnd == 1 else self.children[hwnd]["size"]
        return 0, 0, *size
    def EnumChildWindows(self, hwnd, callback, _):
        for child in self.children:
            callback(child, None)
    def GetClassName(self, hwnd):
        return self.children[hwnd].get("class", "QWidget")
    def GetWindowText(self, hwnd):
        return self.children[hwnd].get("title", "")
    def IsWindowVisible(self, hwnd):
        return self.children[hwnd].get("visible", True)


@unittest.skipIf(np is None, "Synthetic capture tests require numpy")
class CaptureRegionTests(unittest.TestCase):
    def scene(self, width=688, height=446, toolbar=62, separator=1):
        # Textured camera pixels, including synthetic channel/timestamp markers.
        rng = np.random.default_rng(42)
        scene = rng.integers(55, 185, (height, width, 3), dtype=np.uint8)
        scene[:toolbar] = 232
        for fraction in (0.055, 0.18, 0.53, 0.69, 0.85, 0.95):
            x = int(width*fraction)
            scene[int(toolbar*.35):int(toolbar*.65), x:x+max(3, int(width*.012))] = 35
        if separator:
            scene[toolbar:toolbar+separator] = (210, 202, 0)
        scene[toolbar+separator+5:toolbar+separator+10, 5:25] = (255, 255, 255)
        scene[-12:-5, -100:-5] = (255, 255, 255)
        return scene

    def test_toolbar_detection_scales_and_preserves_camera_overlay_rows(self):
        for width, height, toolbar, separator in ((688, 446, 62, 1), (344, 223, 31, 1), (1376, 892, 124, 2)):
            with self.subTest(size=(width, height)):
                frame = self.scene(width, height, toolbar, separator)
                self.assertEqual(detect_toolbar_top(frame, np), toolbar+separator)
                camera = frame[toolbar+separator:]
                self.assertTrue(np.all(camera[5:10, 5:25] == 255))
                self.assertTrue(np.all(camera[-12:-5, -100:-5] == 255))

    def test_plain_bright_or_natural_dark_top_never_get_blindly_trimmed(self):
        frames = [np.full((446, 688, 3), 232, dtype=np.uint8),
                  np.random.default_rng(1).integers(0, 255, (446, 688, 3), dtype=np.uint8),
                  np.random.default_rng(2).integers(0, 25, (446, 688, 3), dtype=np.uint8)]
        # A bright featureless sky above textured content is not a toolbar.
        sky = self.scene()
        sky[:62] = 232
        frames.append(sky)
        for frame in frames:
            with self.assertRaises(CaptureRegionUnavailable):
                detect_toolbar_top(frame, np)

    def test_recognized_toolbar_over_dark_night_camera_works_on_first_frame(self):
        frame = self.scene()
        frame[63:] = 0
        self.assertEqual(detect_toolbar_top(frame, np), 63)
        frame[:62] = 232  # Removing icons makes a pale sky/band ambiguous again.
        with self.assertRaises(CaptureRegionUnavailable):
            detect_toolbar_top(frame, np)

    def test_parent_offset_and_camera_bottom_are_preserved_in_capture(self):
        frame = self.scene()
        bgra = np.concatenate((frame, np.full((*frame.shape[:2], 1), 255, dtype=np.uint8)), axis=2)
        sct = Mock()
        sct.grab.return_value = bgra
        cv = Mock()
        cv.cvtColor.side_effect = lambda image, _: image[:, :, :3]
        gui = FakeGui(688, 446, origin=(-500, 120))
        camera, info = capture_frame(sct, 1, gui, cv, np)
        self.assertEqual(info.parent_client_rect, (-500, 120, 188, 566))
        self.assertEqual(info.content_rect, (-500, 183, 188, 566))
        self.assertEqual(info.method, "toolbar_detection")
        self.assertEqual(camera.shape, (383, 688, 3))
        self.assertTrue(np.array_equal(camera, frame[63:]))
        sct.grab.assert_called_once_with(dict(left=-500, top=120, width=688, height=446))

    def test_distinct_render_child_wins_and_child_rect_is_intersected(self):
        frame = np.zeros((446, 688, 3), dtype=np.uint8)
        gui = FakeGui(688, 446, children={
            2: {"class": "QtSurfaceWindow", "origin": (90, 260), "size": (720, 400)},
            3: {"class": "QWidget", "origin": (100, 200), "size": (688, 446)}})
        info = CaptureRegionResolver().resolve(frame, 1, (100, 200, 788, 646), gui, np)
        self.assertEqual(info.content_rect, (100, 260, 788, 646))
        self.assertEqual(info.method, "render_child")
        # Top-only crop intentionally retains parent bottom and side camera bands.

    def test_full_parent_generic_or_render_widgets_do_not_swallow_toolbar(self):
        frame = self.scene()
        gui = FakeGui(688, 446, children={
            2: {"class": "OpenGLRenderWindow", "origin": (100, 200), "size": (688, 446)},
            3: {"class": "QWidget", "origin": (100, 250), "size": (688, 396)},
            4: {"class": "RenderWindow", "origin": (100, 260), "size": (688, 386), "visible": False}})
        info = CaptureRegionResolver().resolve(frame, 1, (100, 200, 788, 646), gui, np)
        self.assertEqual(info.method, "toolbar_detection")
        self.assertEqual(info.content_rect[1], 263)

    def test_cached_boundary_survives_night_scene_without_crop_jitter(self):
        resolver = CaptureRegionResolver()
        frame = self.scene()
        gui = FakeGui(688, 446)
        initial = resolver.resolve(frame, 1, (100, 200, 788, 646), gui, np)
        dark = frame.copy()
        dark[63:] = 0
        with patch.object(capture_region, "detect_toolbar_top", side_effect=AssertionError("must use established boundary")):
            result = resolver.resolve(dark, 1, (300, 400, 988, 846), gui, np)
        self.assertTrue(result.cached)
        self.assertEqual(result.content_rect, (300, 463, 988, 846))
        self.assertFalse(initial.cached)

    def test_resize_rechecks_and_fullscreen_chrome_loss_invalidates_cache(self):
        resolver = CaptureRegionResolver()
        gui = FakeGui(688, 446)
        resolver.resolve(self.scene(), 1, (100, 200, 788, 646), gui, np)
        resized = self.scene(1376, 892, 124, 2)
        big_gui = FakeGui(1376, 892)
        result = resolver.resolve(resized, 1, (100, 200, 1476, 1092), big_gui, np)
        self.assertFalse(result.cached)
        self.assertEqual(result.content_rect[1], 326)
        with self.assertRaises(CaptureRegionUnavailable):
            resolver.resolve(np.zeros_like(resized), 1, (100, 200, 1476, 1092), big_gui, np)
        self.assertIsNone(resolver.key)

    def test_same_client_size_toolbar_growth_and_shrink_recompute_exact_boundary(self):
        resolver = CaptureRegionResolver()
        gui = FakeGui(688, 446)
        for toolbar in (62, 80, 44, 62, 63, 61):
            with self.subTest(toolbar=toolbar):
                info = resolver.resolve(self.scene(toolbar=toolbar), 1, (100, 200, 788, 646), gui, np)
                self.assertEqual(info.content_rect, (100, 200+toolbar+1, 788, 646))
                self.assertFalse(info.cached)

    def test_same_client_size_render_child_top_change_invalidates_cache(self):
        frame = self.scene()
        gui = FakeGui(688, 446, children={
            2: {"class": "OpenGLRender", "origin": (100, 263), "size": (688, 383)}})
        resolver = CaptureRegionResolver()
        first = resolver.resolve(frame, 1, (100, 200, 788, 646), gui, np)
        gui.children[2]["origin"] = (100, 281)
        gui.children[2]["size"] = (688, 365)
        second = resolver.resolve(frame, 1, (100, 200, 788, 646), gui, np)
        self.assertEqual(first.content_rect[1], 263)
        self.assertEqual(second.content_rect[1], 281)
        self.assertFalse(second.cached)

    def test_letterbox_below_toolbar_is_preserved_once_boundary_known(self):
        frame = self.scene()
        resolver = CaptureRegionResolver()
        gui = FakeGui(688, 446)
        resolver.resolve(frame, 1, (100, 200, 788, 646), gui, np)
        frame[63:90] = 0
        frame[-30:] = 0
        result = resolver.resolve(frame, 1, (100, 200, 788, 646), gui, np)
        self.assertEqual(result.content_rect, (100, 263, 788, 646))
        self.assertTrue(result.cached)


if __name__ == "__main__":
    unittest.main()
