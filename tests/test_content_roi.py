"""ROI migration uses synthetic coordinates, never live camera content."""
import sys
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from region_policy import pixel_roi, rebase_roi


class ContentRoiMigrationTests(unittest.TestCase):
    def assertCoordinatesAlmostEqual(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target)

    def test_rebasing_preserves_absolute_scene_area(self):
        # Client is 800 x 448; camera starts 64 pixels below its top.
        parent = (100, 200, 900, 648)
        content = (100, 264, 900, 648)
        old_roi = [0.25, 112/448, 0.75, 336/448]
        migrated = rebase_roi(old_roi, parent, content)
        self.assertCoordinatesAlmostEqual(migrated, [0.25, 48/384, 0.75, 272/384])
        camera_pixels = pixel_roi(migrated, 800, 384)
        absolute = [camera_pixels[0]+100, camera_pixels[1]+264,
                    camera_pixels[2]+100, camera_pixels[3]+264]
        self.assertCoordinatesAlmostEqual(absolute, [300, 312, 700, 536])

    def test_toolbar_overlap_is_clipped_without_expanding_selection(self):
        migrated = rebase_roi([0.25, 0, 0.75, 0.5], (0, 0, 800, 448), (0, 64, 800, 448))
        self.assertCoordinatesAlmostEqual(migrated, [0.25, 0, 0.75, 160/384])
        # A crop on all four sides clamps an old explicit full-frame selection.
        self.assertEqual(rebase_roi([0, 0, 1, 1], (10, 20, 810, 468), (30, 84, 790, 440)), [0, 0, 1, 1])

    def test_toolbar_only_and_boundary_only_selections_raise(self):
        for roi in ([0.1, 0.01, 0.9, 0.1], [0, 0, 1, 64/448]):
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                rebase_roi(roi, (0, 0, 800, 448), (0, 64, 800, 448))

    def test_normalized_result_is_invariant_to_uniform_resize_and_translation(self):
        original = rebase_roi([0.25, 0.25, 0.75, 0.75], (0, 0, 800, 448), (0, 64, 800, 448))
        doubled = rebase_roi([0.25, 0.25, 0.75, 0.75], (-300, 50, 1300, 946), (-300, 178, 1300, 946))
        self.assertCoordinatesAlmostEqual(doubled, original)

    def test_unset_roi_remains_full_camera_content(self):
        for roi in (None, []):
            self.assertIsNone(rebase_roi(roi, (0, 0, 800, 448), (0, 64, 800, 448)))

    def test_invalid_rectangles_are_rejected_even_without_explicit_roi(self):
        valid = (0, 0, 800, 448)
        invalid = ((0, 0, 0, 448), (0, 448, 800, 0), (0, 0, float("nan"), 448),
                   (0, 0, 800, float("inf")), (-1e308, 0, 1e308, 448),
                   (0, 0, 800), None, (0, 0, "bad", 448))
        for rectangle in invalid:
            for parent, content in ((rectangle, valid), (valid, rectangle)):
                with self.subTest(parent=parent, content=content), self.assertRaises(ValueError):
                    rebase_roi(None, parent, content)


if __name__ == "__main__":
    unittest.main()
