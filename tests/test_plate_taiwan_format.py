"""Taiwan plate layout validation and layout-forced character repair."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plate_recognition import complete_plate, recognition_candidates, taiwan_plate


class TaiwanFormatTests(unittest.TestCase):
    def test_accepts_current_and_older_layouts(self):
        for text in ("ABC-1234", "AB-1234", "1234-AB", "ABC-123", "123-ABC", "rak 5678"):
            self.assertTrue(complete_plate(text), text)

    def test_rejects_impossible_layouts_and_unissued_letters(self):
        for text in ("1234ABC", "AB123", "A1B2C3", "AIC1234", "ABO1234", "ABCD1234", ""):
            self.assertFalse(complete_plate(text), text)

    def test_repairs_only_slot_forced_confusions(self):
        self.assertEqual(taiwan_plate("A8C-1234"), "ABC1234")
        self.assertEqual(taiwan_plate("ABC-12O4"), "ABC1204")
        self.assertEqual(taiwan_plate("ABC-I234"), "ABC1234")
        self.assertEqual(taiwan_plate("ABC1234"), "ABC1234")

    def test_ambiguous_or_unfixable_readings_stay_rejected(self):
        # An I/O in a letter slot has no unambiguous replacement.
        self.assertIsNone(taiwan_plate("AIC-1234"))
        # Swaps never change the length or invent characters.
        self.assertIsNone(taiwan_plate("ABC-12345"))
        self.assertIsNone(taiwan_plate("ABC-12O4", fix=False))

    def test_candidates_keep_raw_reading_and_prefer_valid_layout(self):
        ocr = SimpleNamespace(text="A8C1234", confidence=[.95] * 7)
        box = SimpleNamespace(x1=0, y1=0, x2=200, y2=60)
        prediction = SimpleNamespace(ocr=ocr, detection=SimpleNamespace(bounding_box=box))
        alpr = SimpleNamespace(ocr=SimpleNamespace(predict=lambda image: None))
        selected, variants = recognition_candidates(alpr, np.zeros((60, 200, 3), np.uint8), prediction)
        self.assertEqual(selected["raw_text"], "ABC1234")
        self.assertEqual(selected["repaired_from"], "A8C1234")
        self.assertIn("A8C1234", [item["raw_text"] for item in variants])


if __name__ == "__main__":
    unittest.main()
