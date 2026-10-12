"""Optional real H.264 encode: synthetic frames only, no camera or Telegram."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from telegram_video import MAX_UPLOAD_BYTES, TelegramVideoQueue

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('cv2', 'numpy', 'imageio_ffmpeg'))

@unittest.skipUnless(AVAILABLE, 'Real encoder check needs OpenCV, numpy and imageio-ffmpeg')
class RealEncoderTests(unittest.TestCase):
    def test_full_five_minute_video_becomes_playable_h264(self):
        import cv2
        import numpy as np
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            original = root / 'synthetic.mp4'
            prepared = root / 'synthetic_telegram.mp4'
            writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320, 240))
            self.assertTrue(writer.isOpened())
            try:
                for index in range(3000):
                    frame = np.zeros((240, 320, 3), np.uint8)
                    frame[:, :, index % 3] = 60 + index % 190
                    cv2.rectangle(frame, (index % 220, 60), (index % 220 + 50, 190), (220, 220, 220), -1)
                    writer.write(frame)
            finally:
                writer.release()
            queue = TelegramVideoQueue(root, lambda message: None)
            output = queue.prepare({'path': str(original), 'prepared_path': str(prepared)})
            self.assertGreater(output.stat().st_size, 0)
            self.assertLessEqual(output.stat().st_size, MAX_UPLOAD_BYTES)
            video = cv2.VideoCapture(str(output))
            try:
                self.assertTrue(video.isOpened())
                self.assertEqual(int(video.get(cv2.CAP_PROP_FRAME_COUNT)), 3000)
                self.assertAlmostEqual(video.get(cv2.CAP_PROP_FPS), 10, places=2)
                self.assertTrue(video.read()[0])
            finally:
                video.release()
            probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-i', str(output)], capture_output=True, text=True)
            self.assertIn('Video: h264', probe.stderr)

if __name__ == '__main__':
    unittest.main()
