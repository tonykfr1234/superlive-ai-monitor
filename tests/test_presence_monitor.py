"""Headless tests: no screen capture, YOLO download, notifications or network."""
import importlib.util
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


BASELINE = Path(__file__).resolve().parents[1] / "src"
if str(BASELINE) not in sys.path:
    sys.path.insert(0, str(BASELINE))

from record_protocol import atomic_json, read_json, recorder_ready
from region_policy import box_in_roi, people_in_region, pixel_roi, selection_roi, validate_roi


def load_headless_monitor():
    # Mock only external UI/inference dependencies during import. Production
    # region selection and file-based request/state handling remain real.
    dependency_names = (
        "cv2", "mss", "numpy", "psutil", "requests", "torch",
        "win32gui", "win32process", "ultralytics", "winotify", "app_branding",
    )
    dependencies = {name: MagicMock(name=name) for name in dependency_names}
    spec = importlib.util.spec_from_file_location(
        "presence_monitor_under_test", BASELINE / "monitor.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


monitor = load_headless_monitor()


class RegionPolicyTests(unittest.TestCase):
    def test_unset_and_empty_region_cover_whole_frame(self):
        for roi in (None, []):
            with self.subTest(roi=roi):
                self.assertEqual(pixel_roi(roi, 1920, 1080), (0, 0, 1920, 1080))
                self.assertTrue(box_in_roi((1, 1, 10, 10), roi, 1920, 1080))
                self.assertTrue(box_in_roi((1900, 1060, 1919, 1079), roi, 1920, 1080))
                self.assertFalse(box_in_roi((1920, 20, 1930, 30), roi, 1920, 1080))

    def test_person_intersecting_region_counts_but_edge_contact_does_not(self):
        roi = [0.25, 0.25, 0.75, 0.75]
        cases = [
            ((40, 40, 60, 60), True),       # inside
            ((10, 30, 26, 60), True),       # overlap, center outside
            ((0, 0, 100, 100), True),       # person box contains region
            ((10, 30, 25, 60), False),      # touches left edge only
            ((75, 30, 90, 60), False),      # touches right edge only
            ((30, 10, 60, 25), False),      # touches top edge only
            ((30, 75, 60, 90), False),      # touches bottom edge only
            ((1, 1, 10, 10), False),        # outside
            ((50, 50, 50, 60), False),      # empty box
        ]
        for box, expected in cases:
            with self.subTest(box=box):
                self.assertEqual(box_in_roi(box, roi, 100, 100), expected)

    def test_selected_region_scales_with_capture_resolution(self):
        roi = selection_roi((100, 50, 200, 100), 400, 200)
        self.assertEqual(roi, [0.25, 0.25, 0.75, 0.75])
        self.assertEqual(pixel_roi(roi, 800, 600), (200, 150, 600, 450))
        self.assertTrue(box_in_roi((180, 180, 210, 240), roi, 800, 600))
        self.assertFalse(box_in_roi((20, 20, 100, 100), roi, 800, 600))
        self.assertIsNone(selection_roi((0, 0, 0, 0), 400, 200))

    def test_only_confident_people_in_region_are_returned_without_track_ids(self):
        boxes = [(30, 30, 40, 70), (40, 40, 60, 70), (40, 40, 60, 70),
                 (40, 40, 60, 70), (1, 1, 10, 10)]
        people = people_in_region(
            boxes, [0, 0, 0, 2, 0], [0.25, 0.9, 0.249, 0.99, 0.99],
            [0.25, 0.25, 0.75, 0.75], 100, 100, 0.25,
        )
        self.assertEqual(people, [
            {"box": (30, 30, 40, 70), "confidence": 0.25},
            {"box": (40, 40, 60, 70), "confidence": 0.9},
        ])
        # Presence alone is sufficient: identical, stationary boxes still count.
        self.assertEqual(people, people_in_region(
            boxes, [0, 0, 0, 2, 0], [0.25, 0.9, 0.249, 0.99, 0.99],
            [0.25, 0.25, 0.75, 0.75], 100, 100, 0.25,
        ))

    def test_invalid_regions_fail_instead_of_silently_monitoring_full_frame(self):
        for roi in ("full", {}, [0, 0, 1], [0, 0, 1, 1, 1], [-0.1, 0, 1, 1],
                    [0, 0, 1.1, 1], [0.5, 0, 0.5, 1], [0, 0.8, 1, 0.2],
                    [0, 0, float("nan"), 1], [0, 0, float("inf"), 1]):
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                validate_roi(roi)


class RecorderProtocolTests(unittest.TestCase):
    def test_readiness_requires_fresh_live_idle_recorder_and_no_pending_request(self):
        state = {"status": "IDLE", "updated_at": 95, "pid": 123}
        self.assertTrue(recorder_ready(state, now=100, pid_alive=lambda pid: pid == 123))
        self.assertFalse(recorder_ready(state, pending=True, now=100))
        self.assertFalse(recorder_ready(state, now=100, pid_alive=lambda pid: False))
        for status in ("RECORDING", "STARTING", "STOPPED", "ERROR", "UPLOADING"):
            self.assertFalse(recorder_ready(dict(state, status=status), now=100))
        for updated in (90, 0, 101, float("nan"), float("inf"), "bad", None):
            with self.subTest(updated=updated):
                self.assertFalse(recorder_ready(dict(state, updated_at=updated), now=100))
        for invalid in (None, [], {}, {"status": "IDLE"}, dict(state, pid=0), dict(state, pid="bad")):
            with self.subTest(invalid=invalid):
                self.assertFalse(recorder_ready(invalid, now=100))

    def test_json_retries_transient_windows_lock_and_preserves_on_permanent_failure(self):
        import record_protocol
        with tempfile.TemporaryDirectory(dir=BASELINE.parent) as folder:
            path = Path(folder) / "state.json"
            atomic_json(path, {"old": True})
            replace = record_protocol.os.replace
            calls = [0]
            def transient(source, destination):
                calls[0] += 1
                if calls[0] < 3:
                    raise PermissionError("sharing violation")
                replace(source, destination)
            with patch.object(record_protocol.os, "replace", side_effect=transient), \
                    patch.object(record_protocol.time, "sleep"):
                atomic_json(path, {"new": True})
            self.assertEqual(read_json(path), {"new": True})
            self.assertEqual(calls[0], 3)
            with patch.object(record_protocol.os, "replace", side_effect=PermissionError), \
                    patch.object(record_protocol.time, "sleep"), self.assertRaises(PermissionError):
                atomic_json(path, {"lost": True})
            self.assertEqual(read_json(path), {"new": True})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_json_replacement_is_valid_and_failed_write_preserves_existing_state(self):
        with tempfile.TemporaryDirectory(dir=BASELINE.parent) as folder:
            path = Path(folder) / "runtime" / "state.json"
            first = {"status": "IDLE", "label": "指定區域"}
            atomic_json(path, first)
            self.assertEqual(read_json(path), first)
            with self.assertRaises(ValueError):
                atomic_json(path, {"updated_at": float("nan")})
            self.assertEqual(read_json(path), first)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            path.write_text("incomplete {", encoding="utf-8")
            self.assertEqual(read_json(path, {"status": "UNKNOWN"}), {"status": "UNKNOWN"})


class PresenceTriggerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=BASELINE.parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = dict(monitor.DEFAULT_POLICY)
        self.people = [{"box": (50, 50, 100, 180), "confidence": 0.85}]
        replacements = {
            "POLICY_FILE": self.root / "policy.json",
            "REQUEST_FILE": self.root / "record_request.json",
            "STATE_FILE": self.root / "recording_state.json",
            "STOP_FLAG": self.root / "stop.flag",
        }
        for name, value in replacements.items():
            p = patch.object(monitor, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(monitor.psutil, "pid_exists", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(monitor, "dispatch_detection_notification")
        self.notification = p.start()
        self.addCleanup(p.stop)

    def state(self, status="IDLE", age=0):
        atomic_json(monitor.STATE_FILE, {
            "status": status, "updated_at": time.time() - age, "pid": 123,
        })

    def trigger(self):
        return monitor.trigger_recording(object(), self.people, self.cfg, ("", ""))

    def test_dispatcher_accepts_new_confirmed_activity_after_recorder_returns_to_idle(self):
        self.state()
        self.assertTrue(self.trigger())
        first = read_json(monitor.REQUEST_FILE)
        self.assertEqual(first["count"], 1)
        self.assertIsNone(first["roi"])
        self.assertFalse(self.trigger())  # request not yet consumed
        self.assertEqual(self.notification.call_count, 1)
        self.state("RECORDING")
        monitor.REQUEST_FILE.unlink()
        for _ in range(3):
            self.assertFalse(self.trigger())
        self.assertEqual(self.notification.call_count, 1)
        self.state("IDLE")
        # The dispatcher does not track motion; DetectionSession gates its callers.
        self.assertTrue(self.trigger())
        second = read_json(monitor.REQUEST_FILE)
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertEqual(self.notification.call_count, 2)

    def test_missing_stale_busy_and_stopped_recorder_never_notify(self):
        self.assertFalse(self.trigger())
        for status in ("RECORDING", "STARTING", "UPLOADING", "ERROR", "STOPPED"):
            self.state(status)
            self.assertFalse(self.trigger())
        self.state(age=11)
        self.assertFalse(self.trigger())
        self.state()
        monitor.STOP_FLAG.write_text("STOP", encoding="utf-8")
        self.assertFalse(self.trigger())
        self.notification.assert_not_called()
        self.assertFalse(monitor.REQUEST_FILE.exists())

    def test_absence_never_notifies_or_requests_recording(self):
        self.state()
        self.people = []
        self.assertFalse(self.trigger())
        self.notification.assert_not_called()
        self.assertFalse(monitor.REQUEST_FILE.exists())

    def test_notification_failure_still_publishes_request_with_selected_region(self):
        self.state()
        self.cfg["roi"] = [0.1, 0.2, 0.8, 0.9]
        self.notification.side_effect = OSError("simulated snapshot write failure")
        self.assertTrue(self.trigger())
        self.assertEqual(read_json(monitor.REQUEST_FILE)["roi"], self.cfg["roi"])

    def test_request_is_published_before_dispatching_any_notification(self):
        self.state()
        requests_at_dispatch = []
        self.notification.side_effect = lambda *args: requests_at_dispatch.append(read_json(monitor.REQUEST_FILE))
        self.assertTrue(self.trigger())
        self.assertEqual(len(requests_at_dispatch), 1)
        self.assertEqual(requests_at_dispatch[0]["count"], 1)
        self.assertEqual(requests_at_dispatch[0]["request_id"], self.notification.call_args.args[3])

    def test_policy_defaults_to_all_people_full_frame_and_rejects_invalid_config(self):
        self.assertIsNone(monitor.load_policy()["roi"])
        atomic_json(monitor.POLICY_FILE, {"watch_classes": [2], "roi": [0, 0, 0.5, 1]})
        self.assertEqual(monitor.load_policy()["watch_classes"], [0])
        for data in ([], {"roi": [0, 0, 0, 1]}, {"target_fps": 0},
                     {"target_fps": 61}, {"confidence": 0}, {"confidence": 1.1}):
            with self.subTest(data=data):
                atomic_json(monitor.POLICY_FILE, data)
                with self.assertRaises(ValueError):
                    monitor.load_policy()

    def test_saving_region_removes_obsolete_motion_notification_settings(self):
        self.cfg.update(moving_notify_seconds=1, still_notify_seconds=60, absence_reset_seconds=3)
        monitor.save_roi(self.cfg, [0.1, 0.2, 0.8, 0.9])
        saved = read_json(monitor.POLICY_FILE)
        self.assertEqual(saved["roi"], [0.1, 0.2, 0.8, 0.9])
        self.assertFalse({"moving_notify_seconds", "still_notify_seconds", "absence_reset_seconds"} & saved.keys())
        monitor.save_roi(self.cfg, None)
        self.assertIsNone(read_json(monitor.POLICY_FILE)["roi"])

    def test_polygon_save_reload_and_record_request_preserve_all_vertices(self):
        polygon = [[0.1, 0.2], [0.8, 0.2], [0.4, 0.5], [0.1, 0.9]]
        monitor.save_roi(self.cfg, polygon)
        polygon[0][0] = 0.05
        loaded = monitor.load_policy()
        self.assertEqual(loaded["roi"], [[0.1, 0.2], [0.8, 0.2], [0.4, 0.5], [0.1, 0.9]])
        self.assertEqual(loaded["roi_coordinate_space"], "mumu_content_v1")
        self.assertEqual(self.cfg["roi"], loaded["roi"])
        self.state()
        self.assertTrue(self.trigger())
        self.assertEqual(read_json(monitor.REQUEST_FILE)["roi"], loaded["roi"])

    def test_invalid_polygon_cannot_replace_saved_region(self):
        monitor.save_roi(self.cfg, [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])
        previous = read_json(monitor.POLICY_FILE)
        with self.assertRaises(ValueError):
            monitor.save_roi(self.cfg, [[0.1, 0.1], [0.9, 0.9], [0.1, 0.9], [0.9, 0.1]])
        self.assertEqual(read_json(monitor.POLICY_FILE), previous)
        self.assertEqual(self.cfg, previous)


class AsyncDetectionNotificationTests(unittest.TestCase):
    def test_notification_keeps_detached_polygon_vertices(self):
        polygon = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]
        # Run the worker after mutating the live selection to verify its snapshot.
        with patch.object(monitor.threading, "Thread") as worker, patch.object(monitor, "notify_detection") as notify:
            monitor.dispatch_detection_notification(["frame"], [], polygon, "polygon-event", ("", ""))
            polygon[0][0] = 0.7
            worker.call_args.kwargs["target"]()
        self.assertEqual(notify.call_args.args[2], [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])

    def test_slow_delivery_does_not_block_and_keeps_event_snapshot(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        received = []

        def slow_delivery(image, people, roi, request_id, credentials):
            started.set()
            release.wait(3)
            received.append((image, roi, request_id))
            finished.set()

        image, roi = ["original frame"], [0.1, 0.2, 0.8, 0.9]
        with patch.object(monitor, "notify_detection", side_effect=slow_delivery):
            try:
                monitor.dispatch_detection_notification(image, [], roi, "event-123", ("", ""))
                self.assertTrue(started.wait(2), "notification worker did not start")
                self.assertFalse(finished.is_set(), "dispatch blocked on slow notification")
                image[0] = "next frame"
                roi[0] = 0
            finally:
                release.set()
                self.assertTrue(finished.wait(2), "notification worker did not finish")
        self.assertEqual(received, [(["original frame"], [0.1, 0.2, 0.8, 0.9], "event-123")])


if __name__ == "__main__":
    unittest.main()
