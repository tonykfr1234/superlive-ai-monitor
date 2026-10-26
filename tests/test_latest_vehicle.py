"""Live vehicle/footer tests use synthetic frames and plates; no network or camera."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from latest_vehicle import LatestVehicleTracker, RETENTION_SECONDS, latest_vehicle_text
from preview_ui import PreviewController, PreviewLayout, latest_vehicle_footer_text, render_preview
from record_protocol import atomic_json, read_json


START = 1780000000.0
FRAME = (1000, 600)


def vehicle(box=(100, 100, 500, 400), class_id=2):
    return dict(box=box, class_id=class_id, confidence=.9)


def plate_event(text="AB1234", **updates):
    result = dict(id="synthetic", confirmed=True, text=text, matched="AB1235", timestamp=START+1,
                  bbox=[700, 400, 800, 450], evidence=[
                      dict(text=text, timestamp=START+offset, sample_id=str(index), bbox=[150, 300, 230, 340])
                      for index, offset in enumerate((0, .5, 1))])
    result.update(updates)
    return result


class LatestVehicleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.tracker = LatestVehicleTracker(self.root, now=START)

    def observe_group(self, cars=None):
        for offset in (0, .5, 1):
            self.tracker.observe(cars or [vehicle()], START+offset, FRAME)

    def confirm(self):
        self.observe_group()
        self.tracker.update_plates([plate_event()])
        self.assertEqual(self.tracker.snapshot()["plate"], "AB1234")

    def test_last_vehicle_uses_only_valid_vehicle_observations(self):
        self.tracker.observe([vehicle(class_id=0)], START, FRAME)
        self.assertEqual(self.tracker.snapshot(), {})
        self.tracker.observe([vehicle()], START+1, FRAME)
        self.tracker.observe([], START+2, FRAME)
        self.tracker.observe([vehicle()], START+3, FRAME, available=False)
        self.assertEqual(self.tracker.snapshot()["timestamp"], START+1)

    def test_observed_plate_uses_its_evidence_and_not_top_level_box_or_target(self):
        self.confirm()
        text = latest_vehicle_text(self.tracker.snapshot())
        self.assertIn("AB1234", text)
        self.assertNotIn("AB1235", text)
        self.assertEqual(self.tracker.snapshot()["plate_timestamp"], START+1)

    def test_replay_unconfirmed_unknown_source_and_missing_evidence_are_ignored(self):
        self.observe_group()
        for changes in [dict(source="recording_replay"), dict(source="imported"),
                        dict(confirmed=False), dict(evidence=[]), dict(evidence=None)]:
            with self.subTest(changes=changes):
                self.tracker.update_plates([plate_event(**changes)])
                self.assertEqual(self.tracker.snapshot()["plate"], "")

    def test_unrelated_plate_box_or_different_sample_text_cannot_supply_a_plate(self):
        self.observe_group()
        for kind in ("outside", "wrong_text", "one_frame", "same_time"):
            event = plate_event()
            for index, sample in enumerate(event["evidence"]):
                if kind == "outside":
                    sample["bbox"] = [700, 400, 800, 450]
                elif kind == "wrong_text" and index < 2:
                    sample["text"] = "AB1235"
                elif kind == "one_frame":
                    sample["sample_id"] = "same"
                elif kind == "same_time":
                    sample["timestamp"] = START
            with self.subTest(kind=kind):
                self.tracker.update_plates([event])
                self.assertEqual(self.tracker.snapshot()["plate"], "")

    def test_two_different_vehicles_cannot_combine_their_samples(self):
        self.observe_group([vehicle(), vehicle((600, 100, 950, 400))])
        event = plate_event()
        event["evidence"] = event["evidence"][:2]
        event["evidence"][1]["bbox"] = [700, 300, 780, 340]
        self.tracker.update_plates([event])
        self.assertEqual(self.tracker.snapshot()["plate"], "")

    def test_vehicle_order_and_normal_jitter_keep_same_vehicle_sample_association(self):
        first, second = vehicle(), vehicle((600, 100, 950, 400))
        self.tracker.observe([first, second], START, FRAME)
        self.tracker.observe([second, vehicle((110, 105, 510, 405))], START+.5, FRAME)
        self.tracker.observe([vehicle((115, 100, 515, 400)), second], START+1, FRAME)
        self.tracker.update_plates([plate_event()])
        self.assertEqual(self.tracker.snapshot()["plate"], "AB1234")
        self.tracker.observe([first, second], START+1.5, FRAME)
        self.assertEqual(self.tracker.snapshot()["plate"], "AB1234")

    def test_new_vehicle_or_group_change_clears_previous_plate(self):
        for change in ("different", "disappeared", "paused", "gap", "resolution", "count"):
            with self.subTest(change=change):
                self.tracker = LatestVehicleTracker(self.root, now=START)
                self.confirm()
                cars, size, stamp = [vehicle()], FRAME, START+1.5
                if change == "different":
                    cars = [vehicle((600, 100, 950, 400))]
                elif change == "disappeared":
                    self.tracker.observe([], START+1.25, FRAME)
                elif change == "paused":
                    self.tracker.observe([], START+1.25, FRAME, available=False)
                elif change == "gap":
                    stamp = START+5
                elif change == "resolution":
                    size = (1200, 700)
                elif change == "count":
                    cars.append(vehicle((600, 100, 950, 400)))
                self.tracker.observe(cars, stamp, size)
                self.tracker.update_plates([plate_event()])
                self.assertEqual(self.tracker.snapshot()["plate"], "")

    def test_out_of_order_observation_or_plate_does_not_replace_newer_state(self):
        self.confirm()
        original = self.tracker.snapshot()
        self.tracker.observe([vehicle((600, 100, 950, 400))], START+.25, FRAME)
        older = plate_event("XY9999")
        older["evidence"] = older["evidence"][:2]
        self.tracker.update_plates([older])
        self.assertEqual(self.tracker.snapshot(), original)

    def test_future_or_stale_evidence_cannot_attach(self):
        self.observe_group()
        for stamp in (START-20, START+20):
            event = plate_event(timestamp=stamp)
            for sample in event["evidence"]:
                sample["timestamp"] = stamp
            self.tracker.update_plates([event])
        self.assertEqual(self.tracker.snapshot()["plate"], "")

    def test_pending_plate_can_confirm_after_vehicle_leaves_without_advancing_vehicle_time(self):
        self.observe_group()
        self.tracker.observe([], START+1.5, FRAME)
        self.tracker.update_plates([plate_event()])
        self.assertEqual(self.tracker.snapshot()["plate"], "AB1234")
        self.assertEqual(self.tracker.snapshot()["timestamp"], START+1)

    def test_slow_ocr_keeps_matching_observations_at_sixty_fps(self):
        for frame in range(421):
            self.tracker.observe([vehicle()], START+frame/60, FRAME)
        self.tracker.update_plates([plate_event()])
        self.assertEqual(self.tracker.snapshot()["plate"], "AB1234")
        self.assertEqual(self.tracker.snapshot()["timestamp"], START+7)

    def test_restart_restores_display_but_does_not_reuse_old_plate_for_new_observation(self):
        self.confirm()
        restored = LatestVehicleTracker(self.root, now=START+10)
        self.assertEqual(restored.snapshot(), self.tracker.snapshot())
        restored.observe([vehicle()], START+11, FRAME)
        self.assertEqual(restored.snapshot()["plate"], "")

    def test_persistence_is_throttled_but_new_context_plate_and_final_time_are_saved(self):
        with patch("latest_vehicle.time.monotonic", return_value=10) as clock, patch(
                "latest_vehicle.atomic_json", wraps=atomic_json) as save:
            for index in range(10):
                self.tracker.observe([vehicle()], START+index/10, FRAME)
            self.assertEqual(save.call_count, 1)
            clock.return_value = 11
            self.tracker.observe([vehicle()], START+1, FRAME)
            self.assertEqual(save.call_count, 2)
            self.tracker.update_plates([plate_event()])
            self.assertEqual(save.call_count, 3)
            self.tracker.observe([vehicle()], START+1.1, FRAME)
            self.assertEqual(save.call_count, 3)
            self.tracker.observe([], START+1.2, FRAME)
            self.assertEqual(save.call_count, 4)
        self.assertEqual(read_json(self.tracker.path)["timestamp"], START+1.1)

    def test_bad_persistence_is_ignored_and_write_failure_does_not_stop_detection(self):
        for value in [[], {"timestamp": START+1}, {"timestamp": START-RETENTION_SECONDS-1},
                      {"timestamp": float("inf")}]:
            with patch("latest_vehicle.read_json", return_value=value):
                self.assertEqual(LatestVehicleTracker(self.root, now=START).snapshot(), {})
        with patch("latest_vehicle.read_json", return_value=dict(timestamp=START, plate="AB1234",
                plate_timestamp=START-RETENTION_SECONDS-1)):
            self.assertEqual(LatestVehicleTracker(self.root, now=START).snapshot()["plate"], "")
        with patch("latest_vehicle.atomic_json", side_effect=PermissionError):
            self.tracker.observe([vehicle()], START, FRAME)
        self.assertEqual(self.tracker.snapshot()["timestamp"], START)
        self.assertTrue(self.tracker.persistence_error)

    def test_footer_has_explicit_taipei_full_date_and_no_fabricated_plate(self):
        text = latest_vehicle_text(dict(timestamp=START, plate=""))
        expected = datetime.fromtimestamp(START, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIn(expected, text)
        self.assertIn("台灣時間", text)
        self.assertIn("車牌未確認", text)
        self.assertIn("尚未偵測到車輛", latest_vehicle_text({}))


class LatestVehicleFooterTests(unittest.TestCase):
    def test_wide_narrow_portrait_and_long_plate_render_below_buttons_without_truncation(self):
        import numpy as np
        from PIL import ImageDraw
        for size in ((1440, 900), (1100, 760), (960, 720), (800, 480), (640, 480), (320, 300)):
            for show_plates in (False, True):
                with self.subTest(size=size, plates=show_plates):
                    controller = PreviewController()
                    controller.latest_vehicle = dict(timestamp=START, plate="ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
                    text = latest_vehicle_footer_text(controller)
                    layout = PreviewLayout.create(720, 1280, *size, show_plates=show_plates,
                        plate_sidebar=show_plates, plate_status_text="車牌辨識中 · ADB 1600x900",
                        latest_vehicle_status=text)
                    controller.layout = layout
                    self.assertEqual("".join(layout.latest_vehicle_lines).replace(" ", ""), text.replace(" ", ""))
                    self.assertNotIn("…", "".join(layout.latest_vehicle_lines))
                    last_button = max(rect.y+rect.height for rect in layout.buttons.values())
                    self.assertGreater(layout.latest_vehicle_top, last_button)
                    self.assertLessEqual(layout.video.y+layout.video.height, layout.footer.y)
                    self.assertAlmostEqual(layout.video.width/layout.video.height, 720/1280, delta=.025)
                    original_draw = ImageDraw.Draw
                    drawn = []
                    def capture_draw(image, *args, **kwargs):
                        base = original_draw(image, *args, **kwargs)
                        class Capture:
                            def __getattr__(self, name):
                                return getattr(base, name)
                            def text(self, position, value, **options):
                                if options.get("fill") == (155, 220, 183):
                                    drawn.append((value, base.textbbox(position, value, font=options["font"])))
                                return base.text(position, value, **options)
                        return Capture()
                    frame = np.full((1280, 720, 3), 25, np.uint8)
                    source = frame.copy()
                    with patch("PIL.ImageDraw.Draw", side_effect=capture_draw):
                        result = render_preview(frame, controller, "IDLE", count=0)
                    self.assertTrue(np.array_equal(source, frame))
                    self.assertEqual(result.shape, (layout.height, layout.width, 3))
                    self.assertEqual(len(drawn), len(layout.latest_vehicle_lines))
                    for _, box in drawn:
                        self.assertGreater(box[1], last_button)
                        self.assertGreaterEqual(box[0], 0)
                        self.assertLessEqual(box[2], layout.width-12)
                        self.assertLessEqual(box[3], layout.height-4)


if __name__ == "__main__":
    unittest.main()
