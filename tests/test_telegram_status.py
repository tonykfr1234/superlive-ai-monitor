"""Lower-left Telegram ETA text using synthetic runtime state only."""
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from preview_ui import PreviewController, PreviewLayout, render_preview
from record_protocol import atomic_json
from telegram_status import TAIPEI, telegram_button_state, telegram_delivery_status


class TelegramStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "runtime").mkdir()
        (self.root / "logs/Stage5C").mkdir(parents=True)
        (self.root / "config/telegram.env").write_text(
            "BOT_TOKEN=TEST_FIXTURE_NOT_A_SECRET\nCHAT_ID=TEST_FIXTURE_NOT_A_SECRET\n",
            encoding="ascii",
        )
        self.event = datetime(2026, 11, 2, 14, 55, 8, tzinfo=TAIPEI).timestamp()

    def test_recording_reports_earliest_and_latest_delivery_times(self):
        atomic_json(self.root / "runtime/recording_state.json", {
            "status": "RECORDING",
            "request_id": "event",
            "trigger_created_at": self.event,
            "clip_started_at": self.event + 1,
            "elapsed_seconds": 30,
        })
        text = telegram_delivery_status(self.root, self.event, now=self.event + 30)
        self.assertIn("最快 14:57:09 後", text)
        text = telegram_delivery_status(self.root, self.event, now=self.event + 130)
        self.assertIn("錄影中", text)
        self.assertIn("最晚 15:00:09", text)

    def test_queue_quiet_and_retry_times_are_visible(self):
        quiet_until = self.event + 120
        atomic_json(self.root / "runtime/telegram_quiet.json", {
            "request_id": "event", "quiet_until": quiet_until,
        })
        atomic_json(self.root / "runtime/telegram_upload_queue/job.json", {
            "trigger_created_at": self.event,
            "clip_started_at": self.event + 1,
            "path": "synthetic.mp4",
            "duration": 120,
            "retry_at": 0,
        })
        text = telegram_delivery_status(self.root, self.event, now=self.event + 60)
        self.assertIn("靜默至 14:57:08", text)
        atomic_json(self.root / "runtime/telegram_upload_queue/job.json", {
            "trigger_created_at": self.event,
            "clip_started_at": self.event + 1,
            "path": "synthetic.mp4",
            "duration": 120,
            "retry_at": self.event + 180,
        })
        (self.root / "runtime/telegram_quiet.json").unlink()
        text = telegram_delivery_status(self.root, self.event, now=self.event + 120)
        self.assertIn("14:58:08 再試", text)

    def test_relevant_recorder_failure_explains_why_nothing_was_queued(self):
        line = "[2026-11-02 14:55:09] EVENT_RECORD_ERROR=RuntimeError DETAIL=ADB frame is stale\n"
        (self.root / "logs/Stage5C/recorder.log").write_text(line, encoding="utf-8")
        text = telegram_delivery_status(self.root, self.event, now=self.event + 60)
        self.assertIn("未排隊", text)
        self.assertIn("ADB 新影格尚未就緒", text)

    def test_unrelated_or_unconfigured_status_does_not_claim_a_queue(self):
        line = "[2026-11-02 13:00:00] EVENT_RECORD_ERROR=RuntimeError DETAIL=old\n"
        (self.root / "logs/Stage5C/recorder.log").write_text(line, encoding="utf-8")
        text = telegram_delivery_status(self.root, self.event, now=self.event + 60)
        self.assertIn("尚未建立可推播的錄影", text)
        (self.root / "config/telegram.env").write_text("BOT_TOKEN=\nCHAT_ID=\n", encoding="ascii")
        self.assertIn("尚未設定", telegram_delivery_status(self.root, self.event))

    def test_settings_button_says_enabled_retrying_or_not_configured(self):
        self.assertEqual(telegram_button_state(self.root), "enabled")
        atomic_json(self.root / "runtime/telegram_upload_queue/sent.json", {"attempts": 2, "sent_at": 1})
        atomic_json(self.root / "runtime/telegram_plate_delivery/late.json", {"local_only": True, "attempts": 1})
        self.assertEqual(telegram_button_state(self.root), "enabled")
        atomic_json(self.root / "runtime/telegram_photo_queue/failing.json", {"attempts": 1, "retry_at": 9})
        self.assertEqual(telegram_button_state(self.root), "retrying")
        (self.root / "config/telegram.env").write_text("BOT_TOKEN=\nCHAT_ID=\n", encoding="ascii")
        self.assertEqual(telegram_button_state(self.root), "not_configured")

    def test_settings_button_label_follows_state(self):
        import numpy as np
        from PIL import ImageDraw
        controller = PreviewController()
        controller.telegram_button = "enabled"
        controller.layout = PreviewLayout.create(1600, 900, 1280, 820, show_plates=True, dashboard=True)
        drawn = []
        original = ImageDraw.Draw

        def capture_draw(image, *args, **kwargs):
            base = original(image, *args, **kwargs)

            class Draw:
                def __getattr__(self, name):
                    return getattr(base, name)

                def text(self, position, value, **options):
                    drawn.append(value)
                    return base.text(position, value, **options)

            return Draw()

        with patch("PIL.ImageDraw.Draw", side_effect=capture_draw):
            render_preview(np.full((900, 1600, 3), 30, np.uint8), controller, "IDLE")
        self.assertIn("Telegram 已啟用中", drawn)

    def test_dashboard_draws_delivery_status_inside_latest_card(self):
        import numpy as np
        from PIL import ImageDraw
        controller = PreviewController()
        controller.latest_vehicle = {"timestamp": self.event}
        controller.telegram_status = "Telegram：最快 14:57:09 後（錄影滿 2 分鐘）"
        controller.layout = PreviewLayout.create(
            1600, 900, 1280, 820, show_plates=True, dashboard=True)
        panel = controller.layout.panels["latest"]
        drawn = []
        original = ImageDraw.Draw

        def capture_draw(image, *args, **kwargs):
            base = original(image, *args, **kwargs)

            class Draw:
                def __getattr__(self, name):
                    return getattr(base, name)

                def text(self, position, value, **options):
                    if options.get("fill") == (245, 202, 111):
                        drawn.append((value, base.textbbox(position, value, font=options["font"])))
                    return base.text(position, value, **options)

            return Draw()

        with patch("PIL.ImageDraw.Draw", side_effect=capture_draw):
            result = render_preview(
                np.full((900, 1600, 3), 30, np.uint8), controller, "IDLE")
        self.assertEqual(result.shape, (820, 1280, 3))
        self.assertEqual(len(drawn), 1)
        self.assertIn("Telegram", drawn[0][0])
        box = drawn[0][1]
        self.assertGreater(box[1], panel.y)
        self.assertLessEqual(box[3], panel.y + panel.height)


if __name__ == "__main__":
    unittest.main()
