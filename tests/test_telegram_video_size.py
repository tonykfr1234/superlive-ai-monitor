"""Telegram receives the clip's real dimensions so it is shown full-frame."""
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from telegram_video import TelegramVideoQueue


class VideoSizeTests(unittest.TestCase):
    def test_reports_encoded_width_and_height(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 36))
            for _ in range(3):
                writer.write(np.zeros((36, 64, 3), np.uint8))
            writer.release()
            self.assertEqual(TelegramVideoQueue.video_size(path), {"width": 64, "height": 36})

    def test_unreadable_video_omits_dimensions(self):
        self.assertEqual(TelegramVideoQueue.video_size(Path("missing-clip.mp4")), {})


if __name__ == "__main__":
    unittest.main()
