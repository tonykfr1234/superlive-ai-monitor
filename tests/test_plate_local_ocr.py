"""Local Taiwan OCR fine-tune: used only when both model and config exist."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import plate_recognition as pr


class LocalOcrModelTests(unittest.TestCase):
    def test_both_files_select_local_model(self):
        with tempfile.TemporaryDirectory() as root:
            models = Path(root) / "models"; models.mkdir()
            (models / pr.LOCAL_OCR_NAME).write_bytes(b"onnx")
            (models / pr.LOCAL_OCR_CONFIG).write_text("alphabet: x", encoding="utf-8")
            self.assertEqual(pr.local_ocr_model(root), (str(models / pr.LOCAL_OCR_NAME),
                                                        str(models / pr.LOCAL_OCR_CONFIG)))

    def test_missing_file_falls_back_to_hub_model(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(pr.local_ocr_model(root))
            models = Path(root) / "models"; models.mkdir()
            (models / pr.LOCAL_OCR_NAME).write_bytes(b"onnx")
            self.assertIsNone(pr.local_ocr_model(root))


if __name__ == "__main__":
    unittest.main()
