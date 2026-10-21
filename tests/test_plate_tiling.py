"""Tiled plate scans preserve geometry and count one physical observation."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plate_recognition import detect_plate_observations, PlateConsensus


def prediction(text, box, score=0.8, detector=0.8):
    return SimpleNamespace(
        detection=SimpleNamespace(bounding_box=SimpleNamespace(
            x1=box[0], y1=box[1], x2=box[2], y2=box[3]), confidence=detector),
        ocr=SimpleNamespace(text=text, confidence=[score] * len(text)))


def model(results):
    return SimpleNamespace(predict=Mock(side_effect=results),
                           ocr=SimpleNamespace(predict=Mock(return_value=None)))


class PlateTilingTests(unittest.TestCase):
    def test_small_source_uses_only_one_full_scan(self):
        alpr = model([[prediction("AB1234", [100, 80, 160, 100])]])
        frame = np.zeros((540, 960, 3), np.uint8)
        observed = detect_plate_observations(alpr, frame)
        self.assertEqual(alpr.predict.call_count, 1)
        self.assertEqual(observed[0]["bbox"], [100, 80, 160, 100])
        self.assertEqual(observed[0]["variants"][0]["source_region"], [0, 0, 960, 540])

    def test_native_source_finds_plate_in_tile_after_full_scan_misses(self):
        alpr = model([[], [], [prediction("AB1234", [194, 184, 247, 207])], [], []])
        observed = detect_plate_observations(alpr, np.zeros((900, 1600, 3), np.uint8))
        self.assertEqual(alpr.predict.call_count, 5)
        self.assertEqual([call.args[0].shape[:2] for call in alpr.predict.call_args_list],
                         [(900, 1600)] + [(540, 960)] * 4)
        self.assertEqual(observed[0]["bbox"], [834, 184, 887, 207])
        self.assertEqual(observed[0]["selected"]["raw_text"], "AB1234")

    def test_overlapping_views_merge_to_one_vote_and_retain_other_readings(self):
        alpr = model([[prediction("AB1234", [900, 200, 950, 220], 0.75)],
                      [prediction("AB1234", [901, 201, 952, 222], 0.8)],
                      [prediction("ZX9876", [260, 200, 310, 220], 0.95)], [], []])
        observed = detect_plate_observations(alpr, np.zeros((900, 1600, 3), np.uint8))
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["selected"]["raw_text"], "ZX9876")
        self.assertEqual(len(observed[0]["variants"]), 3)
        self.assertEqual({tuple(v["source_region"]) for v in observed[0]["variants"]},
                         {(0, 0, 1600, 900), (0, 0, 960, 540), (640, 0, 1600, 540)})
        consensus = PlateConsensus(["AB1234"])
        for item in observed:
            self.assertIsNone(consensus.observe(item["selected"]["raw_text"],
                item["selected"]["confidence"], 100, "one-frame", item["bbox"],
                item["detector_confidence"], item["variants"]))

    def test_two_distinct_physical_plates_are_not_merged(self):
        alpr = model([[prediction("AB1234", [100, 100, 160, 120]),
                       prediction("CD5678", [170, 100, 230, 120])], [], [], [], []])
        observed = detect_plate_observations(alpr, np.zeros((900, 1600, 3), np.uint8))
        self.assertEqual(len(observed), 2)
        self.assertEqual({item["selected"]["raw_text"] for item in observed}, {"AB1234", "CD5678"})

    def test_low_detector_confidence_does_not_suppress_usable_plate(self):
        alpr = model([[prediction("AB1234", [900, 200, 950, 220], 0.8, 0.7)],
                      [prediction("ZX9876", [900, 200, 950, 220], 0.99, 0.3)], [], [], []])
        observed = detect_plate_observations(alpr, np.zeros((900, 1600, 3), np.uint8))
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["selected"]["raw_text"], "AB1234")
        self.assertEqual(observed[0]["detector_confidence"], 0.7)

    def test_tile_boxes_are_clipped_and_translated_to_original_source(self):
        alpr = model([[], [], [], [], [prediction("AB1234", [-10, -10, 100, 30]),
                                      prediction("CD5678", [1000, 600, 1100, 650])]])
        observed = detect_plate_observations(alpr, np.zeros((900, 1600, 3), np.uint8))
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["bbox"], [640, 360, 740, 390])


if __name__ == "__main__":
    unittest.main()
