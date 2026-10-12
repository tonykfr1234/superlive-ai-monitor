"""Pure geometry checks; no camera, model, network, or notification access."""
import math
import sys
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from region_policy import box_in_roi, people_in_region, pixel_roi, rebase_roi, roi_vertices, validate_roi


TRIANGLE = [[0, 0], [1, 0], [0, 1]]
CONCAVE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.4],
           [0.4, 0.4], [0.4, 0.9], [0.1, 0.9]]
U_SHAPE = [[0.1, 0.1], [0.3, 0.1], [0.3, 0.7], [0.7, 0.7],
           [0.7, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


class PolygonValidationTests(unittest.TestCase):
    def test_rectangle_format_and_unset_regions_remain_compatible(self):
        rectangle = [0.1, 0.2, 0.8, 0.9]
        self.assertEqual(validate_roi(rectangle), rectangle)
        self.assertIsNot(validate_roi(rectangle), rectangle)
        self.assertEqual(roi_vertices(rectangle), [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]])
        for unset in (None, []):
            self.assertIsNone(validate_roi(unset))
            self.assertIsNone(roi_vertices(unset))

    def test_triangle_quadrilateral_and_concave_shapes_accept_both_windings(self):
        for shape in (TRIANGLE, [[0, 0], [0.8, 0], [1, 1], [0.1, 0.9]], CONCAVE, U_SHAPE):
            for vertices in (shape, list(reversed(shape))):
                with self.subTest(vertices=vertices):
                    self.assertEqual(validate_roi(vertices), vertices)
                    self.assertEqual(roi_vertices(vertices), vertices)

    def test_normalization_detaches_outer_and_inner_lists(self):
        original = [[0, 0], [1, 0], [0, 1]]
        validated = validate_roi(original)
        validated[0][0] = 0.5
        validated.append([0.5, 0.5])
        self.assertEqual(original, TRIANGLE)
        self.assertEqual(validate_roi(((0, 0), (1, 0), (0, 1))), TRIANGLE)

    def test_straight_middle_vertices_and_very_small_positive_area_are_valid(self):
        self.assertEqual(validate_roi([[0, 0], [0.5, 0], [1, 0], [1, 1], [0, 1]])[1], [0.5, 0])
        small = [[0.5, 0.5], [0.500000001, 0.5], [0.5, 0.500000001]]
        self.assertEqual(validate_roi(small), small)

    def test_malformed_and_nonfinite_coordinates_are_rejected_as_value_errors(self):
        invalid = [
            [[0, 0], [1, 1]], [[0, 0], [1, 0], [0, 1, 2]],
            [[0, 0], [1, 0], 1], [[0, 0], [1, 0], [None, 1]],
            [[0, 0], [1, 0], ["bad", 1]], [[0, 0], [1, 0], [math.nan, 1]],
            [[0, 0], [1, 0], [math.inf, 1]], [[0, 0], [1, 0], [-0.1, 1]],
            [[0, 0], [1, 0], [0, 1.1]], [0, 0, None, 1],
        ]
        for shape in invalid:
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                validate_roi(shape)

    def test_degenerate_crossing_touching_and_retraced_shapes_are_rejected(self):
        invalid = [
            [[0, 0], [0.5, 0.5], [1, 1]],
            [[0, 0], [1, 0], [0, 1], [0, 0]],
            [[0, 0], [1, 1], [0, 1], [1, 0]],
            [[0, 0], [1, 0.8], [0, 1], [0.8, 0]],  # nonzero-area crossing
            [[0, 0], [1, 0], [0.5, 0], [1, 1], [0, 1]],  # retraced edge
            [[0, 0], [1, 0], [1, 1], [0.5, 0], [0, 1]],  # non-neighbour touch
        ]
        for shape in invalid:
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                validate_roi(shape)


class PolygonIntersectionTests(unittest.TestCase):
    def test_bounding_box_is_available_but_its_empty_corner_does_not_count(self):
        self.assertEqual(pixel_roi(TRIANGLE, 100, 200), (0, 0, 100, 200))
        self.assertFalse(box_in_roi((80, 160, 90, 180), TRIANGLE, 100, 200))
        self.assertTrue(box_in_roi((5, 10, 10, 20), TRIANGLE, 100, 200))

    def test_containment_crossings_partial_overlap_and_edge_only_contacts(self):
        cases = [
            ((10, 10, 20, 20), True),
            ((-10, -10, 110, 110), True),  # person contains the whole polygon
            ((-10, 40, 110, 50), True),  # only crossing edges, no enclosed vertices
            ((40, 40, 80, 80), True),  # positive area despite outside box center
            ((50, 50, 60, 60), False),  # sloped-edge tangent at one corner
            ((10, -10, 20, 0), False),  # horizontal-edge contact
            ((-10, 10, 0, 20), False),  # vertical-edge contact
            ((100, 0, 110, 10), False),  # polygon vertex only
            ((50, 50, 50, 60), False),
            ((20, 20, 10, 10), False),
        ]
        for shape in (TRIANGLE, list(reversed(TRIANGLE))):
            for box, expected in cases:
                with self.subTest(box=box, shape=shape):
                    self.assertEqual(box_in_roi(box, shape, 100, 100), expected)

    def test_concave_notch_is_excluded_in_both_windings(self):
        for shape in (CONCAVE, list(reversed(CONCAVE))):
            self.assertFalse(box_in_roi((50, 50, 80, 80), shape, 100, 100))
            self.assertFalse(box_in_roi((40, 40, 80, 80), shape, 100, 100))
            self.assertTrue(box_in_roi((39, 50, 80, 80), shape, 100, 100))
            self.assertTrue(box_in_roi((50, 39, 80, 80), shape, 100, 100))

    def test_box_can_intersect_multiple_disconnected_parts_of_a_concave_polygon(self):
        for shape in (U_SHAPE, list(reversed(U_SHAPE))):
            self.assertTrue(box_in_roi((0, 20, 100, 50), shape, 100, 100))
            self.assertFalse(box_in_roi((30, 20, 70, 50), shape, 100, 100))

    def test_people_filter_uses_polygon_with_class_and_confidence_rules(self):
        boxes = [(10, 10, 20, 20), (70, 70, 90, 90), (10, 10, 20, 20), (10, 10, 20, 20)]
        self.assertEqual(people_in_region(boxes, [0, 0, 2, 0], [0.9, 0.9, 0.9, 0.1],
                                         TRIANGLE, 100, 100, 0.25),
                         [{"box": (10, 10, 20, 20), "confidence": 0.9}])

    def test_polygon_scales_with_asymmetric_resolution_changes(self):
        self.assertEqual(box_in_roi((20, 20, 50, 50), TRIANGLE, 100, 100),
                         box_in_roi((60, 40, 150, 100), TRIANGLE, 300, 200))

    def test_decimal_diagonal_tangency_survives_pixel_scaling_without_false_overlap(self):
        shape = [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]]
        for width, height in ((100, 100), (1920, 1080), (1371, 777), (7680, 4320)):
            for x, y in ((0.18, 0.82), (0.42, 0.58), (0.58, 0.42)):
                for vertices in (shape, list(reversed(shape))):
                    with self.subTest(width=width, height=height, x=x, y=y, vertices=vertices):
                        box = (x*width, y*height, (x+0.01)*width, (y+0.01)*height)
                        self.assertFalse(box_in_roi(box, vertices, width, height))
                        # A tiny but meaningful inward move remains a positive overlap.
                        overlapping = (box[0]-0.000001, box[1]-0.000001, box[2], box[3])
                        self.assertTrue(box_in_roi(overlapping, vertices, width, height))

    def test_tiny_positive_regions_are_not_discarded_by_a_fixed_area_threshold(self):
        shape = [[0.5, 0.5], [0.500000001, 0.5], [0.5, 0.500000001]]
        self.assertTrue(box_in_roi((0, 0, 100, 100), shape, 100, 100))


