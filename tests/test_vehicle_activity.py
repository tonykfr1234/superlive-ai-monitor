"""Vehicle-only motion must use the same quiet/minimum/inactivity rules."""
import unittest

from test_activity_monitor import ActivityMonitorTests, monitor
from person_activity import PersonActivityTracker
from record_protocol import read_json
from region_policy import objects_in_region, object_counts


class ObjectRegionTests(unittest.TestCase):
    def test_supported_motor_vehicles_and_people_require_confident_roi_overlap(self):
        boxes = [(10, 10, 40, 50)] * 8
        classes = [0, 2, 3, 5, 7, 1, 16, 2]
        confidence = [.9] * 7 + [.1]
        found = objects_in_region(boxes, classes, confidence,
                                  [[0, 0], [.8, 0], [.8, .8], [0, .8]], 100, 100, .25)
        self.assertEqual([item['kind'] for item in found], ['person', 'car', 'motorcycle', 'bus', 'truck'])
        self.assertEqual(object_counts(found), dict(person=1, car=1, motorcycle=1, bus=1, truck=1))
        self.assertEqual(objects_in_region([(90, 90, 99, 99)], [2], [.99], [0, 0, .5, .5], 100, 100, .25), [])

    def test_person_cannot_reuse_vehicle_track_but_vehicle_subtype_jitter_can(self):
        tracker = PersonActivityTracker()
        def obj(cls): return [{'box': (10, 10, 70, 70), 'class_id': cls}]
        self.assertTrue(tracker.update(obj(2), 100, 100, 1))
        self.assertFalse(tracker.update(obj(7), 100, 100, 2))
        self.assertTrue(tracker.update(obj(0), 100, 100, 3))


class VehicleMotionIntegrationTests(ActivityMonitorTests):
    # Reuse every boundary test with vehicles, not just one artificial arrival.
    def setUp(self):
        super().setUp()
        self.people = [dict(item, class_id=2, kind='car') for item in self.people]
        self.moved = [dict(item, class_id=2, kind='car') for item in self.moved]

    def test_vehicle_only_request_records_actual_type_and_zero_people(self):
        self.assertEqual(self.observe(status='IDLE', event=None), 'recording_started')
        request = read_json(monitor.REQUEST_FILE)
        self.assertEqual(request['count'], 1)
        self.assertEqual(request['object_counts']['person'], 0)
        self.assertEqual(request['object_counts']['car'], 1)


if __name__ == '__main__':
    unittest.main()
