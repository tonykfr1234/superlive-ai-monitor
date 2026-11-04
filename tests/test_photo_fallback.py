"""Durable photo fallback when an event cannot produce a video."""
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_protocol import atomic_json, read_json
from recorder import Recorder
from telegram_status import TAIPEI, telegram_delivery_status
from telegram_video import TelegramVideoQueue


class PhotoFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.stamp = datetime(2026, 11, 2, 14, 55, 8, tzinfo=TAIPEI).timestamp()
        self.photo = self.root / "events/2026-11-02/event.jpg"
        self.photo.parent.mkdir(parents=True)
        self.photo.write_bytes(b"synthetic-jpeg")
        self.request = {
            "request_id": "event-1455",
            "created_at": self.stamp,
            "object_counts": {"person": 0, "car": 1, "motorcycle": 0, "bus": 0, "truck": 0},
            "snapshot_path": str(self.photo),
        }
        self.queue = TelegramVideoQueue(self.root, log=lambda _: None, immediate_plates=True)

    def test_photo_fallback_is_durable_deduplicated_and_immediate(self):
        manifest = self.queue.enqueue_photo(
            self.photo, self.request, "ADB frame is stale")
        self.assertEqual(
            manifest,
            self.queue.enqueue_photo(self.photo, self.request, "duplicate"),
        )
        atomic_json(self.root / "runtime/telegram_quiet.json", {
            "request_id": "blocking-video", "quiet_until": self.stamp + 300,
        })
        self.queue.send_photo = Mock()
        with patch("telegram_video.load_credentials", return_value=("fixture", "fixture")):
            self.queue.process_photos_once(now=self.stamp + 1)
        self.queue.send_photo.assert_called_once()
        job = read_json(manifest)
        self.assertEqual(job["sent_at"], self.stamp + 1)
        self.queue.process_photos_once(now=self.stamp + 2)
        self.assertEqual(self.queue.send_photo.call_count, 1)

    def test_photo_caption_explains_failure_without_private_path(self):
        manifest = self.queue.enqueue_photo(
            self.photo, self.request, "ADB frame is stale")
        caption = self.queue.photo_caption(read_json(manifest))
        self.assertIn("錄影失敗，附上事件照片", caption)
        self.assertIn("車輛 1", caption)
        self.assertIn("這是事件當下留下的原始照片", caption)
        self.assertNotIn(str(self.photo), caption)

    def test_send_photo_uses_telegram_photo_endpoint_and_public_content(self):
        manifest = self.queue.enqueue_photo(
            self.photo, self.request, "ADB frame is stale")
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch("requests.post", return_value=response) as post:
            self.queue.send_photo(
                self.photo, read_json(manifest), "fixture-token", "fixture-chat")
        self.assertTrue(post.call_args.args[0].endswith("/sendPhoto"))
        self.assertEqual(post.call_args.kwargs["data"]["chat_id"], "fixture-chat")
        self.assertEqual(
            post.call_args.kwargs["files"]["photo"][2], "image/jpeg")
        self.assertNotIn(str(self.photo), post.call_args.kwargs["data"]["caption"])

    def test_pending_photo_is_shown_in_lower_left_status(self):
        (self.root / "config/telegram.env").write_text(
            "BOT_TOKEN=TEST_FIXTURE_NOT_A_SECRET\nCHAT_ID=TEST_FIXTURE_NOT_A_SECRET\n",
            encoding="ascii",
        )
        manifest = self.queue.enqueue_photo(self.photo, self.request, "RuntimeError")
        text = telegram_delivery_status(self.root, self.stamp, now=self.stamp + 2)
        self.assertIn("錄影失敗，照片已排隊", text)
        job = read_json(manifest)
        job["sent_at"] = self.stamp + 3
        atomic_json(manifest, job)
        text = telegram_delivery_status(self.root, self.stamp, now=self.stamp + 4)
        self.assertIn("照片已推播 14:55:11", text)

    def test_recorder_uses_photo_only_when_no_video_was_queued(self):
        recorder = Recorder.__new__(Recorder)
        recorder.log = Mock()
        recorder.uploader = Mock()
        recorder.last_video_queued = False
        self.assertTrue(recorder.queue_photo_fallback(self.request, "capture failed"))
        recorder.uploader.enqueue_photo.assert_called_once()
        recorder.uploader.reset_mock()
        recorder.last_video_queued = True
        self.assertFalse(recorder.queue_photo_fallback(self.request, "later error"))
        recorder.uploader.enqueue_photo.assert_not_called()

    def test_rejects_photo_outside_event_evidence(self):
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"private")
        with self.assertRaises(ValueError):
            self.queue.enqueue_photo(outside, self.request, "failure")


if __name__ == "__main__":
    unittest.main()