class PolygonRebaseTests(unittest.TestCase):
    def assertVerticesAlmostEqual(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for point, target in zip(actual, expected):
            for coordinate, value in zip(point, target):
                self.assertAlmostEqual(coordinate, value)

    def test_translation_and_normalization_preserve_absolute_polygon(self):
        original = [[0.2, 0.4], [0.8, 0.4], [0.5, 0.8]]
        migrated = rebase_roi(original, (100, 200, 1100, 700), (200, 300, 1000, 650))
        self.assertVerticesAlmostEqual(migrated, [[0.125, 100/350], [0.875, 100/350], [0.5, 300/350]])

    def test_clipping_triangle_preserves_diagonal_boundary(self):
        migrated = rebase_roi(TRIANGLE, (0, 0, 100, 100), (0, 20, 100, 100))
        self.assertEqual(len(migrated), 3)
        self.assertFalse(box_in_roi((70, 50, 80, 60), migrated, 100, 80))
        self.assertTrue(box_in_roi((5, 5, 10, 10), migrated, 100, 80))
        self.assertAlmostEqual(abs(sum(migrated[i][0]*migrated[(i+1) % 3][1]
                                           - migrated[(i+1) % 3][0]*migrated[i][1]
                                           for i in range(3)))/2, 0.4)

    def test_clipping_connected_concave_polygon_keeps_notch(self):
        migrated = rebase_roi(CONCAVE, (0, 0, 100, 100), (20, 20, 80, 80))
        self.assertEqual(len(migrated), 6)
        self.assertTrue(box_in_roi((1, 1, 10, 10), migrated, 60, 60))
        self.assertFalse(box_in_roi((25, 25, 50, 50), migrated, 60, 60))

    def test_polygon_that_contains_crop_is_an_explicit_full_crop_polygon(self):
        migrated = rebase_roi([[0, 0], [1, 0], [1, 1], [0, 1]],
                              (0, 0, 100, 100), (20, 20, 80, 80))
        self.assertEqual(set(map(tuple, migrated)), {(0, 0), (1, 0), (1, 1), (0, 1)})
        self.assertIsNotNone(migrated)

    def test_disconnected_clipping_requires_reselection(self):
        for shape in (U_SHAPE, list(reversed(U_SHAPE))):
            with self.assertRaisesRegex(ValueError, "select a new region"):
                rebase_roi(shape, (0, 0, 100, 100), (0, 0, 100, 50))

    def test_empty_and_edge_only_intersections_never_become_full_frame(self):
        for crop in ((100, 0, 200, 100), (110, 0, 200, 100), (50, 50, 100, 100)):
            with self.subTest(crop=crop), self.assertRaisesRegex(ValueError, "select a new region"):
                rebase_roi(TRIANGLE, (0, 0, 100, 100), crop)

    def test_decimal_corner_only_crop_requires_reselection(self):
        shape = [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]]
        for width, height in ((100, 100), (1920, 1080), (1371, 777), (7680, 4320)):
            with self.subTest(width=width, height=height), self.assertRaisesRegex(ValueError, "select a new region"):
                rebase_roi(shape, (0, 0, width, height),
                           (0.42*width, 0.58*height, 0.43*width, 0.59*height))


if __name__ == "__main__":
    unittest.main()
