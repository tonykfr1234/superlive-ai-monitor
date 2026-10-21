"""Synthetic plates only; all Telegram HTTP is mocked."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from preview_ui import PreviewController, PreviewLayout, plate_rows, render_preview, status_lines
from record_protocol import atomic_json, read_json
import telegram_video


def event(number=1, timestamp=1000, **extra):
    return dict(id="synthetic-%s" % number, timestamp=timestamp, text="ZX1235", raw_text="ZX-1235",
                matched="ZX1234", distance=1, confirmed=True, **extra)


class PlatePreviewTests(unittest.TestCase):
    def test_panel_keeps_video_proportional_and_excludes_mouse_roi(self):
        for size in ((1100, 760), (799, 480), (640, 480), (320, 300)):
            layout = PreviewLayout.create(1920, 1080, *size, show_plates=True)
            self.assertAlmostEqual(layout.video.width/layout.video.height, 1920/1080, delta=.04)
            self.assertLessEqual(layout.plates.y+layout.plates.height, layout.footer.y)
            self.assertIsNone(layout.image_point(layout.plates.x+5, layout.plates.y+5))
            self.assertLessEqual(layout.video.y+layout.video.height, layout.footer.y)

    def test_recent_five_sorted_and_observation_never_replaced_with_match(self):
        rows = plate_rows([event(index, 1000+index) for index in range(8)])
        self.assertEqual(len(rows), 5)
        self.assertIn("08:16:47", rows[0][0])
        self.assertIn("ZX1235", rows[0][0])
        self.assertNotIn("ZX1234", rows[0][0])
        self.assertIn("ZX1234", rows[0][1])
        self.assertIn("1", rows[0][1])
        self.assertEqual(plate_rows([dict(event(), confirmed=False)]), [])

    def test_model_is_visible_and_render_preserves_evidence(self):
        import numpy as np
        controller = PreviewController()
        controller.layout = PreviewLayout.create(640, 360, show_plates=True)
        controller.plate_events = [event()]
        controller.plate_status = "Ready"
        self.assertIn("YOLO11n", " ".join(status_lines(controller, "IDLE", 1, 25)))
        frame = np.full((360, 640, 3), 25, np.uint8)
        original = frame.copy()
        rendered = render_preview(frame, controller, "IDLE", 1, 25)
        self.assertTrue(np.array_equal(frame, original))
        self.assertEqual(rendered.shape, (720, 960, 3))


class PlateTelegramTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = Mock()
        self.queue = telegram_video.TelegramVideoQueue(self.root, self.log)
        for patcher in (patch.object(telegram_video, "load_credentials", return_value=("fake", "fake")),
                        patch.object(telegram_video.time, "time", return_value=1000)):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("requests.post", return_value=Mock(status_code=200, json=lambda: {"ok": True}))
        self.http = patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, data):
        atomic_json(self.root / "runtime" / "plate_events" / (data["id"]+".json"), data)

    def quiet(self, until):
        atomic_json(self.root / "runtime" / "telegram_quiet.json", {"request_id": "synthetic-clip", "quiet_until": until})

    def test_plate_delivers_while_idle_once_across_restart_with_original_and_match(self):
        self.save(event())
        self.queue.process_once(now=1000)
        self.http.assert_called_once()
        text = self.http.call_args.kwargs["data"]["text"]
        self.assertIn("08:16:40", text)
        self.assertIn("辨識：ZX1235", text)
        self.assertIn("名單：ZX1234", text)
        self.assertIn("差 1 碼", text)
        restarted = telegram_video.TelegramVideoQueue(self.root, self.log)
        restarted.process_once(now=1100)
        self.http.assert_called_once()

    def test_plate_quiet_then_sends_and_direct_dispatch_rechecks(self):
        self.save(event())
        self.quiet(1120)
        self.queue.process_once(now=1000)
        self.http.assert_not_called()
        with self.assertRaises(telegram_video.TelegramDeferred):
            self.queue.send_plate(event(), "fake", "fake")
        self.quiet(1000)
        self.queue.process_once(now=1000)
        self.http.assert_called_once()

    def test_unconfirmed_and_malformed_events_do_not_block_valid_event(self):
        self.save(dict(event(1), confirmed=False))
        self.save(dict(event(2), timestamp="bad"))
        self.save(event(3))
        self.queue.process_once(now=1000)
        self.http.assert_called_once()

    def test_plate_retry_is_persisted_without_secret_exception_text(self):
        self.save(event())
        self.http.side_effect = RuntimeError("secret-token-in-url")
        self.queue.process_once(now=1000)
        state = read_json(next(self.queue.plate_delivery.glob("*.json")))
        self.assertEqual(state["attempts"], 1)
        self.assertEqual(state["retry_at"], 1020)
        self.assertNotIn("secret-token", str(self.log.call_args_list))
        self.queue.process_once(now=1001)
        self.http.assert_called_once()

    def test_caption_includes_only_clip_interval_and_is_bounded(self):
        self.save(event(0, 899))
        self.save(event(1, 950))
        self.save(event(2, 1201))
        job = dict(duration=300, complete=True, created_at=1200, clip_started_at=900)
        caption = self.queue.caption(job)
        self.assertIn("08:15:50", caption)
        self.assertNotIn("08:14:59", caption)
        self.assertNotIn("08:20:01", caption)
        for index in range(3, 90):
            self.save(event(index, 950+index))
        caption = self.queue.caption(job)
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("另有", caption)

    def test_video_cleanup_failure_does_not_resend_after_restart(self):
        path = self.root / "synthetic.mp4"
        path.write_bytes(b"synthetic video")
        manifest = self.queue.enqueue(path, {}, 180, True)
        self.queue.cleanup_delivery_copy = Mock(return_value=False)
        self.queue.process_once(now=1000)
        self.assertEqual(read_json(manifest)["sent_at"], 1000)
        self.http.assert_called_once()
        restarted = telegram_video.TelegramVideoQueue(self.root, self.log)
        restarted.process_once(now=1001)
        self.http.assert_called_once()
        self.assertFalse(manifest.exists())
        self.assertTrue(path.exists())

    def test_shutdown_defers_plate_and_video(self):
        self.save(event())
        self.queue.stop_event.set()
        self.queue.process_once(now=1000)
        self.http.assert_not_called()
        with self.assertRaises(telegram_video.TelegramDeferred):
            self.queue.send_plate(event(), "fake", "fake")


if __name__ == "__main__":
    unittest.main()
