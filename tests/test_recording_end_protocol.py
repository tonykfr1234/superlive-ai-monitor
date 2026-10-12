"""Event end IPC validation without any live processes or camera access."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from record_protocol import recording_active, valid_end_request


class RecordingActiveTests(unittest.TestCase):
    def setUp(self):
        self.state = {"status": "RECORDING", "request_id": "event-one", "updated_at": 95, "pid": 123}

    def test_requires_fresh_live_recording_with_event_id(self):
        alive = Mock(return_value=True)
        self.assertTrue(recording_active(self.state, now=100, pid_alive=alive))
        alive.assert_called_once_with(123)
        self.assertTrue(recording_active(self.state, now=100))
        self.assertFalse(recording_active(self.state, now=100, pid_alive=lambda _: False))
        for status in ("IDLE", "STARTING", "PAUSED", "STOPPED", "ERROR", "UPLOADING"):
            with self.subTest(status=status):
                self.assertFalse(recording_active(dict(self.state, status=status), now=100))

    def test_state_age_matches_recorder_readiness_boundaries(self):
        self.assertTrue(recording_active(dict(self.state, updated_at=100), now=100))
        self.assertTrue(recording_active(dict(self.state, updated_at=90.001), now=100))
        for updated in (90, 89, 100.001, float("nan"), float("inf"), -float("inf"), "bad", None):
            with self.subTest(updated=updated):
                self.assertFalse(recording_active(dict(self.state, updated_at=updated), now=100))

    def test_malformed_ids_pids_and_state_are_rejected(self):
        for event_id in (None, "", 1, [], {}):
            with self.subTest(event_id=event_id):
                self.assertFalse(recording_active(dict(self.state, request_id=event_id), now=100))
        for pid in (None, 0, -1, True, 1.5, float("inf"), float("nan"), "bad"):
            with self.subTest(pid=pid):
                self.assertFalse(recording_active(dict(self.state, pid=pid), now=100))
        for state in (None, [], {}, {"status": "RECORDING", "request_id": "event-one"}):
            with self.subTest(state=state):
                self.assertFalse(recording_active(state, now=100))

    def test_default_clock_is_wall_time(self):
        with patch("record_protocol.time.time", return_value=100):
            self.assertTrue(recording_active(self.state))


class EndRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.request = {"request_id": "event-one", "created_at": 95, "reason": "NO_MOVEMENT"}

    def test_matching_inactivity_signal_has_ten_second_inclusive_lifetime(self):
        self.assertTrue(valid_end_request(self.request, "event-one", now=95))
        self.assertTrue(valid_end_request(self.request, "event-one", now=100))
        self.assertTrue(valid_end_request(self.request, "event-one", now=105))
        self.assertFalse(valid_end_request(self.request, "event-one", now=105.001))
        self.assertFalse(valid_end_request(self.request, "event-one", now=94.999))

    def test_other_events_and_other_end_reasons_are_rejected(self):
        self.assertFalse(valid_end_request(self.request, "next-event", now=100))
        for reason in (None, "", "NO_PERSON", "CAPTURE_ERROR", "STOPPED", "NO_MOVEMENT "):
            with self.subTest(reason=reason):
                self.assertFalse(valid_end_request(dict(self.request, reason=reason), "event-one", now=100))
        for event_id in (None, "", 1, [], {}):
            with self.subTest(event_id=event_id):
                self.assertFalse(valid_end_request(dict(self.request, request_id=event_id), event_id, now=100))

    def test_malformed_nonfinite_stale_and_future_timestamps_are_rejected(self):
        for created_at in (None, "bad", [], {}, True, float("nan"), float("inf"), -float("inf"), 89.999, 100.001):
            with self.subTest(created_at=created_at):
                self.assertFalse(valid_end_request(dict(self.request, created_at=created_at), "event-one", now=100))
        for request in (None, [], {}, {"request_id": "event-one", "reason": "NO_MOVEMENT"}):
            with self.subTest(request=request):
                self.assertFalse(valid_end_request(request, "event-one", now=100))

    def test_default_clock_is_wall_time(self):
        with patch("record_protocol.time.time", return_value=100):
            self.assertTrue(valid_end_request(self.request, "event-one"))

    def test_movement_requires_complete_fresh_distinct_successor(self):
        successor = {"request_id": "event-two", "created_at": 100,
                     "count": 1, "confidence": 0.8, "roi": [[0, 0], [1, 0], [0, 1]]}
        signal = dict(self.request, reason="MOVEMENT", next_request=successor)
        self.assertTrue(valid_end_request(signal, "event-one", now=100))
        for key, value in (("request_id", "event-one"), ("request_id", ""), ("request_id", " "),
                           ("request_id", 1), ("created_at", 89.9), ("created_at", 100.1),
                           ("created_at", float("nan")), ("count", 0), ("count", True),
                           ("count", 1.5), ("confidence", 0), ("confidence", 1.1),
                           ("confidence", float("inf")), ("roi", [[0, 0], [1, 1]])):
            with self.subTest(key=key, value=value):
                self.assertFalse(valid_end_request(dict(signal, next_request=dict(successor, **{key: value})), "event-one", now=100))
        for key in successor:
            incomplete = dict(successor)
            incomplete.pop(key)
            with self.subTest(missing=key):
                self.assertFalse(valid_end_request(dict(signal, next_request=incomplete), "event-one", now=100))
        self.assertFalse(valid_end_request(dict(signal, next_request=None), "event-one", now=100))


if __name__ == "__main__":
    unittest.main()
