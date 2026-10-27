from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from plate_recognition import enhanced_plate_crops, recognition_candidates, PlateConsensus


class PlateEnhancementTests(unittest.TestCase):
    def crop(self):
        image = np.full((18, 50, 3), 180, dtype=np.uint8)
        image[4:14, 5:45:5] = 30
        return image

    def test_bounded_processing_preserves_original_pixels(self):
        crop = self.crop()
        before = crop.copy()
        outputs = enhanced_plate_crops(crop)
        self.assertEqual([name for name, _ in outputs], ['lanczos3','denoise','contrast','unsharp'])
        np.testing.assert_array_equal(crop, before)
        self.assertTrue(all(image.dtype == np.uint8 and image.shape[2] == 3 for _, image in outputs))
        self.assertTrue(all(image.shape[0] <= 192 and image.shape[1] <= 1152 for _, image in outputs))
        self.assertEqual(enhanced_plate_crops(np.zeros((0, 50, 3), np.uint8)), [])
        self.assertEqual(enhanced_plate_crops(np.zeros((100, 500, 3), np.uint8)), [])

    def test_one_failed_variant_preserves_original_and_other_results(self):
        read = lambda text, score: SimpleNamespace(text=text, confidence=[score]*len(text))
        ocr = SimpleNamespace(predict=Mock(side_effect=[ValueError('fixture'), read('AB1234', .8),
                                                       read('AB1234', .95), read('AB1234', .94)]))
        prediction = SimpleNamespace(ocr=read('AX1234', .7), detection=SimpleNamespace(
            bounding_box=SimpleNamespace(x1=0, y1=0, x2=50, y2=18)))
        selected, variants = recognition_candidates(SimpleNamespace(ocr=ocr), self.crop(), prediction)
        self.assertEqual(selected['variant'], 'contrast')
        self.assertEqual(variants[0]['raw_text'], 'AX1234')
        self.assertEqual(len(variants), 4)
        consensus = PlateConsensus([])
        self.assertIsNone(consensus.observe(selected['raw_text'], selected['confidence'], 100,
            'one-frame', [0, 0, 50, 18], .9, variants))


if __name__ == '__main__':
    unittest.main()
