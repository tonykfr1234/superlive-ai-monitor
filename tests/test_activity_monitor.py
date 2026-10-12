"""Headless integration of activity decisions, event IPC, and the monitor loop."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_presence_monitor import BASELINE, load_headless_monitor
from record_protocol import atomic_json, read_json


monitor = load_headless_monitor()


class FakeFrame:
    shape = (200, 400, 3)

    def copy(self):
        return self


class ActivityMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=BASELINE.parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1000.0
        self.event_started = {}
        self.cfg = dict(monitor.DEFAULT_POLICY, roi_coordinate_space="mumu_content_v1")
        self.people = [{"box": (40, 40, 100, 180), "confidence": 0.9}]
        self.moved = [{"box": (70, 40, 130, 180), "confidence": 0.9}]
        self.image = FakeFrame()
        replacements = {
            "ROOT": self.root, "RUNTIME": self.root / "runtime",
            "EVENT_ROOT": self.root / "events", "MODEL_PATH": self.root / "fake-model.pt",
            "POLICY_FILE": self.root / "policy.json", "TELEGRAM_ENV": self.root / "telegram.env",
            "REQUEST_FILE": self.root / "record_request.json",
            "END_REQUEST_FILE": self.root / "record_end_request.json",
            "STATE_FILE": self.root / "recording_state.json", "STOP_FLAG": self.root / "stop.flag",
        }
        for name, value in replacements.items():
            self.patch(monitor, name, value)
        self.patch(monitor.time, "time", lambda: self.now)
        self.patch(monitor.time, "monotonic", lambda: self.now)
        self.patch(monitor.time, "sleep", self.sleep)
        self.alive = self.patch(monitor.psutil, "pid_exists", Mock(return_value=True))
        self.notification = self.patch(monitor, "dispatch_detection_notification", Mock())
        self.session = monitor.DetectionSession()

    def patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def sleep(self, seconds):
        self.now += max(seconds, 0.00001)

    def state(self, status="RECORDING", event="one", age=0, elapsed=None):
        if elapsed is not None:
            self.event_started[event] = self.now-elapsed
        started = self.event_started.setdefault(event, self.now)
        state = {"status": status, "request_id": event, "updated_at": self.now-age,
                 "pid": 123, "elapsed_seconds": self.now-started}
        atomic_json(monitor.STATE_FILE, state)
        return state

    def observe(self, people=None, status="RECORDING", event="one", state=None):
        if people is None:
            people = self.people
        state = self.state(status, event) if state is None else state
        return self.session.observe(self.image, people, self.cfg, ("", ""), state, 400, 200)

    def quiet_seconds(self, seconds, people=None, event="one"):
        outcomes = []
        for _ in range(seconds):
            self.now += 1
            outcomes.append(self.observe(people=people, event=event))
        return outcomes

    def test_stationary_person_finishes_after_two_minutes_and_motion_restarts_once(self):
        self.assertEqual(self.observe(status="IDLE", event=None), "recording_started")
        first_id = read_json(monitor.REQUEST_FILE)["request_id"]
        self.assertIsNone(self.observe(status="IDLE", event=None))
        self.assertEqual(self.notification.call_count, 1)
        monitor.REQUEST_FILE.unlink()
        self.assertIsNone(self.observe(event=first_id))
        self.assertTrue(all(outcome is None for outcome in self.quiet_seconds(119, event=first_id)))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertEqual(self.quiet_seconds(1, event=first_id), ["recording_end_requested"])
        self.assertEqual(read_json(monitor.END_REQUEST_FILE)["request_id"], first_id)
        self.assertEqual(read_json(monitor.END_REQUEST_FILE)["reason"], "NO_MOVEMENT")
        self.assertEqual(self.notification.call_count, 1)
        self.now += 0.1
        self.assertIsNone(self.observe(status="IDLE", event=None))
        self.assertFalse(monitor.REQUEST_FILE.exists())
        self.assertEqual(self.observe(people=self.moved, status="IDLE", event=None), "recording_started")
        self.assertNotEqual(read_json(monitor.REQUEST_FILE)["request_id"], first_id)
        self.assertEqual(self.notification.call_count, 2)
        self.assertIsNone(self.observe(people=self.moved, status="IDLE", event=None))
        self.assertEqual(self.notification.call_count, 2)

    def test_motion_during_recording_resets_the_full_inactivity_interval(self):
        self.observe()
        self.quiet_seconds(99)
        self.now += 1
        self.assertIsNone(self.observe(people=self.moved))
        self.assertTrue(all(outcome is None for outcome in self.quiet_seconds(119, people=self.moved)))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertEqual(self.quiet_seconds(1, people=self.moved), ["recording_end_requested"])
        self.notification.assert_not_called()

    def test_successful_empty_detections_require_the_whole_two_minutes(self):
        self.assertIsNone(self.observe(people=[]))
        self.quiet_seconds(119, people=[])
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertEqual(self.quiet_seconds(1, people=[]), ["recording_end_requested"])
        self.notification.assert_not_called()
        self.assertFalse(monitor.REQUEST_FILE.exists())

    def test_unknown_observation_suspends_continuity(self):
        self.observe()
        self.quiet_seconds(119)
        self.session.suspend()
        self.now += 60
        self.assertIsNone(self.observe())
        self.quiet_seconds(119)
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertEqual(self.quiet_seconds(1), ["recording_end_requested"])

    def test_new_event_does_not_inherit_old_events_inactivity(self):
        self.observe()
        self.quiet_seconds(119)
        self.now += 1
        self.assertIsNone(self.observe(event="two"))
        self.quiet_seconds(119, event="two")
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertEqual(self.quiet_seconds(1, event="two"), ["recording_end_requested"])
        self.assertEqual(read_json(monitor.END_REQUEST_FILE)["request_id"], "two")

    def test_snapshot_rollover_and_stale_state_cannot_finish_a_new_event(self):
        self.observe()
        self.quiet_seconds(119)
        self.now += 1
        previous = self.state(event="one")
        self.state(event="two")
        self.assertIsNone(self.observe(state=previous))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())

        self.assertFalse(monitor.request_recording_end(previous))
        stale = self.state(event="two", age=10)
        self.assertIsNone(self.observe(state=stale))
        self.assertFalse(monitor.can_observe_activity(stale))
        self.assertFalse(monitor.request_recording_end(stale))
        self.state(event="two")
        self.assertFalse(monitor.request_recording_end(stale))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())

    def test_idle_snapshot_cannot_start_after_recorder_becomes_busy(self):
        before = self.state(status="IDLE", event=None)
        self.state(status="RECORDING", event="new-event")
        self.assertIsNone(self.observe(state=before))
        self.assertFalse(monitor.REQUEST_FILE.exists())
        self.notification.assert_not_called()

    def test_duplicate_end_is_suppressed_and_stale_signal_can_be_refreshed(self):
        state = self.state(elapsed=120)
        self.assertTrue(monitor.request_recording_end(state))
        original = read_json(monitor.END_REQUEST_FILE)
        self.now += 1
        self.assertFalse(monitor.request_recording_end(self.state()))
        self.assertEqual(read_json(monitor.END_REQUEST_FILE), original)
        self.now += 10
        self.assertTrue(monitor.request_recording_end(self.state()))
        self.assertGreater(read_json(monitor.END_REQUEST_FILE)["created_at"], original["created_at"])

    def test_movement_before_minimum_never_rolls_over(self):
        self.observe()
        self.now += 119.99
        self.assertIsNone(self.observe(people=self.moved))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.notification.assert_not_called()

    def test_movement_at_two_minutes_reserves_new_event_once(self):
        self.observe()
        self.now += 120
        self.assertEqual(self.observe(people=self.moved), "recording_rollover_requested")
        end = read_json(monitor.END_REQUEST_FILE)
        self.assertEqual(end["request_id"], "one")
        self.assertEqual(end["reason"], "MOVEMENT")
        successor = end["next_request"]
        self.assertNotEqual(successor["request_id"], "one")
        self.assertEqual(successor["count"], 1)
        self.assertEqual(successor["confidence"], .9)
        self.assertFalse(monitor.REQUEST_FILE.exists())
        self.assertIsNone(self.observe(people=self.people))
        self.assertEqual(read_json(monitor.END_REQUEST_FILE), end)
        self.notification.assert_called_once()

    def test_new_clip_restarts_minimum_even_after_previous_rollover(self):
        self.observe()
        self.now += 120
        self.assertEqual(self.observe(people=self.moved), "recording_rollover_requested")
        next_id = read_json(monitor.END_REQUEST_FILE)["next_request"]["request_id"]
        monitor.END_REQUEST_FILE.unlink()
        self.assertIsNone(self.observe(people=self.moved, event=next_id))
        self.now += 119
        self.assertIsNone(self.observe(people=self.people, event=next_id))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.now += 1
        self.assertEqual(self.observe(people=self.moved, event=next_id), "recording_rollover_requested")
        self.assertEqual(read_json(monitor.END_REQUEST_FILE)["request_id"], next_id)

    def test_recorder_maximum_has_priority_over_movement_rollover(self):
        self.observe()
        self.now += 300
        self.assertIsNone(self.observe(people=self.moved))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())

    def test_elapsed_missing_invalid_or_younger_snapshot_cannot_end(self):
        for elapsed in (None, -1, float("nan"), float("inf"), True, "bad", 119.9):
            state = self.state(elapsed=120)
            state["elapsed_seconds"] = elapsed
            self.assertFalse(monitor.request_recording_end(state))
        observed = self.state(elapsed=120)
        self.state(elapsed=119.9)
        self.assertFalse(monitor.request_recording_end(observed))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())

    def test_local_start_evidence_never_sends_telegram_even_with_credentials(self):
        with patch.object(monitor.cv2, "imwrite", return_value=True), patch("requests.post") as network:
            monitor.notify_detection(self.image, self.people, None, "local-only",
                                     ("configured-bot", "configured-chat"))
        network.assert_not_called()
        self.assertTrue((monitor.EVENT_ROOT / "person_events.csv").exists())

    def test_starting_pending_and_stop_states_cannot_trigger_or_end(self):
        for status in ("STARTING", "PAUSED", "STOPPED", "ERROR"):
            with self.subTest(status=status):
                state = self.state(status=status)
                self.assertFalse(monitor.can_observe_activity(state))
                self.assertIsNone(self.observe(state=state))
        atomic_json(monitor.REQUEST_FILE, {"request_id": "pending", "created_at": self.now})
        idle = self.state(status="IDLE", event=None)
        self.assertFalse(monitor.can_observe_activity(idle))
        self.assertIsNone(self.observe(state=idle))
        monitor.REQUEST_FILE.unlink()
        active = self.state()
        self.assertTrue(monitor.can_observe_activity(active))
        monitor.STOP_FLAG.write_text("STOP", encoding="utf-8")
        self.assertFalse(monitor.can_observe_activity(active))
        self.assertIsNone(self.observe(state=active))
        self.assertFalse(monitor.request_recording_end(active))
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        self.assertFalse(monitor.REQUEST_FILE.exists())
        self.notification.assert_not_called()

    def run_fake_main(self, capture_error=None, select_after_inference=False):
        # Prime an expired timer: any mistaken empty observation would end now.
        self.observe(people=[])
        self.now += 120
        self.state()
        self.patch(monitor, "DetectionSession", lambda: self.session)
        self.patch(monitor, "set_dpi_awareness", Mock())
        self.patch(monitor, "load_policy", lambda: self.cfg)
        self.patch(monitor, "load_telegram", lambda: ("", ""))
        self.patch(monitor, "find_mumu", lambda: 1)
        self.patch(monitor.win32gui, "IsWindow", lambda _: True)
        self.patch(monitor.win32gui, "IsIconic", lambda _: False)
        self.patch(monitor.torch.cuda, "is_available", lambda: False)
        self.patch(monitor.np, "zeros", lambda *args, **kwargs: self.image)
        capture = SimpleNamespace(close=Mock())
        self.patch(monitor, "new_mss", lambda: capture)
        info = SimpleNamespace(parent_client_rect=(0, 0, 400, 200),
                               content_rect=(0, 0, 400, 200), method="synthetic")
        def capture_frame(*args, **kwargs):
            self.state()
            if capture_error:
                raise capture_error
            return self.image, info
        self.patch(monitor, "capture_frame", capture_frame)
        model = SimpleNamespace(predict=Mock(return_value=[SimpleNamespace(boxes=None)]))
        self.patch(monitor, "YOLO", Mock(return_value=model))
        controller = SimpleNamespace(roi=None, selecting=False, layout=None, key=Mock(return_value=None))
        if select_after_inference:
            def key(_):
                controller.selecting = True
                return None
            controller.key = key
        self.patch(monitor, "PreviewController", lambda roi: controller)
        self.patch(monitor, "render_preview", Mock(return_value=self.image))
        self.patch(monitor.cv2, "getWindowImageRect", lambda _: (0, 0, 960, 720))
        self.patch(monitor.cv2, "getWindowProperty", lambda *args: 1)
        rendered = [0]
        def wait_key(_):
            rendered[0] += 1
            if rendered[0] == 2:
                monitor.STOP_FLAG.touch()  # End only the synthetic loop.
            return -1
        self.patch(monitor.cv2, "waitKey", wait_key)
        monitor.main()
        self.assertEqual(rendered[0], 2)
        capture.close.assert_called_once()
        return model, controller

    def test_main_predicts_during_recording_and_processes_successful_empty_result(self):
        model, _ = self.run_fake_main()
        self.assertGreaterEqual(model.predict.call_count, 1)
        self.assertEqual(read_json(monitor.END_REQUEST_FILE)["reason"], "NO_MOVEMENT")
        self.notification.assert_not_called()

    def test_main_unknown_capture_cannot_end_even_with_previously_elapsed_timer(self):
        model, _ = self.run_fake_main(capture_error=monitor.CaptureRegionUnavailable("synthetic unavailable"))
        model.predict.assert_not_called()
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        monitor.STOP_FLAG.unlink()
        self.now += 1
        self.assertIsNone(self.observe(people=[]), "Recovery must begin a fresh observable interval")
        self.assertFalse(monitor.END_REQUEST_FILE.exists())

    def test_main_selection_discards_inference_before_it_can_end_current_event(self):
        model, controller = self.run_fake_main(select_after_inference=True)
        self.assertTrue(controller.selecting)
        self.assertEqual(model.predict.call_count, 1)
        self.assertFalse(monitor.END_REQUEST_FILE.exists())
        monitor.STOP_FLAG.unlink()
        self.now += 1
        self.assertIsNone(self.observe(people=[]), "Leaving selection must restart the inactivity interval")
        self.assertFalse(monitor.END_REQUEST_FILE.exists())


if __name__ == "__main__":
    unittest.main()
