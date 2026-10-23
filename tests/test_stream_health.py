"""Content freshness must not be confused with detected object movement."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from stream_health import FrameStallDetector


class FrameStallDetectorTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((360, 640, 3), 80, dtype=np.uint8)

    def test_identical_frames_become_suspect_at_timeout(self):
        detector = FrameStallDetector()
        self.assertFalse(detector.observe(self.frame, 100))
        self.assertFalse(detector.observe(self.frame.copy(), 159.9))
        self.assertTrue(detector.observe(self.frame.copy(), 160))
        self.assertEqual(detector.stale_for(161), 61)
        self.assertTrue(detector.suspected)

    def test_quiet_scene_with_small_timestamp_change_stays_fresh(self):
        detector = FrameStallDetector()
        for second in range(0, 240):
            frame = self.frame.copy()
            # Camera seconds digit: much less than 0.1% of the whole picture.
            frame[343:350, 605 + second % 10:607 + second % 10] = 240
            self.assertFalse(detector.observe(frame, second))
        self.assertEqual(detector.stale_for(239), 0)

    def test_high_resolution_timestamp_survives_area_resampling(self):
        detector = FrameStallDetector()
        frame = np.full((900, 1600, 3), 80, np.uint8)
        self.assertFalse(detector.observe(frame, 0))
        frame[865:881, 1510:1516] = 245
        self.assertFalse(detector.observe(frame, 60))
        self.assertEqual(detector.stale_for(60), 0)

    def test_low_encoder_noise_does_not_hide_frozen_content(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 0)
        rng = np.random.default_rng(174)
        for second in range(1, 61):
            noise = rng.integers(-3, 4, self.frame.shape, dtype=np.int16)
            frame = (self.frame.astype(np.int16) + noise).astype(np.uint8)
            result = detector.observe(frame, second)
            self.assertEqual(result, second == 60)

    def test_isolated_hot_pixels_do_not_count_as_timestamp(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 0)
        frame = self.frame.copy()
        for index in range(20):
            frame[index * 10, index * 20] = 255
        self.assertTrue(detector.observe(frame, 60))

    def test_configured_camera_roi_ignores_toolbar_animation(self):
        detector = FrameStallDetector(roi=(0, 0.1, 1, 0.9))
        detector.observe(self.frame, 0)
        for second in range(1, 61):
            frame = self.frame.copy()
            frame[:25] = 150 if second % 2 else 250
            frame[335:] = second
            self.assertEqual(detector.observe(frame, second), second == 60)

    def test_real_content_change_clears_suspicion(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 0)
        self.assertTrue(detector.observe(self.frame, 60))
        changed = self.frame.copy()
        changed[120:140, 100:130] = 180
        self.assertFalse(detector.observe(changed, 61))
        self.assertEqual(detector.stale_for(61), 0)

    def test_slow_change_accumulates_against_stable_anchor(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 0)
        for second in range(1, 13):
            frame = self.frame.copy()
            frame[100:110, 100:110] += second
            self.assertFalse(detector.observe(frame, second))
        self.assertEqual(detector.stale_for(12), 0)

    def test_normal_capture_rollover_does_not_reset_timer(self):
        detector = FrameStallDetector(timeout_seconds=200)
        for second in (0, 60, 120, 179, 180, 181, 199):
            self.assertFalse(detector.observe(self.frame, second))
        self.assertTrue(detector.observe(self.frame, 200))

    def test_explicit_reset_starts_new_observation(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 0)
        self.assertTrue(detector.observe(self.frame, 60))
        detector.reset()
        self.assertFalse(detector.suspected)
        self.assertEqual(detector.stale_for(60), 0)
        self.assertFalse(detector.observe(self.frame, 61))
        self.assertFalse(detector.observe(self.frame, 120))
        self.assertTrue(detector.observe(self.frame, 121))

    def test_shape_change_and_clock_regression_reset_conservatively(self):
        detector = FrameStallDetector()
        detector.observe(self.frame, 10)
        self.assertFalse(detector.observe(self.frame[:180], 70))
        self.assertFalse(detector.observe(self.frame[:180], 5))
        self.assertEqual(detector.stale_for(5), 0)

    def test_no_sample_blank_frames_and_invalid_input(self):
        detector = FrameStallDetector()
        self.assertEqual(detector.stale_for(100), 0)
        blank = np.zeros_like(self.frame)
        self.assertFalse(detector.observe(blank, 0))
        with self.assertRaises(ValueError):
            detector.observe(None, 50)
        with self.assertRaises(ValueError):
            detector.observe(np.empty((0, 0, 3), np.uint8), 50)
        with self.assertRaises(ValueError):
            detector.observe(blank.astype(np.float32), 0.1)
        with self.assertRaises(ValueError):
            detector.observe(blank, float('nan'))
        self.assertTrue(detector.observe(blank, 60))

    def test_current_frame_is_checked_when_timeout_crosses_between_samples(self):
        detector = FrameStallDetector(sample_interval=2)
        detector.observe(self.frame, 0)
        detector.observe(self.frame, 59.5)
        fresh = self.frame.copy()
        fresh[343:350, 605:610] = 230
        self.assertFalse(detector.observe(fresh, 60))
        self.assertEqual(detector.stale_for(60), 0)

    def test_independent_captures_use_roi_without_changing_health_timer(self):
        detector = FrameStallDetector(roi=(0.5, 0.9, 1, 1))
        detector.observe(self.frame, 0)
        outside = self.frame.copy()
        outside[:100] = 255
        self.assertFalse(detector.frames_changed(self.frame, outside))
        changed = self.frame.copy()
        changed[343:350, 605:610] = 230
        self.assertTrue(detector.frames_changed(self.frame, changed))
        self.assertEqual(detector.stale_for(59), 59)
        self.assertFalse(detector.suspected)
        self.assertTrue(detector.observe(self.frame, 60))

    def test_independent_captures_validate_both_and_accept_shape_change(self):
        detector = FrameStallDetector()
        self.assertTrue(detector.frames_changed(self.frame, self.frame[:180]))
        for first, second in ((None, self.frame), (self.frame, None),
                              (self.frame, self.frame.astype(np.float32))):
            with self.subTest(first=type(first).__name__, second=type(second).__name__):
                with self.assertRaises(ValueError):
                    detector.frames_changed(first, second)
        self.assertEqual(detector.stale_for(100), 0)
        self.assertFalse(detector.suspected)

    def test_invalid_configuration_is_rejected(self):
        for timeout in (0, 14.9, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                FrameStallDetector(timeout_seconds=timeout)
        for roi in ((0, 0, 1), (0, 0, 0, 1), (-0.1, 0, 1, 1), (0, 0, 1, 2),
                    (0, float('nan'), 1, 1)):
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                FrameStallDetector(roi=roi)
        with self.assertRaises(ValueError):
            FrameStallDetector(sample_interval=0)


if __name__ == '__main__':
    unittest.main()
