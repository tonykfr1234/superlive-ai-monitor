"""Vehicle/person reporting with synthetic evidence and no network traffic."""
import csv
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from daily_report import DailyReports, TAIPEI
from record_protocol import atomic_json, read_json
from telegram_video import TelegramVideoQueue, event_line, event_object_counts


class VehicleCaptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue = TelegramVideoQueue(self.root, Mock())
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"synthetic video")

    def job(self, counts=None, count=2):
        request = dict(request_id="synthetic", count=count, clip_started_at=1000)
        if counts is not None:
            request["object_counts"] = counts
        return read_json(self.queue.enqueue(self.video, request, 180, True))

    def plate(self, **extra):
        event = dict(id="synthetic-plate", timestamp=1050, text="ZX1235", raw_text="ZX-1235",
                     matched="ZX1234", distance=1, confirmed=True)
        event.update(extra)
        atomic_json(self.root / "runtime/plate_events/synthetic-plate.json", event)
        return event

    def test_vehicle_only_queue_preserves_counts_across_restart_and_labels_missing_plate(self):
        job = self.job(dict(person=0, car=1, truck=1), count=2)
        restarted = TelegramVideoQueue(self.root, Mock())
        persisted = read_json(next(restarted.directory.glob("*.json")))
        self.assertEqual(persisted["object_counts"]["person"], 0)
        self.assertEqual(persisted["object_counts"]["car"], 1)
        self.assertEqual(persisted["object_counts"]["truck"], 1)
        caption = restarted.caption(persisted)
        self.assertIn("車輛錄影", caption)
        self.assertNotIn("人物：", caption)
        self.assertNotIn("車種：", caption)
        self.assertNotIn("車牌未確認", caption)
        self.assertNotIn("偵測到人員", caption)

    def test_mixed_counts_are_reported_separately_from_total(self):
        job = self.job(dict(person=2, car=1, motorcycle=2, bus=1, truck=1), count=7)
        caption = self.queue.caption(job)
        self.assertIn("人物與車輛錄影", caption)
        self.assertNotIn("人物：", caption)
        self.assertNotIn("車種：", caption)
        self.assertNotIn("人物：7", caption)

    def test_legacy_pending_job_count_still_means_people(self):
        job = self.job(count=2)
        self.assertNotIn("object_counts", job)
        caption = self.queue.caption(job)
        self.assertIn("人物錄影", caption)
        self.assertNotIn("人物：", caption)
        self.assertNotIn("車牌未確認", caption)

    def test_vehicle_clip_includes_actual_fuzzy_plate_and_time(self):
        self.plate()
        caption = self.queue.caption(self.job(dict(person=0, car=1), count=1))
        self.assertIn("08:17:30 辨識：ZX1235", caption)
        self.assertIn("🔎 ⚠️", caption)
        self.assertIn("可能符合：ZX1234", caption)
        self.assertNotIn("車牌未確認", caption)

    def test_standalone_plate_message_has_vehicle_label_and_preserves_ocr(self):
        event = self.plate()
        with patch("requests.post", return_value=Mock(status_code=200, json=lambda: {"ok": True})) as post:
            self.queue.send_plate(event, "fake-token", "fake-chat")
        message = post.call_args.kwargs["data"]["text"]
        self.assertIn("車牌／車輛紀錄", message)
        self.assertIn("辨識：ZX1235", message)
        self.assertIn("可能符合：ZX1234", message)

    def test_replay_notification_labels_original_video_and_time_without_private_path(self):
        event = self.plate(source="recording_replay", source_video=r"C:\private\synthetic.mp4", offset_seconds=27.5)
        line = event_line(event)
        self.assertIn("原片補辨識：synthetic.mp4（影片 +27.5 秒）", line)
        self.assertNotIn("private", line)
        self.assertIn("08:17:30 辨識：ZX1235", line)
        self.assertIn("可能符合：ZX1234", line)
        self.assertIn("原片補辨識", self.queue.caption(self.job(dict(car=1), count=1)))

    def test_explicit_class_counts_do_not_fall_back_to_legacy_total(self):
        counts = event_object_counts(dict(count=9, object_counts={"car": 1, "person": -2, "bus": True, "truck": "2"}))
        self.assertEqual(counts, dict(person=0, car=1, motorcycle=0, bus=0, truck=2))

    def test_exact_target_is_highlighted_but_other_plates_have_no_list_notice(self):
        exact = self.plate(text="ZX-1234", matched="ZX1234", distance=0)
        self.assertIn("🚨 ⭐ 指定車牌", event_line(exact))
        self.assertNotIn("可能符合", event_line(exact))
        self.assertIn("🚨 ⭐", self.queue.caption(self.job(dict(car=1))))
        for status in ("unmatched", "ambiguous", "insufficient"):
            other = self.plate(text="AB5678", matched=None, match_status=status)
            line = event_line(other)
            self.assertIn("辨識：AB5678", line)
            self.assertNotIn("名單", line)
            self.assertNotIn("🚨", line)
            self.assertNotIn("可能符合", line)


class VehicleCsvTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stamp = datetime(2026, 10, 21, 12, 0, tzinfo=TAIPEI).timestamp()
        self.report = DailyReports(self.root, self.root / "videos", Mock(), clock=lambda: self.stamp)

    def csv_entries(self):
        self.report.generate(datetime(2026, 10, 21).date(), "2300")
        path = self.root / "videos/2026-10-21/SuperLive_Triggers_2026-10-21_2300.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return {row["事件ID"]: row for row in csv.DictReader(stream)}

    def test_typed_vehicle_mixed_and_legacy_triggers_are_not_mislabeled(self):
        for identity, count, counts in (("vehicle", 2, dict(car=1, truck=1)),
                                        ("mixed", 4, dict(person=2, motorcycle=2)),
                                        ("legacy", 3, None)):
            request = dict(request_id=identity, created_at=self.stamp, count=count, confidence=.9)
            if counts is not None:
                request["object_counts"] = counts
            self.report.record_trigger(request, identity+".mp4")
        rows = self.csv_entries()
        self.assertEqual(rows["vehicle"]["人數"], "0")
        self.assertEqual(rows["vehicle"]["車輛數"], "2")
        self.assertEqual(rows["vehicle"]["車輛類型"], "汽車、卡車")
        self.assertEqual(rows["vehicle"]["汽車數"], "1")
        self.assertEqual(rows["vehicle"]["卡車數"], "1")
        self.assertEqual(rows["vehicle"]["事件類型"], "VEHICLE_ACTIVITY")
        self.assertEqual(rows["mixed"]["人數"], "2")
        self.assertEqual(rows["mixed"]["車輛數"], "2")
        self.assertEqual(rows["mixed"]["機車數"], "2")
        self.assertEqual(rows["mixed"]["總物件數"], "4")
        self.assertEqual(rows["legacy"]["人數"], "3")
        self.assertEqual(rows["legacy"]["車輛數"], "0")

    def test_video_filename_update_retains_typed_trigger_counts(self):
        request = dict(request_id="typed", created_at=self.stamp, count=1, confidence=.8,
                       trigger_type="MOVEMENT_ROLLOVER", object_counts=dict(car=1))
        self.report.record_trigger(request)
        del request["object_counts"]
        self.report.record_trigger(request, "synthetic.mp4")
        row = self.csv_entries()["typed"]
        self.assertEqual(row["人數"], "0")
        self.assertEqual(row["車輛數"], "1")
        self.assertEqual(row["汽車數"], "1")
        self.assertEqual(row["影片檔名"], "synthetic.mp4")
        self.assertEqual(row["事件類型"], "MOVEMENT_ROLLOVER")

    def test_untyped_vehicle_evidence_does_not_invent_people_or_class_breakdown(self):
        events = self.root / "events"
        events.mkdir()
        (events / "person_events.csv").write_text(
            "timestamp,event_type,count,confidence,request_id\n"
            "2026-10-21 10:00:00,PERSON_DETECTED,2,0.8,person\n"
            "2026-10-21 10:01:00,VEHICLE_ACTIVITY,3,0.8,vehicle\n"
            "2026-10-21 10:02:00,PERSON_VEHICLE_ACTIVITY,4,0.8,mixed\n", encoding="utf-8-sig")
        rows = self.csv_entries()
        self.assertEqual(rows["person"]["人數"], "2")
        self.assertEqual(rows["vehicle"]["人數"], "0")
        self.assertEqual(rows["vehicle"]["車輛數"], "3")
        self.assertEqual(rows["vehicle"]["車輛類型"], "未記錄")
        self.assertEqual(rows["vehicle"]["汽車數"], "")
        self.assertEqual(rows["mixed"]["人數"], "")
        self.assertEqual(rows["mixed"]["車輛數"], "")
        self.assertEqual(rows["mixed"]["總物件數"], "4")


if __name__ == "__main__":
    unittest.main()
