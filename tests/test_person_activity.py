"""Synthetic activity/timeout tests; no camera, model, GUI, or network access."""
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from person_activity import InactivityTimer, PersonActivityTracker


def people(*boxes):
    return [{"box": box, "confidence": 0.9} for box in boxes]


class PersonActivityTests(unittest.TestCase):
    def setUp(self):
        self.tracker = PersonActivityTracker()
        self.box = (100, 100, 300, 600)

    def update(self, boxes, now, width=1000, height=1000):
        return self.tracker.update(people(*boxes), width, height, now)

    def test_initial_arrival_moves_but_continuously_visible_stationary_person_does_not(self):
        self.assertTrue(self.update([self.box], 0))
        for now in (0.1, 1, 120, 121, 300):
            self.assertFalse(self.update([self.box], now))

    def test_small_jitter_does_not_accumulate_as_movement(self):
        self.update([self.box], 0)
        for index in range(1, 100):
            offset = (index % 7)-3
            box = (100+offset, 100-offset, 300-offset, 600+offset)
            self.assertFalse(self.update([box], index*0.1))

    def test_slow_displacement_accumulates_against_stable_anchor(self):
        self.update([self.box], 0)
        for offset in (2, 4, 6, 8):
            self.assertFalse(self.update([(100+offset, 100, 300+offset, 600)], offset))
        self.assertTrue(self.update([(110, 100, 310, 600)], 10))
        self.assertFalse(self.update([(110, 100, 310, 600)], 11))
        self.assertTrue(self.update([(120, 100, 320, 600)], 12))

    def test_large_size_change_counts_when_center_is_unchanged(self):
        self.update([self.box], 0)
        self.assertFalse(self.update([(96, 100, 304, 600)], 1))
        self.assertTrue(self.update([(90, 100, 310, 600)], 2))

    def test_small_frames_still_require_three_pixels_of_center_movement(self):
        self.update([(10, 10, 30, 60)], 0, 100, 100)
        self.assertFalse(self.update([(12, 10, 32, 60)], 1, 100, 100))
        self.assertTrue(self.update([(13, 10, 33, 60)], 2, 100, 100))

    def test_association_is_independent_of_detector_order_and_tracks_multiple_people(self):
        second = (600, 150, 780, 650)
        self.assertTrue(self.update([self.box, second], 0))
        self.assertFalse(self.update([second, self.box], 1))
        self.assertTrue(self.update([(620, 150, 800, 650), self.box], 2))
        self.assertFalse(self.update([self.box, (620, 150, 800, 650)], 3))
        self.assertTrue(self.update([self.box, (620, 150, 800, 650), (400, 100, 500, 400)], 4))

    def test_brief_missing_detections_are_not_new_arrivals(self):
        self.assertTrue(self.update([self.box], 0))
        self.assertFalse(self.update([], 0.1))
        self.assertFalse(self.update([], 0.7))
        self.assertFalse(self.update([self.box], 0.9))
        self.assertFalse(self.update([self.box], 20))

    def test_one_missing_person_does_not_make_other_people_arrive_again(self):
        second = (600, 150, 780, 650)
        self.update([self.box, second], 0)
        self.assertFalse(self.update([second], 0.1))
        self.assertFalse(self.update([second, self.box], 0.5))

    def test_observed_long_absence_then_return_is_a_new_arrival(self):
        self.update([self.box], 0)
        self.assertFalse(self.update([], 1))
        self.assertFalse(self.update([], 2.1))
        self.assertFalse(self.update([], 120))
        self.assertTrue(self.update([self.box], 121))

    def test_capture_resolution_change_does_not_count_as_movement(self):
        self.update([self.box], 0)
        self.assertFalse(self.update([(200, 50, 600, 300)], 1, 2000, 500))
        self.assertTrue(self.update([(220, 50, 620, 300)], 2, 2000, 500))

    def test_reset_for_new_roi_discards_old_person_association(self):
        self.update([self.box], 0)
        self.tracker.reset()
        self.assertTrue(self.update([self.box], 1))

    def test_invalid_boxes_raise_instead_of_becoming_observed_absence(self):
        self.update([self.box], 0)
        for box in ((1, 1, 1, 2), (1, 1, float("nan"), 2), (1, 1, 2)):
            with self.subTest(box=box), self.assertRaises(ValueError):
                self.update([box], 1)
        self.assertFalse(self.update([self.box], 2))


class InactivityTimerTests(unittest.TestCase):
    def test_exact_120_seconds_of_observed_inactivity(self):
        timer = InactivityTimer()
        self.assertFalse(timer.observe("event-1", False, 10))
        self.assertFalse(timer.observe("event-1", False, 129.999))
        self.assertTrue(timer.observe("event-1", False, 130))

    def test_motion_restarts_timer_and_motion_at_deadline_prevents_stop(self):
        timer = InactivityTimer()
        self.assertFalse(timer.observe("event-1", True, 0))
        self.assertFalse(timer.observe("event-1", False, 119))
        self.assertFalse(timer.observe("event-1", True, 120))
        self.assertFalse(timer.observe("event-1", False, 239.999))
        self.assertTrue(timer.observe("event-1", False, 240))

    def test_suspend_discards_unknown_time_and_prior_partial_inactivity(self):
        timer = InactivityTimer()
        timer.observe("event-1", False, 0)
        timer.observe("event-1", False, 100)
        timer.suspend()
        self.assertFalse(timer.observe("event-1", False, 1000))
        self.assertFalse(timer.observe("event-1", False, 1119.999))
        self.assertTrue(timer.observe("event-1", False, 1120))

    def test_event_rollover_and_idle_reset_start_new_intervals(self):
        timer = InactivityTimer()
        timer.observe("first", False, 0)
        self.assertTrue(timer.observe("first", False, 120))
        self.assertFalse(timer.observe("second", False, 130))
        self.assertFalse(timer.observe("second", False, 249))
        self.assertTrue(timer.observe("second", False, 250))
        timer.reset()
        self.assertFalse(timer.observe("second", False, 300))
        self.assertFalse(timer.observe(None, False, 500))
        self.assertFalse(timer.observe("third", False, 600))

    def test_stationary_person_can_finish_event_without_immediately_retriggering(self):
        tracker, timer = PersonActivityTracker(), InactivityTimer()
        person = people((100, 100, 300, 600))
        moving = tracker.update(person, 1000, 1000, 0)
        self.assertTrue(moving)
        self.assertFalse(timer.observe("event-1", moving, 0))
        for now in (1, 60, 120):
            moving = tracker.update(person, 1000, 1000, now)
            timed_out = timer.observe("event-1", moving, now)
        self.assertTrue(timed_out)
        timer.reset()  # Recorder returns to IDLE; tracker is deliberately kept.
        self.assertFalse(tracker.update(person, 1000, 1000, 121))
        self.assertTrue(tracker.update(people((115, 100, 315, 600)), 1000, 1000, 122))

    def test_absence_after_arrival_also_expires_inactivity(self):
        tracker, timer = PersonActivityTracker(), InactivityTimer()
        timer.observe("event-1", tracker.update(people((100, 100, 300, 600)), 1000, 1000, 0), 0)
        self.assertFalse(timer.observe("event-1", tracker.update([], 1000, 1000, 119), 119))
        self.assertTrue(timer.observe("event-1", tracker.update([], 1000, 1000, 120), 120))


if __name__ == "__main__":
    unittest.main()
