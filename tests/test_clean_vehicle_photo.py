"""Latest-vehicle, plate and event photos never use quality-switch frames."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from latest_vehicle import LatestVehicleTracker, _masked
from plate_recognition import under_controls
from adb_capture import AdbStream

CAR = [dict(class_id=2, box=[40, 30, 120, 90])]


def scene(seed, sharp=True):
    rng = np.random.default_rng(seed)
    frame = np.full((100, 160, 3), 120, np.uint8)
    car = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8) if sharp else np.full((60, 80, 3), 130, np.uint8)
    frame[30:90, 40:120] = car
    return frame


def greyed(frame):
    masked = frame.copy()
    masked[:12] = 51
    masked[82:] = 51
    return masked


class CleanVehiclePhotoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tracker = LatestVehicleTracker(temp.name, now=100)

    def test_greyed_switch_frame_is_never_the_photo(self):
        self.tracker.observe(CAR, 101, (160, 100), frame=greyed(scene(1)))
        self.assertIsNone(self.tracker.image)
        self.assertTrue(_masked(greyed(scene(1))))
        self.assertFalse(_masked(scene(1)))

    def test_frames_during_switch_keep_the_earlier_clean_photo(self):
        clean = scene(1)
        self.tracker.observe(CAR, 101, (160, 100), frame=clean)
        kept = self.tracker.image.copy()
        self.tracker.observe(CAR, 102.5, (160, 100), frame=None)  # controls on screen
        np.testing.assert_array_equal(self.tracker.image, kept)
        self.assertEqual(self.tracker.snapshot()["timestamp"], 102.5)

    def test_high_quality_frame_replaces_and_blurrier_frame_does_not(self):
        self.tracker.observe(CAR, 101, (160, 100), frame=scene(1))
        self.tracker.observe(CAR, 102.5, (160, 100), frame=scene(2, sharp=False))
        self.assertEqual(self.tracker.snapshot()["image_timestamp"], 101)
        self.tracker.observe(CAR, 104, (160, 100), frame=scene(3, sharp=False), high_quality=True)
        self.assertEqual(self.tracker.snapshot()["image_timestamp"], 104)
        self.tracker.observe(CAR, 105.5, (160, 100), frame=scene(4))  # sharper, but low quality
        self.assertEqual(self.tracker.snapshot()["image_timestamp"], 104)


class ControlRegionTests(unittest.TestCase):
    def stream(self, busy=False, mask_until=0.0, masks=()):
        stream = AdbStream.__new__(AdbStream)
        stream._quality_busy = threading.Event()
        if busy:
            stream._quality_busy.set()
        stream._quality_mask_until = mask_until
        stream._quality_masks = list(masks)
        stream._quality_applied = "high"
        return stream

    def test_regions_only_while_switching_or_toolbar_hiding(self):
        self.assertEqual(self.stream().control_regions(1920, 1080), [])
        regions = self.stream(busy=True, masks=[(1500, 300, 1700, 400)]).control_regions(1920, 1080)
        self.assertIn([0, 0, 1920, 129], regions)
        self.assertIn([0, 885, 1920, 1080], regions)
        self.assertIn([1488, 288, 1712, 412], regions)
        self.assertTrue(self.stream(mask_until=time.monotonic() + 5).control_regions(1920, 1080))
        self.assertFalse(self.stream(busy=True).high_quality_confirmed())
        self.assertTrue(self.stream().high_quality_confirmed())

    def test_masked_ai_frame_matches_regions(self):
        stream = self.stream(busy=True)
        frame = np.full((1080, 1920, 3), 200, np.uint8)
        masked = stream.camera_without_controls(frame)
        self.assertTrue(_masked(masked))
        self.assertEqual(int(masked[500, 900, 0]), 200)

    def test_plate_photo_skipped_only_when_controls_cover_it(self):
        bands = [[0, 0, 1920, 129], [0, 885, 1920, 1080]]
        self.assertTrue(under_controls([800, 900, 900, 940], bands))   # plate in the bottom band
        self.assertTrue(under_controls([800, 850, 900, 880], bands))   # its padded photo reaches the band
        self.assertFalse(under_controls([800, 500, 900, 540], bands))
        self.assertFalse(under_controls([800, 900, 900, 940], []))     # no switch: always saved


if __name__ == "__main__":
    unittest.main()
