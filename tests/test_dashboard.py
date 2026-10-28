"""Four-panel geometry, interaction, and private vehicle-photo association."""
from pathlib import Path
from itertools import permutations
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from preview_ui import PreviewController, PreviewLayout, render_preview
from latest_vehicle import LatestVehicleTracker
from record_protocol import read_json


class DashboardTests(unittest.TestCase):
    def controller(self, size=(1280, 820), source=(1600, 900), order=None):
        c = PreviewController([[.1, .2], [.8, .2], [.6, .8]])
        c.plate_enabled = True
        c.animation_time = 1.2
        if order:
            c.panel_order = list(order)
        c.layout = PreviewLayout.create(*source, *size, show_plates=True, dashboard=True, panel_order=c.panel_order)
        return c

    def test_all_reordered_panels_keep_controls_and_video_inside_their_cards(self):
        for size in ((640, 720), (800, 760), (1280, 820), (1920, 1080)):
            for order in permutations(('camera', 'plates', 'latest', 'controls')):
                c = self.controller(size=size, order=order)
                l = c.layout
                for name, r in l.panels.items():
                    self.assertGreaterEqual(r.x, 0)
                    self.assertLessEqual(r.x+r.width, l.width)
                    self.assertLessEqual(r.y+r.height, l.height)
                for name, button in l.buttons.items():
                    r = l.panels['latest' if name == 'review_plate' else 'controls']
                    self.assertTrue(r.contains(button.x, button.y))
                    self.assertLessEqual(button.x+button.width, r.x+r.width)
                    self.assertLessEqual(button.y+button.height, r.y+r.height)
                v, camera = l.video, l.panels['camera']
                self.assertAlmostEqual(v.width/v.height, 1600/900, delta=.015)
                self.assertGreaterEqual(v.y, camera.y+38)
                self.assertLessEqual(v.x+v.width, camera.x+camera.width)
                self.assertLessEqual(v.y+v.height, camera.y+camera.height)

    def test_header_drag_swaps_panels_without_adding_roi_points_and_no_reset_shortcut(self):
        c = self.controller()
        c.command('select')
        h, target = c.layout.headers['camera'], c.layout.panels['latest']
        c.mouse('down', h.x+20, h.y+15)
        c.mouse('move', target.x+20, target.y+70)
        action = c.mouse('up', target.x+20, target.y+70)
        self.assertEqual(action.kind, 'layout_changed')
        self.assertEqual(c.panel_order, ['latest', 'plates', 'camera', 'controls'])
        self.assertEqual(c.draft, [])
        self.assertIsNone(c.key(ord('l')))
        self.assertEqual(c.panel_order, ['latest', 'plates', 'camera', 'controls'])
        self.assertNotIn('reset_layout', c.layout.buttons)

    def test_drop_outside_cancels_and_reordered_roi_uses_actual_image_coordinates(self):
        c = self.controller(order=['controls', 'latest', 'plates', 'camera'])
        c.command('select')
        h = c.layout.headers['camera']
        c.mouse('down', h.x+4, h.y+4)
        self.assertIsNone(c.mouse('up', -10, -10))
        for point in ((.2, .2), (.8, .2), (.5, .8)):
            c.mouse('down', *c.layout.canvas_point(point))
        self.assertTrue(c.can_confirm)
        self.assertAlmostEqual(c.draft[0][0], .2, delta=.01)
        before = len(c.draft)
        p = c.layout.panels['latest']
        c.mouse('down', p.x+20, p.y+80)
        self.assertEqual(len(c.draft), before)

    def test_render_keeps_source_unchanged_and_replaces_black_camera_padding(self):
        for source in ((1600, 900), (720, 1280)):
            c = self.controller(size=(1000, 760), source=source)
            c.latest_vehicle_image = np.full((200, 350, 3), (80, 150, 210), np.uint8)
            c.latest_vehicle = dict(timestamp=1700000000, image_timestamp=1700000000, plate='AB1234')
            frame = np.full((source[1], source[0], 3), (80, 110, 130), np.uint8)
            original = frame.copy()
            rendered = render_preview(frame, c, 'RECORDING', 1, 35, 42)
            np.testing.assert_array_equal(original, frame)
            self.assertEqual(rendered.shape, (760, 1000, 3))
            p = c.layout.panels['camera']
            self.assertGreater(int(rendered[p.y+40, p.x+3].max()), 25)
            c.animation_time = 2.0
            self.assertFalse(np.array_equal(rendered, render_preview(frame, c, 'RECORDING', 1, 35, 42)))

    def test_buttons_and_plate_scroll_still_work_in_reordered_cards(self):
        c = self.controller(order=['latest', 'controls', 'camera', 'plates'])
        r = c.layout.buttons['select']
        self.assertEqual(c.mouse('down', r.x+10, r.y+10).kind, 'selection_started')
        r = c.layout.buttons['cancel']
        c.mouse('down', r.x+10, r.y+10)
        self.assertEqual(c.mouse('up', r.x+10, r.y+10).kind, 'selection_cancelled')
        c.plate_scroll_max = 100
        p = c.layout.plates
        c.mouse('wheel_down', p.x+10, p.y+10)
        self.assertEqual(c.plate_scroll, 3)
        r = c.layout.buttons['stop']
        c.mouse('down', r.x+10, r.y+10)
        self.assertEqual(c.mouse('up', r.x+10, r.y+10).kind, 'stop')


class VehiclePhotoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tracker = LatestVehicleTracker(self.root, now=100)
        self.frame = np.full((300, 500, 3), (60, 120, 180), np.uint8)
        self.vehicle = dict(class_id=2, box=[100, 80, 300, 240])

    def test_crop_is_local_bounded_independent_and_restored_only_with_matching_hash(self):
        original = self.frame.copy()
        self.tracker.observe([self.vehicle], 101, (500, 300), frame=self.frame)
        self.assertIsNotNone(self.tracker.image)
        self.assertLessEqual(self.tracker.image.shape[1], 960)
        self.assertLessEqual(self.tracker.image.shape[0], 540)
        np.testing.assert_array_equal(self.frame, original)
        self.frame[:] = 0
        self.assertGreater(self.tracker.image.max(), 0)
        saved = read_json(self.tracker.path)
        self.assertEqual(saved['image_timestamp'], 101)
        restored = LatestVehicleTracker(self.root, now=102)
        self.assertIsNotNone(restored.image)
        self.tracker.image_path.write_bytes(b'invalid photo')
        self.assertIsNone(LatestVehicleTracker(self.root, now=102).image)

    def test_missing_vehicle_keeps_last_photo_but_new_group_cannot_reuse_old_photo(self):
        self.tracker.observe([self.vehicle], 101, (500, 300), frame=self.frame)
        image = self.tracker.image
        self.tracker.observe([], 102, (500, 300), frame=self.frame)
        self.assertIs(self.tracker.image, image)
        self.assertEqual(self.tracker.latest['timestamp'], 101)
        self.tracker.observe([self.vehicle], 103, (500, 300))
        self.assertIsNone(self.tracker.image)
        self.assertNotIn('image_sha256', self.tracker.latest)

    def test_unavailable_capture_never_replaces_photo_and_image_rate_is_bounded(self):
        self.tracker.observe([self.vehicle], 101, (500, 300), frame=self.frame)
        image = self.tracker.image
        self.tracker.observe([self.vehicle], 101.2, (500, 300), frame=self.frame)
        self.assertIs(self.tracker.image, image)
        self.tracker.observe([self.vehicle], 102, (500, 300), available=False, frame=self.frame)
        self.assertEqual(self.tracker.latest['image_timestamp'], 101)

    def test_disk_failure_keeps_in_memory_photo_and_monitor_state(self):
        with patch('pathlib.Path.write_bytes', side_effect=PermissionError('synthetic')):
            self.tracker.observe([self.vehicle], 101, (500, 300), frame=self.frame)
        self.assertIsNotNone(self.tracker.image)
        self.assertNotIn('image_sha256', self.tracker.latest)
        self.assertEqual(self.tracker.latest['timestamp'], 101)


if __name__ == '__main__':
    unittest.main()
