"""Plate matching/consensus tests with fictional plates and no model or network."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plate_recognition import (PlateConsensus, PlateService, character_confidence,
                               edit_distance, load_allowlist, match_plate,
                               normalize_plate, recognition_candidates, valid_sample)


class PlateMatchingTests(unittest.TestCase):
    def test_normalization_only_removes_formatting(self):
        self.assertEqual(normalize_plate(" ab-1234 "), "AB1234")
        self.assertNotEqual(normalize_plate("O0I1"), "0011")

    def test_two_digits_or_letters_or_mixed_errors_are_allowed(self):
        for observed in ("AB1299", "XY1234", "AX1239", "AB14", "AB991234"):
            with self.subTest(observed=observed):
                result = match_plate(observed, ["AB-1234"])
                self.assertEqual(result["matched"], "AB1234")
                self.assertEqual(result["distance"], 2)
                self.assertEqual(result["text"], observed)

    def test_three_errors_fail_and_too_short_does_not_match(self):
        self.assertIsNone(match_plate("XYZ234", ["AB1234"])["matched"])
        self.assertEqual(match_plate("A14", ["AB1234"])["match_status"], "insufficient")

    def test_ambiguity_requires_review_and_exact_match_wins(self):
        result = match_plate("AB1234", ["AB1235", "AB1233"])
        self.assertEqual(result["match_status"], "ambiguous")
        self.assertIsNone(result["matched"])
        self.assertEqual(result["candidates"], ["AB1233", "AB1235"])
        self.assertEqual(match_plate("AB1234", ["AB1234", "AB1235"])["match_status"], "exact")

    def test_empty_allowlist_does_not_invent_target(self):
        self.assertIsNone(match_plate("AB1234", [])["matched"])
        self.assertEqual(edit_distance("AB1234", "A123"), 2)

    def test_padding_probabilities_cannot_rescue_low_ocr_confidence(self):
        self.assertAlmostEqual(character_confidence("AB1234", [0.4] * 6 + [1.0] * 4), 0.4)
        self.assertAlmostEqual(character_confidence("AB1234____", [0.8] * 6 + [0.1] * 4), 0.8)
        self.assertEqual(character_confidence("AB1234", [0.9] * 5), 0)
        self.assertEqual(character_confidence("AB1234", [float("nan")] * 10), 0)


class PlateConsensusTests(unittest.TestCase):
    def make(self):
        return PlateConsensus(["AB1234"])

    def test_three_samples_confirm_and_preserve_actual_fuzzy_reading(self):
        tracker = self.make()
        self.assertIsNone(tracker.observe("AX1239", 0.9, 100, "a"))
        self.assertIsNone(tracker.observe("AX1239", 0.9, 100.5, "b"))
        event = tracker.observe("AX1239", 0.9, 101, "c")
        self.assertEqual(event["text"], "AX1239")
        self.assertEqual(event["matched"], "AB1234")
        self.assertEqual(event["distance"], 2)
        self.assertEqual(len(event["evidence"]), 3)
        self.assertTrue(event["confirmed"])
        self.assertTrue(event["time"].endswith("+08:00"))

    def test_duplicate_frame_cannot_provide_three_votes(self):
        tracker = self.make()
        for now in (100, 101, 102, 103):
            self.assertIsNone(tracker.observe("AB1234", 0.9, now, "same-frame"))

    def test_low_confidence_or_no_text_never_confirms(self):
        tracker = self.make()
        for i in range(12):
            self.assertIsNone(tracker.observe("AB1234", 0.71, i, str(i)))
            self.assertIsNone(tracker.observe("", 1.0, i, str(i)))
            self.assertIsNone(tracker.observe("AB1234", 1.0, i, str(i), detector_confidence=0.39))

    def test_expired_evidence_and_pause_reset_do_not_confirm(self):
        tracker = self.make()
        tracker.observe("AB1234", 0.9, 100, "a")
        tracker.observe("AB1234", 0.9, 101, "b")
        self.assertIsNone(tracker.observe("AB1234", 0.9, 110, "c"))
        tracker.reset()
        self.assertIsNone(tracker.observe("AB1234", 0.9, 111, "d"))
        self.assertIsNone(tracker.observe("AB1234", 0.9, 112, "e"))

    def test_debounce_sixty_seconds_and_reconfirm_after_absence(self):
        tracker = self.make()
        for i in range(3):
            event = tracker.observe("AB1234", 0.9, 100 + i, str(i))
        self.assertIsNotNone(event)
        for i in range(3, 8):
            self.assertIsNone(tracker.observe("AB1234", 0.9, 100 + i, str(i)))
        for i in range(3):
            event = tracker.observe("AB1234", 0.9, 162 + i, "later" + str(i))
        self.assertIsNotNone(event)

    def test_ambiguous_result_retains_observation_without_picking_target(self):
        tracker = PlateConsensus(["AB1233", "AB1235"])
        for i in range(3):
            event = tracker.observe("AB1234", 0.9, i, str(i))
        self.assertEqual(event["text"], "AB1234")
        self.assertEqual(event["match_status"], "ambiguous")
        self.assertIsNone(event["matched"])


class PlateIsolationTests(unittest.TestCase):
    def test_missing_environment_degrades_without_importing_fast_alpr(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PlateService(directory)
            self.assertFalse(service.start())
            self.assertEqual(service.snapshot()["status"], "UNAVAILABLE")
            self.assertFalse(service.submit(None))
            self.assertEqual(service.recent(), [])
            service.stop()

    def test_only_local_allowlist_is_loaded_and_default_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(load_allowlist(root), [])
            (root / "config").mkdir()
            (root / "config" / "plate_allowlist.json").write_text(
                json.dumps(["ab-1234", "AB1234", "?", 123]), encoding="utf-8")
            self.assertEqual(load_allowlist(root), ["AB1234"])

    def test_stale_or_pre_pause_frames_are_rejected(self):
        control = {"active": True, "session": "s", "generation": 2, "heartbeat": 100}
        sample = {"session": "s", "generation": 2, "timestamp": 100}
        self.assertTrue(valid_sample(sample, control, 101))
        self.assertFalse(valid_sample(sample, control, 109))
        self.assertFalse(valid_sample(sample, dict(control, active=False), 101))
        self.assertFalse(valid_sample(sample, dict(control, generation=3), 101))
        self.assertFalse(valid_sample(sample, dict(control, session="new"), 101))
        self.assertFalse(valid_sample(sample, control, 99))
        self.assertFalse(valid_sample({}, {}, 101))

    def test_crop_variant_uses_confidence_and_retains_both_observations(self):
        import numpy as np
        prediction = SimpleNamespace(
            ocr=SimpleNamespace(text="AX1239", confidence=[0.91] * 6 + [1] * 4),
            detection=SimpleNamespace(bounding_box=SimpleNamespace(x1=5, y1=5, x2=45, y2=20)))
        ocr = SimpleNamespace(predict=lambda frame: SimpleNamespace(text="AB1234", confidence=[0.85] * 10))
        selected, variants = recognition_candidates(SimpleNamespace(ocr=ocr), np.zeros((40, 80, 3), dtype=np.uint8), prediction)
        # Even when a different variant happens to resemble a target, confidence
        # selects the actual observation. The matcher runs only afterwards.
        self.assertEqual(selected["raw_text"], "AX1239")
        self.assertEqual([item["variant"] for item in variants], ["original", "lanczos3"])
        self.assertEqual([item["raw_text"] for item in variants], ["AX1239", "AB1234"])
        with patch.object(ocr, "predict", side_effect=ValueError("model unavailable")):
            selected, variants = recognition_candidates(SimpleNamespace(ocr=ocr), np.zeros((40, 80, 3), dtype=np.uint8), prediction)
        self.assertEqual(selected["raw_text"], "AX1239")
        self.assertEqual(len(variants), 1)


if __name__ == "__main__":
    unittest.main()
