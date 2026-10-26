"""Chronological delivery using synthetic recordings and no network access."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_protocol import atomic_json, read_json
from recorder import Recorder
import telegram_video
from plate_history import prune_history, RETENTION_SECONDS


class TelegramOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = Mock()
        self.clock = patch.object(telegram_video.time, "time", return_value=2000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        credentials = patch.object(telegram_video, "load_credentials", return_value=("fake", "fake"))
        credentials.start()
        self.addCleanup(credentials.stop)
        self.sent = []
        self.queue = self.make_queue()

    def make_queue(self):
        queue = telegram_video.TelegramVideoQueue(self.root, self.log)
        def video(path, job, token, chat):
            content = queue.caption_content(job)
            self.sent.append(("video", telegram_video.event_time(job), content))
            return dict(notified_plates=content["plates"], notified_through=content["through"])
        def plate(event, token, chat):
            self.sent.append(("plate", event["timestamp"], event))
        queue.send = Mock(side_effect=video)
        queue.send_plate = Mock(side_effect=plate)
        return queue

    def video(self, start, duration=100, trigger=None):
        path = self.root / ("synthetic-%s.mp4" % start)
        path.write_bytes(b"synthetic recording")
        return self.queue.enqueue(path, dict(request_id="clip-%s" % start,
            created_at=start if trigger is None else trigger, clip_started_at=start,
            object_counts=dict(car=1)), duration, True)

    def plate(self, stamp, identity=None, **extra):
        event = dict(id=identity or "plate-%s" % stamp, timestamp=stamp, confirmed=True,
                     text="ZX1235", matched="ZX1234", distance=1)
        event.update(extra)
        atomic_json(self.queue.plate_directory / (event["id"]+".json"), event)
        return event

    def receipt(self, event):
        return read_json(self.queue.plate_receipt(event["id"]), {})

    def order(self):
        return [(kind, stamp) for kind, stamp, _ in self.sent]

    def recording(self, start=1000, elapsed=250):
        atomic_json(self.root / "runtime/recording_state.json", dict(status="RECORDING",
            request_id="clip-%s" % start, trigger_created_at=start, clip_started_at=start+1,
            updated_at=start+1+elapsed, elapsed_seconds=elapsed))

    def test_video_order_uses_trigger_not_completion_or_filename(self):
        late = self.video(1200)
        early = self.video(1000)
        data = read_json(early)
        data["created_at"] = 3000
        atomic_json(early, data)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000), ("video", 1200)])
        self.assertFalse(early.exists())
        self.assertFalse(late.exists())

    def test_trigger_time_is_preserved_separately_from_first_frame(self):
        manifest = self.video(1010, trigger=1000)
        self.assertEqual(read_json(manifest)["trigger_created_at"], 1000)
        self.assertEqual(read_json(manifest)["clip_started_at"], 1010)

    def test_legacy_queue_uses_clip_start_before_completion(self):
        self.video(1200)
        manifest = self.video(1000)
        data = read_json(manifest)
        del data["trigger_created_at"]
        atomic_json(manifest, data)
        self.make_queue().process_once()
        self.assertEqual(self.order(), [("video", 1000), ("video", 1200)])

    def test_plate_and_video_share_one_chronological_timeline(self):
        self.video(1300)
        self.plate(1200)
        self.video(1000)
        self.plate(900)
        self.queue.process_once()
        self.assertEqual(self.order(), [("plate", 900), ("video", 1000),
                                      ("plate", 1200), ("video", 1300)])

    def test_failed_old_video_blocks_newer_video_and_plate_until_retry(self):
        manifest = self.video(1000)
        self.video(1400)
        self.plate(1200)
        normal = self.queue.send.side_effect
        self.queue.send.side_effect = RuntimeError("private network error")
        self.queue.process_once(now=2000)
        self.assertEqual(read_json(manifest)["retry_at"], 2020)
        self.queue.send_plate.assert_not_called()
        self.queue.send.side_effect = normal
        self.queue.process_once(now=2019)
        self.assertEqual(self.queue.send.call_count, 1)
        self.queue.process_once(now=2020)
        self.assertEqual(self.order(), [("video", 1000), ("plate", 1200), ("video", 1400)])
        self.assertNotIn("private network", str(self.log.call_args_list))

    def test_failed_old_plate_blocks_newer_video_across_restart(self):
        event = self.plate(900)
        self.video(1000)
        self.queue.send_plate.side_effect = RuntimeError("synthetic failure")
        self.queue.process_once(now=2000)
        self.queue.send.assert_not_called()
        self.assertEqual(self.receipt(event)["attempts"], 1)
        restarted = self.make_queue()
        restarted.process_once(now=2010)
        restarted.send.assert_not_called()
        restarted.send_plate.assert_not_called()
        restarted.process_once(now=2020)
        self.assertEqual(self.order(), [("plate", 900), ("video", 1000)])

    def test_quiet_guard_blocks_entire_timeline_without_changing_retry(self):
        manifest = self.video(1000)
        self.plate(900)
        original = manifest.read_bytes()
        atomic_json(self.root / "runtime/telegram_quiet.json", dict(request_id="new", quiet_until=2120))
        self.queue.process_once()
        self.assertEqual(self.sent, [])
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual(list(self.queue.plate_delivery.glob("*.json")), [])

    def test_active_clip_holds_its_plates_but_allows_older_backlog(self):
        self.video(700)
        event = self.plate(1100)
        self.recording()
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 700)])
        self.assertEqual(self.receipt(event), {})
        self.video(1000, duration=300)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 700), ("video", 1000)])
        self.assertIn("delivered_via_video", self.receipt(event))

    def test_legacy_active_state_uses_conservative_trigger_boundary(self):
        self.video(700)
        self.plate(990)
        self.recording()
        path = self.root / "runtime/recording_state.json"
        state = read_json(path)
        del state["trigger_created_at"]
        atomic_json(path, state)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 700)])

    def test_plate_settle_window_collects_an_older_late_inference(self):
        self.plate(1995)
        self.queue.process_once(now=2000)
        self.assertEqual(self.sent, [])
        self.plate(1994)
        self.queue.process_once(now=2005)
        self.assertEqual(self.order(), [("plate", 1994), ("plate", 1995)])

    def test_earlier_event_arriving_during_encoding_is_sent_first(self):
        manifest = self.video(1000)
        def prepare(job):
            self.plate(900)
            return Path(job["path"])
        self.queue.prepare = Mock(side_effect=prepare)
        self.queue.process_once()
        self.assertEqual(self.sent, [])
        self.queue.prepare = Mock(return_value=Path(read_json(manifest)["path"]))
        self.queue.process_once()
        self.assertEqual(self.order(), [("plate", 900), ("video", 1000)])

    def test_late_ocr_after_restart_stays_local_without_timestamp_regression(self):
        self.plate(1100)
        self.queue.process_once()
        event = self.plate(1000)
        restarted = self.make_queue()
        restarted.process_once()
        self.assertEqual(self.order(), [("plate", 1100)])
        state = self.receipt(event)
        self.assertTrue(state["local_only"])
        self.assertEqual(state["reason"], "older_than_last_notification")
        self.assertTrue((self.queue.plate_directory / (event["id"]+".json")).exists())

    def test_upgrade_does_not_infer_watermark_from_old_plate_receipts(self):
        event = self.plate(1500)
        atomic_json(self.queue.plate_receipt(event["id"]), dict(event_id=event["id"], sent_at=1900))
        self.video(1000)
        self.make_queue().process_once()
        self.assertEqual(self.order(), [("video", 1000)])

    def test_upgrade_completed_cleanup_job_does_not_block_old_pending_backlog(self):
        manifest = self.video(1500)
        job = read_json(manifest)
        job["sent_at"] = 1800
        atomic_json(manifest, job)
        self.video(1000)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])

    def test_plate_receipt_survives_watermark_write_failure_and_restart(self):
        event = self.plate(1000)
        self.video(1200)
        with patch.object(self.queue, "advance_delivery_time", side_effect=OSError("synthetic disk failure")):
            self.queue.process_once()
        self.assertEqual(self.order(), [("plate", 1000)])
        self.assertEqual(self.receipt(event)["sent_at"], 2000)
        restarted = self.make_queue()
        restarted.process_once()
        self.assertEqual(self.order(), [("plate", 1000), ("video", 1200)])
        self.assertEqual(restarted.last_delivered_time(), 1200)

    def test_video_receipt_failure_after_success_never_resends_or_loses_caption_receipts(self):
        self.video(1000, duration=300)
        event = self.plate(1100)
        actual_atomic = telegram_video.atomic_json
        failed = []
        def fail_caption_receipt(path, data):
            if Path(path) == self.queue.plate_receipt(event["id"]) and not failed:
                failed.append(True)
                raise OSError("synthetic receipt failure")
            return actual_atomic(path, data)
        with patch.object(telegram_video, "atomic_json", side_effect=fail_caption_receipt):
            self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        restarted = self.make_queue()
        restarted.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        self.assertIn("delivered_via_video", self.receipt(event))
        self.assertEqual(list(restarted.directory.glob("*.json")), [])

    def test_successful_video_manifest_write_failure_is_not_reposted_in_process(self):
        manifest = self.video(1000, duration=300)
        self.plate(1100)
        actual_atomic = telegram_video.atomic_json
        def fail_video_manifest(path, data):
            if Path(path) == manifest:
                raise OSError("synthetic receipt failure")
            return actual_atomic(path, data)
        with patch.object(telegram_video, "atomic_json", side_effect=fail_video_manifest):
            with self.assertRaises(OSError):
                self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        self.assertEqual(self.queue.last_delivered_time(), 1100)

    def test_late_local_only_receipts_expire_after_seven_days(self):
        event = self.plate(1000)
        path = self.queue.plate_receipt(event["id"])
        atomic_json(path, dict(event_id=event["id"], local_only=True, retained_at=1000))
        prune_history(self.root, now=1000+RETENTION_SECONDS-1)
        self.assertTrue(path.exists())
        prune_history(self.root, now=1000+RETENTION_SECONDS+1)
        self.assertFalse(path.exists())

    def test_caption_receipts_prevent_duplicate_standalone_plate_notifications(self):
        self.video(1000, duration=300)
        first, last = self.plate(1100), self.plate(1200)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        self.assertIn("delivered_via_video", self.receipt(first))
        self.assertIn("delivered_via_video", self.receipt(last))
        self.assertEqual(self.queue.last_delivered_time(), 1200)
        self.make_queue().process_once()
        self.assertEqual(len(self.sent), 1)

    def test_precapture_plate_after_trigger_is_delivered_before_later_observation(self):
        self.video(1005, duration=100, trigger=1000)
        first, second = self.plate(1002), self.plate(1020)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000)])
        self.assertEqual([event["timestamp"] for event in self.sent[0][2]["plates"]], [1002, 1020])
        self.assertIn("delivered_via_video", self.receipt(first))
        self.assertIn("delivered_via_video", self.receipt(second))

    def test_rollover_plate_cannot_advance_watermark_past_next_trigger(self):
        self.video(1000, duration=220.1)
        self.video(1220.1, duration=120, trigger=1220)
        first, second = self.plate(1210), self.plate(1220.05)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000), ("video", 1220)])
        self.assertEqual(self.sent[0][2]["plates"], [dict(id=first["id"], timestamp=1210)])
        self.assertEqual(self.sent[1][2]["plates"], [dict(id=second["id"], timestamp=1220.05)])

    def test_active_next_clip_bounds_earlier_caption_before_it_is_queued(self):
        manifest = self.video(1000, duration=300)
        self.plate(1199)
        self.plate(1201)
        self.recording(start=1200)
        self.queue.process_once()
        self.assertFalse(manifest.exists())
        self.assertEqual(self.order(), [("video", 1000)])
        self.assertEqual([event["timestamp"] for event in self.sent[0][2]["plates"]], [1199])

    def test_legacy_active_next_clip_also_bounds_caption_conservatively(self):
        self.video(1000, duration=300)
        self.plate(1100)
        self.plate(1201)
        self.recording(start=1200)
        path = self.root / "runtime/recording_state.json"
        state = read_json(path)
        del state["trigger_created_at"]
        atomic_json(path, state)
        self.queue.process_once()
        self.assertEqual([event["timestamp"] for event in self.sent[0][2]["plates"]], [1100])
        self.assertEqual(self.queue.last_delivered_time(), 1100)

    def test_equal_trigger_videos_do_not_block_each_other_with_caption_watermarks(self):
        self.video(1000, duration=100)
        self.video(1001, duration=100, trigger=1000)
        self.plate(1050)
        self.queue.process_once()
        self.assertEqual(self.order(), [("video", 1000), ("video", 1000)])
        self.assertEqual(len(self.sent[0][2]["plates"]), 0)
        self.assertEqual(len(self.sent[1][2]["plates"]), 1)

    def test_caption_overflow_sends_only_omitted_plates_in_order(self):
        self.video(1000, duration=300)
        events = [self.plate(1100+index) for index in range(30)]
        self.queue.process_once()
        content = self.sent[0][2]
        included = content["plates"]
        self.assertGreater(len(included), 0)
        self.assertLess(len(included), len(events))
        self.assertLessEqual(len(content["text"]), 1024)
        self.assertIn("另有", content["text"])
        self.assertEqual([stamp for kind, stamp in self.order() if kind == "plate"],
                         [event["timestamp"] for event in events[len(included):]])
        footer = content["text"].splitlines()[-1]
        self.assertIn(telegram_video.taipei_time(included[-1]["timestamp"]), footer)
        self.assertNotIn(telegram_video.taipei_time(events[-1]["timestamp"]), footer)

    def test_failed_video_does_not_acknowledge_caption_plates(self):
        self.video(1000, duration=300)
        event = self.plate(1100)
        self.queue.send.side_effect = RuntimeError("synthetic failure")
        self.queue.process_once()
        self.assertEqual(self.receipt(event), {})
        self.assertFalse(self.queue.order_file.exists())

    def test_video_caption_contains_full_times_and_observed_plate_in_footer(self):
        manifest = self.video(1010, duration=300, trigger=1000)
        self.plate(1100)
        text = self.queue.caption(read_json(manifest))
        self.assertIn("觸發時間（台灣）：1970/01/01 08:16:40", text)
        self.assertIn("片段：1970/01/01 08:16:50 ～ 1970/01/01 08:21:50", text)
        self.assertIn("ZX1235", text.splitlines()[-1])
        self.assertNotIn("ZX1234", text.splitlines()[-1])

    def test_vehicle_without_plate_footer_uses_actual_trigger_time(self):
        manifest = self.video(1010, trigger=1000)
        footer = self.queue.caption(read_json(manifest)).splitlines()[-1]
        self.assertIn("1970/01/01 08:16:40", footer)
        self.assertNotIn("車牌未確認", footer)

    def test_late_manual_video_is_preserved_instead_of_sent_out_of_order(self):
        self.plate(1200)
        self.queue.process_once()
        manifest = self.video(1000)
        self.queue.process_once()
        self.assertTrue(manifest.exists())
        self.assertTrue(Path(read_json(manifest)["path"]).exists())
        self.assertEqual(self.order(), [("plate", 1200)])

    def test_recording_state_publishes_stable_trigger_and_capture_times(self):
        recorder = Recorder.__new__(Recorder)
        recorder.state = "RECORDING"
        recorder.state_file = self.root / "runtime/recording_state.json"
        recorder.request_id = "synthetic-recording"
        recorder.trigger_created_at = 1000
        recorder.clip_started_at = 1002
        recorder.publish(elapsed_seconds=200)
        state = read_json(recorder.state_file)
        self.assertEqual(state["trigger_created_at"], 1000)
        self.assertEqual(state["clip_started_at"], 1002)
        recorder.publish("IDLE")
        self.assertNotIn("trigger_created_at", read_json(recorder.state_file))


if __name__ == "__main__":
    unittest.main()
