"""Detection boxes in the Telegram copy; dogs as a watched class. Synthetic data only."""
import csv
import io
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import box_overlay
from box_overlay import BoxLog, boxes_at, draw_boxes, load_log, publish_detections, render
from daily_report import DailyReports, HEADER, TAIPEI
from hq_video import HighQualityWriter
from record_protocol import read_json
from region_policy import activity_event_type, activity_label
from telegram_video import TelegramVideoQueue


class BoxLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live = self.root / "live_detections.json"

    def publish(self, stamp, objects):
        publish_detections(self.live, stamp, 1920, 1080, objects)

    def test_each_new_inference_is_logged_once_and_old_ones_are_skipped(self):
        log = BoxLog(self.root / "clip.jsonl", self.live, started_at=100)
        car = [{"box": (100, 200, 400, 500), "class_id": 2, "confidence": .91}]
        for stamp in (90, 100.5, 100.5, 101.5):
            self.publish(stamp, car)
            log.next_poll = 0
            log.poll()
        self.assertEqual([stamp for stamp, _ in load_log(log.path)], [100.5, 101.5])

    def test_boxes_follow_frame_time_and_expire(self):
        entries = [(100.0, {"objects": ["a"]}), (101.0, {"objects": ["b"]})]
        self.assertIsNone(boxes_at(entries, 99.9))
        self.assertEqual(boxes_at(entries, 100.6)["objects"], ["a"])
        self.assertEqual(boxes_at(entries, 101.2)["objects"], ["b"])
        self.assertIsNone(boxes_at(entries, 101.0 + box_overlay.MAX_BOX_AGE + .1))

    def test_boxes_scale_to_the_video_size(self):
        import cv2
        frame = np.zeros((540, 960, 3), np.uint8)
        entry = {"size": [1920, 1080], "objects": [{"box": [400, 400, 800, 900], "class_id": 16, "confidence": .8}]}
        draw_boxes(frame, entry, cv2)
        self.assertEqual(tuple(frame[300, 200]), (255, 0, 255))  # left edge of the dog box, halved
        self.assertEqual(tuple(frame[300, 300]), (0, 0, 0))      # inside stays untouched


class RenderTests(unittest.TestCase):
    def test_copy_has_boxes_and_original_is_unchanged(self):
        import cv2
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            original = folder / "SuperLive_clip.mp4"
            writer = HighQualityWriter(original, 10, (320, 240))
            for _ in range(20):
                writer.write(np.full((240, 320, 3), 90, np.uint8))
            writer.release()
            before = original.read_bytes()
            publish_detections(folder / "live.json", 1000.5, 320, 240,
                               [{"box": (40, 40, 200, 200), "class_id": 0, "confidence": .9}])
            recorder_log = BoxLog(folder / "clip.jsonl", folder / "live.json", started_at=1000)
            recorder_log.poll()
            log = recorder_log.path
            boxed = render(original, folder / "SuperLive_clip_boxes.mp4", log, clip_started_at=1000, fps=10)
            self.assertEqual(original.read_bytes(), before)
            capture = cv2.VideoCapture(str(boxed))
            frames = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(frame)
            capture.release()
            self.assertEqual(len(frames), 20)
            green = lambda frame: frame[120, 41][1] > 200 and frame[120, 41][0] < 80
            self.assertFalse(green(frames[2]))   # 1000.2: before the first detection
            self.assertTrue(green(frames[10]))   # 1001.0: person box drawn


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue = TelegramVideoQueue(self.root, Mock())
        self.video = self.root / "SuperLive_x.mp4"
        self.video.write_bytes(b"synthetic video")

    def job(self, with_log=True):
        log = self.root / "x.jsonl"
        if with_log:
            log.write_text('{"timestamp": 1, "size": [2, 2], "objects": []}\n', encoding="utf-8")
        request = dict(request_id="r", clip_started_at=1000, box_log=str(log), video_fps=10,
                       object_counts=dict(person=1, dog=1))
        return read_json(self.queue.enqueue(self.video, request, 30, True))

    def test_boxed_copy_is_sent_and_kept_beside_the_original(self):
        job = self.job()
        def fake_render(source, destination, *args):
            Path(destination).write_bytes(b"boxed")
            return Path(destination)
        with patch("telegram_video.render", side_effect=fake_render):
            sent = self.queue.prepare(job)
        self.assertEqual(sent.name, "SuperLive_x_boxes.mp4")
        self.assertEqual(self.video.read_bytes(), b"synthetic video")
        self.assertIn(sent.resolve(), self.queue.protected_paths())

    def test_render_failure_or_missing_log_sends_the_original(self):
        with patch("telegram_video.render", side_effect=RuntimeError("decoder")):
            self.assertEqual(self.queue.prepare(self.job()), self.video)
        self.assertEqual(self.queue.prepare(self.job(with_log=False)), self.video)

    def test_caption_names_dogs(self):
        self.assertIn("人物與狗錄影", self.queue.caption(self.job()))
        self.assertEqual(activity_label(dict(dog=2)), "狗")
        self.assertEqual(activity_event_type(dict(dog=1)), "DOG_ACTIVITY")
        self.assertEqual(activity_event_type(dict(person=1, dog=1)), "PERSON_ACTIVITY")


class DailyDogColumnTests(unittest.TestCase):
    def test_dog_count_is_the_last_column(self):
        with tempfile.TemporaryDirectory() as folder:
            stamp = datetime(2026, 11, 6, 12, 0, tzinfo=TAIPEI).timestamp()
            report = DailyReports(folder, Path(folder) / "videos", Mock(), clock=lambda: stamp)
            report.record_trigger(dict(request_id="d", created_at=stamp, count=1, confidence=.8,
                                       object_counts=dict(dog=1)))
            report.generate(datetime(2026, 11, 6).date(), "2300")
            path = next((Path(folder) / "videos").rglob("*.csv"))
            rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
            self.assertEqual(rows[0][-1], "狗數")
            self.assertEqual(HEADER[-1], "狗數")
            self.assertEqual((rows[1][1], rows[1][-1]), ("DOG_ACTIVITY", "1"))


if __name__ == "__main__":
    unittest.main()
