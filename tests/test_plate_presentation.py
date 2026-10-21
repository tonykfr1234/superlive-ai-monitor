"""Synthetic plates only; all Telegram HTTP is mocked."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from preview_ui import (PreviewController, PreviewLayout, plate_rows, render_preview,
                        render_plate_panel, status_lines, ui_font, wrap_panel_text, plate_footer_text)
from record_protocol import atomic_json, read_json
import telegram_video


def event(number=1, timestamp=1000, **extra):
    return dict(id="synthetic-%s" % number, timestamp=timestamp, text="ZX1235", raw_text="ZX-1235",
                matched="ZX1234", distance=1, confirmed=True, **extra)


class PlatePreviewTests(unittest.TestCase):
    def panel_text(self, controller):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (controller.layout.width, controller.layout.height))
        base = ImageDraw.Draw(image)
        output = []
        class RecordingDraw:
            def __getattr__(self, name):
                return getattr(base, name)
            def text(self, position, text, **options):
                output.append((position, text, base.textbbox(position, text, font=options["font"])))
                return base.text(position, text, **options)
        font, _ = ui_font(controller.layout.font_size)
        render_plate_panel(RecordingDraw(), controller, font, True)
        return output

    def test_panel_keeps_video_proportional_and_excludes_mouse_roi(self):
        for size in ((1100, 760), (799, 480), (640, 480), (320, 300)):
            layout = PreviewLayout.create(1920, 1080, *size, show_plates=True, plates_open=True)
            self.assertAlmostEqual(layout.video.width/layout.video.height, 1920/1080, delta=.04)
            self.assertLessEqual(layout.plates.y+layout.plates.height, layout.footer.y)
            self.assertIsNone(layout.image_point(layout.plates.x+5, layout.plates.y+5))
            self.assertLessEqual(layout.video.y+layout.video.height, layout.footer.y)

    def test_all_history_sorted_and_observation_never_replaced_with_match(self):
        rows = plate_rows([event(index, 1000+index) for index in range(8)])
        self.assertEqual(len(rows), 8)
        self.assertIn("08:16:47", rows[0][0])
        self.assertIn("ZX1235", rows[0][0])
        self.assertNotIn("ZX1234", rows[0][0])
        self.assertIn("ZX1234", rows[0][1])
        self.assertIn("1", rows[0][1])
        self.assertEqual(plate_rows([dict(event(), confirmed=False)]), [])

    def test_model_is_visible_and_render_preserves_evidence(self):
        import numpy as np
        controller = PreviewController()
        controller.layout = PreviewLayout.create(640, 360, show_plates=True, plates_open=True)
        controller.plate_events = [event()]
        controller.plate_status = "Ready"
        self.assertIn("YOLO11n", " ".join(status_lines(controller, "IDLE", 1, 25)))
        frame = np.full((360, 640, 3), 25, np.uint8)
        original = frame.copy()
        rendered = render_preview(frame, controller, "IDLE", 1, 25)
        self.assertTrue(np.array_equal(frame, original))
        self.assertEqual(rendered.shape, (720, 960, 3))

    def test_scrollable_status_and_plates_never_overlap_footer_or_cut_numbers(self):
        for size in ((1100, 760), (800, 480), (799, 480), (640, 480), (320, 300)):
            with self.subTest(size=size):
                controller = PreviewController()
                controller.plate_status = "ERROR | ADB 1600x900 | 最高（已確認）"
                controller.plate_error = "辨識程序未回應，正在重新啟動；全畫面人物與車牌持續監看。"
                controller.layout = PreviewLayout.create(1600, 900, *size, show_plates=True, plates_open=True,
                                                         plate_status_text=plate_footer_text(controller))
                controller.plate_events = [dict(event(i, 1000+i), text="ZX%04d" % i,
                                                 matched="AB%04d" % i) for i in range(5)]
                output = self.panel_text(controller)
                all_text = "".join(controller.layout.plate_status_lines)
                for part in ("車牌辨識錯誤", "ADB 1600x900", "最高（已確認）", controller.plate_error):
                    self.assertIn(part, all_text)
                    self.assertNotIn(part, "".join(text for _, text, _ in output))
                panel = controller.layout.plates
                pages = [output]
                while controller.plate_scroll < controller.plate_scroll_max:
                    controller.key("page_down")
                    pages.append(self.panel_text(controller))
                all_text = "".join(text for page in pages for _, text, _ in page)
                for i in range(5):
                    self.assertIn("ZX%04d" % i, all_text)
                    self.assertIn("AB%04d" % i, all_text)
                self.assertNotIn("…", all_text)
                for page in pages:
                    previous_bottom = panel.y
                    for _, _, box in page:
                        self.assertGreaterEqual(box[0], panel.x)
                        self.assertLessEqual(box[2], panel.x+panel.width-20)
                        self.assertGreaterEqual(box[1], previous_bottom)
                        self.assertLessEqual(box[3], panel.y+panel.height)
                        previous_bottom = box[3]

    def test_replay_outcomes_keep_filename_and_no_fake_plate_number(self):
        samples = [dict(event(), source="recording_replay", source_video="synthetic.mp4")]
        for index, outcome in enumerate(("no_plate", "error", "insufficient")):
            samples.append(dict(id=str(index), timestamp=1100+index, confirmed=False, outcome=outcome,
                                source="recording_replay", source_video="private/folder/sample-%d.mp4" % index,
                                error="Synthetic decoder failure" if outcome == "error" else ""))
        rows = plate_rows(samples)
        self.assertEqual(len(rows), 4)
        for row, outcome in zip(rows, ("有候選但未確認", "辨識失敗", "未辨識到車牌")):
            self.assertIn(outcome, row[0])
            self.assertNotIn("ZX1235", row[0])
            self.assertIn("原片補辨識", row[1])
            self.assertNotIn("private", row[1])
        self.assertIn("sample-1.mp4", rows[1][1])
        self.assertIn("Synthetic decoder failure", rows[1][1])
        self.assertIn("ZX1235", rows[-1][0])
        self.assertIn("原片補辨識：synthetic.mp4", rows[-1][1])
        live = dict(timestamp=1200, confirmed=False, outcome="no_plate", source="live_recording",
                    source_video="private/folder/live-synthetic.mp4")
        live_row = plate_rows([live])[0]
        self.assertIn("未辨識到車牌", live_row[0])
        self.assertEqual(live_row[1], "錄影片段：live-synthetic.mp4")

    def test_wheel_page_keys_and_scrollbar_reach_all_history_without_editing_roi(self):
        controller = PreviewController()
        controller.layout = PreviewLayout.create(1600, 900, 800, 480, show_plates=True, plates_open=True)
        controller.plate_events = [dict(event(i, 1000+i), text="ZX%04d" % i) for i in range(60)]
        controller.command("select")
        self.panel_text(controller)
        panel = controller.layout.plates
        x, y = panel.x+30, panel.y+40
        self.assertGreater(controller.plate_scroll_max, controller.plate_page_lines)
        controller.mouse("wheel_down", x, y)
        self.assertEqual(controller.plate_scroll, 3)
        controller.mouse("wheel_up", x, y)
        self.assertEqual(controller.plate_scroll, 0)
        controller.mouse("wheel_down", 0, 0)
        self.assertEqual(controller.plate_scroll, 0)
        controller.key(0x220000)
        self.assertEqual(controller.plate_scroll, controller.plate_page_lines)
        controller.key(0x210000)
        self.assertEqual(controller.plate_scroll, 0)
        track = controller.plate_scrollbar
        controller.mouse("down", track.x+3, track.y+track.height-1)
        controller.mouse("up", track.x+3, track.y+track.height-1)
        self.assertEqual(controller.plate_scroll, controller.plate_scroll_max)
        self.assertIn("ZX0000", "".join(text for _, text, _ in self.panel_text(controller)))
        thumb = controller.plate_scroll_thumb
        self.assertEqual(thumb.y+thumb.height, track.y+track.height)
        controller.mouse("down", track.x+3, track.y+track.height-1)
        controller.mouse("move", track.x+3, track.y)
        controller.mouse("up", track.x+3, track.y)
        self.assertEqual(controller.plate_scroll, 0)
        self.assertEqual(controller.draft, [])
        self.assertTrue(controller.selecting)
        self.assertIsNone(controller.pressed_button)

    def test_history_update_and_resize_clamp_scroll_and_refresh_cache(self):
        controller = PreviewController()
        controller.layout = PreviewLayout.create(1600, 900, 800, 480, show_plates=True, plates_open=True)
        controller.plate_events = [event(i, 1000+i) for i in range(30)]
        self.panel_text(controller)
        controller.key("end")
        self.assertGreater(controller.plate_scroll, 0)
        controller.plate_events = [dict(event(), text="NEW123")]
        controller.layout = PreviewLayout.create(1600, 900, 1100, 900, show_plates=True, plates_open=True)
        text = "".join(text for _, text, _ in self.panel_text(controller))
        self.assertEqual(controller.plate_scroll, 0)
        self.assertIn("NEW123", text)
        self.assertNotIn("ZX1235", text)
        self.assertEqual(controller.plate_scroll_max, 0)

    def test_long_identifiers_wrap_without_losing_characters(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (200, 200)))
        font, _ = ui_font(14)
        source = "車牌辨識錯誤：ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        lines = wrap_panel_text(draw, source, font, 110)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), source)
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            self.assertLessEqual(box[2]-box[0], 110)

    def test_footer_separates_person_and_vehicle_counts_including_selection(self):
        controller = PreviewController()
        controller.layout = PreviewLayout.create(1600, 900, 1100, 760, show_plates=True, plates_open=True)
        controller.object_counts = dict(person=2, car=1, motorcycle=2, bus=1, truck=1)
        with patch("preview_ui.ui_font", return_value=(None, True)):
            lines = status_lines(controller, "RECORDING", 7, 40, elapsed=60)
            self.assertIn("區域：人物 2／車輛 5", " ".join(lines))
            controller.command("select")
            controller.layout = PreviewLayout.create(1600, 900, 320, 600, show_plates=True, plates_open=True)
            self.assertIn("區域：人物 2／車輛 5", " ".join(status_lines(controller, "IDLE", 7, 40)))
            self.assertIn("區域：人物 --／車輛 --", " ".join(status_lines(controller, "CAPTURE_RETRY", None, None)))


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
