import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hq_video import HighQualityWriter
from telegram_video import TelegramVideoQueue, MAX_UPLOAD_BYTES


class HighQualityTests(unittest.TestCase):
    def test_real_h264_preserves_dimensions_duration_and_details(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hq.mp4"
            frame = np.zeros((360, 640, 3), np.uint8)
            cv2.putText(frame, "SuperLive 2026 HD", (30,180), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
            writer = HighQualityWriter(path, 10, (640,360))
            self.assertTrue(writer.isOpened())
            for _ in range(20):
                writer.write(frame)
            writer.release()
            writer.release()
            capture = cv2.VideoCapture(str(path))
            count, fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
            codec = int(capture.get(cv2.CAP_PROP_FOURCC))
            ok, decoded = capture.read()
            capture.release()
            self.assertEqual(count, 20)
            self.assertEqual(fps, 10)
            self.assertIn(codec, [cv2.VideoWriter_fourcc(*name) for name in ("avc1", "h264", "H264")])
            self.assertTrue(ok)
            self.assertEqual(decoded.shape, frame.shape)
            self.assertLess(np.mean(np.abs(decoded.astype(float)-frame)), 2)
            queue = TelegramVideoQueue(folder)
            prepared = queue.prepare(dict(path=str(path), prepared_path=str(Path(folder)/'delivery.mp4'), duration=2))
            self.assertEqual(prepared, path)
            self.assertFalse((Path(folder)/"delivery.mp4").exists())
            capture = cv2.VideoCapture(str(prepared))
            width, height = capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            capture.release()
            self.assertEqual((width,height), (640,360))

    def test_oversized_delivery_uses_budget_and_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root/'source.mp4'
            with original.open('wb') as stream:
                stream.truncate(MAX_UPLOAD_BYTES+1)
            queue = TelegramVideoQueue(root)
            settings=[]
            def encode(job, temporary, quality):
                settings.append(quality)
                with temporary.open('wb') as stream:
                    stream.truncate(MAX_UPLOAD_BYTES+1 if len(settings)==1 else 10)
            with patch.object(queue, 'encode', side_effect=encode):
                queue.prepare(dict(path=str(original), prepared_path=str(root/'out.mp4'), duration=300))
            self.assertEqual(settings[0], ['-crf','20'])
            self.assertIn('-b:v', settings[1])
            self.assertEqual(original.stat().st_size, MAX_UPLOAD_BYTES+1)
