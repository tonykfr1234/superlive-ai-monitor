"""Plate child failure recovery and transient Windows IPC errors, no models."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import plate_recognition as plates
from record_protocol import atomic_json, read_json


class ControlLeaseTests(unittest.TestCase):
    def control(self, **values):
        return dict(session="current", heartbeat=100, active=True, stop=False, **values)

    def test_missing_and_malformed_control_are_retryable_then_expire(self):
        lease = plates.ControlLease("current", now=0)
        self.assertEqual(lease.inspect(self.control(), wall_now=100, now=0), "active")
        for bad in (None, {}, [], {"session": "current", "heartbeat": "bad"},
                    {"session": "current", "heartbeat": float("nan")},
                    {"session": "current", "heartbeat": True}):
            self.assertEqual(lease.inspect(bad, wall_now=101, now=1), "wait")
        self.assertEqual(lease.inspect(None, wall_now=113, now=13), "stop")

    def test_transient_read_failure_resumes_without_new_session(self):
        lease = plates.ControlLease("current", now=0)
        self.assertEqual(lease.inspect(self.control(), wall_now=100, now=1), "active")
        self.assertEqual(lease.inspect({}, wall_now=101, now=2), "wait")
        self.assertEqual(lease.inspect(self.control(), wall_now=102, now=3), "active")
        self.assertEqual(lease.inspect(None, wall_now=114, now=14), "wait")

    def test_explicit_stop_replacement_and_pause_are_distinct(self):
        lease = plates.ControlLease("current", now=0)
        self.assertEqual(lease.inspect(dict(self.control(), stop=True), wall_now=100, now=0), "stop")
        self.assertEqual(lease.inspect(dict(self.control(), session="new"), wall_now=100, now=0), "stop")
        self.assertEqual(lease.inspect(dict(self.control(), active=False), wall_now=100, now=0), "paused")
        self.assertEqual(lease.inspect(dict(self.control(), session="new", heartbeat="bad"), wall_now=100, now=0), "wait")


class SampleReadTests(unittest.TestCase):
    def setUp(self):
        self.control = dict(session="s", generation=1, active=True, heartbeat=100)
        self.metadata = dict(session="s", generation=1, timestamp=100, sample_id="frame-1", roi=None)
        self.frame = np.arange(27, dtype=np.uint8).reshape(3, 3, 3)

    def test_brief_open_lock_recovers_and_closes_real_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"sample.npz"
            np.savez(path, frame=self.frame, metadata=json.dumps(self.metadata))
            original_load, opened = np.load, []
            def load(*args, **kwargs):
                if not opened:
                    opened.append(None)
                    raise PermissionError("fixture sharing lock")
                archive = original_load(*args, **kwargs)
                opened.append(archive)
                return archive
            with patch.object(np, "load", side_effect=load) as mocked, patch.object(plates.time, "sleep") as sleep:
                frame, metadata = plates.read_plate_sample(path, self.control, 100)
            np.testing.assert_array_equal(frame, self.frame)
            self.assertEqual(metadata, self.metadata)
            self.assertEqual(mocked.call_count, 2)
            sleep.assert_called_once_with(.02)
            self.assertIsNone(opened[1].zip)

    def test_lazy_read_and_copy_locks_close_before_retry(self):
        for failing_stage in ("metadata", "frame", "copy"):
            with self.subTest(stage=failing_stage):
                archives = []
                metadata, frame = self.metadata, self.frame
                class Archive:
                    def __init__(self, fails):
                        self.fails, self.closed = fails, False
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        self.closed = True
                    def __getitem__(self, name):
                        if self.fails and name == failing_stage:
                            raise PermissionError("fixture lazy read lock")
                        if name == "metadata":
                            return np.array(json.dumps(metadata))
                        if self.fails and failing_stage == "copy":
                            return SimpleNamespace(copy=Mock(side_effect=PermissionError("fixture copy lock")))
                        return frame
                def load(*args, **kwargs):
                    archive = Archive(not archives)
                    archives.append(archive)
                    return archive
                def wait(delay):
                    self.assertEqual(delay, .02)
                    self.assertTrue(all(archive.closed for archive in archives))
                with patch.object(np, "load", side_effect=load), patch.object(plates.time, "sleep", side_effect=wait):
                    result, actual = plates.read_plate_sample("fixture.npz", self.control, 100)
                np.testing.assert_array_equal(result, frame)
                self.assertEqual(actual, metadata)
                self.assertEqual(len(archives), 2)
                self.assertTrue(all(archive.closed for archive in archives))

    def test_persistent_lock_is_bounded_and_still_raises(self):
        error = PermissionError("fixture persistent lock")
        with patch.object(np, "load", side_effect=error) as load, patch.object(plates.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                plates.read_plate_sample("fixture.npz", self.control, 100)
        self.assertIs(raised.exception, error)
        self.assertEqual(load.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [.02, .04])

    def test_other_read_errors_are_never_retried(self):
        for error in (FileNotFoundError("missing"), ValueError("bad archive"), KeyError("metadata")):
            with self.subTest(error=type(error).__name__), \
                 patch.object(np, "load", side_effect=error) as load, patch.object(plates.time, "sleep") as sleep:
                with self.assertRaises(type(error)):
                    plates.read_plate_sample("fixture.npz", self.control, 100)
                load.assert_called_once()
                sleep.assert_not_called()

    def test_duplicate_stale_and_changed_source_samples_stay_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"sample.npz"
            np.savez(path, frame=self.frame, metadata=json.dumps(self.metadata))
            cases = ((self.control, 100, "frame-1"), (self.control, 200, None),
                     (dict(self.control, generation=2), 100, None),
                     (dict(self.control, session="new"), 100, None))
            with patch.object(plates.time, "sleep") as sleep:
                for control, now, last in cases:
                    self.assertIsNone(plates.read_plate_sample(path, control, now, last))
                sleep.assert_not_called()


class WorkerSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        executable = self.root / ".plate-venv" / "Scripts" / "python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.service = plates.PlateService(self.root)
        self.service.runtime.mkdir(parents=True)
        self.children = []
        def spawn(*args, **kwargs):
            child = Mock()
            child.poll.return_value = None
            child.wait.return_value = 0
            self.children.append(child)
            return child
        p = patch.object(plates.subprocess, "Popen", side_effect=spawn)
        self.spawn = p.start()
        self.addCleanup(p.stop)
        p = patch.object(plates.time, "time", return_value=100)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.service.stop)

    def state(self, **extra):
        data = dict(session=self.service.session, status="READY", error="", updated_at=100, recent=[])
        data.update(extra)
        atomic_json(self.service.runtime / "state.json", data)

    def test_start_only_schedules_background_work(self):
        with patch.object(plates.threading, "Thread") as thread:
            self.assertTrue(self.service.start())
            self.spawn.assert_not_called()
            thread.return_value.start.assert_called_once()

    def test_exited_child_restarts_after_delay_with_new_session_and_no_old_sample(self):
        self.service._supervise(now=0)
        old = self.service.session
        self.state()
        old_frame = self.service.runtime / "sample.npz"
        old_frame.write_bytes(b"old private sample")
        self.service._latest = (object(), {"session": old})
        self.children[0].poll.return_value = 1
        self.service._supervise(now=1)
        self.assertEqual(self.service._status, "RESTARTING")
        self.assertIsNone(self.service._latest)
        self.service._supervise(now=5)
        self.assertEqual(len(self.children), 1)
        self.service._supervise(now=6)
        self.assertEqual(len(self.children), 2)
        self.assertNotEqual(old, self.service.session)
        self.assertFalse(old_frame.exists())
        control = read_json(self.service.runtime / "control.json")
        self.assertEqual(control["session"], self.service.session)
        self.assertFalse(control["active"])

    def test_hung_worker_is_terminated_and_never_shown_ready(self):
        self.service._supervise(now=0)
        self.state(updated_at=60)
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["status"], "RESTARTING")
        self.assertIn("stale", snapshot["error"])
        self.service._supervise(now=1)
        self.children[0].terminate.assert_called_once()
        self.assertEqual(self.service._status, "RESTARTING")

    def test_startup_grace_and_bounded_backoff(self):
        self.service._supervise(now=0)
        self.service._supervise(now=plates.PlateService.START_TIMEOUT-1)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.service._retry_count, 0)
        self.service._supervise(now=plates.PlateService.START_TIMEOUT+1)
        self.assertEqual(self.service._retry_count, 1)
        self.service._retry_count = 100
        self.service._schedule_restart(200, "fixture failure")
        self.assertEqual(self.service._retry_at, 260)

    def test_stop_during_backoff_cannot_create_another_child(self):
        self.service._supervise(now=0)
        self.children[0].poll.return_value = 1
        self.service._supervise(now=1)
        self.service.stop()
        self.service._supervise(now=1000)
        self.service._spawn_worker(1000)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.service.snapshot()["status"], "STOPPED")

    def test_sample_write_error_is_visible_and_clears_after_next_good_sample(self):
        self.service._supervise(now=0)
        self.state()
        self.service._active = True
        self.service._latest = (np.zeros((2, 2, 3), np.uint8), {})
        with patch.object(self.service, "_supervise"), patch.object(self.service, "_write_sample", side_effect=PermissionError):
            self.service._io_cycle()
        self.assertEqual(self.service.snapshot()["status"], "ERROR")
        self.assertIn("PermissionError", self.service.snapshot()["error"])
        with patch.object(self.service, "_supervise"):
            self.service._io_cycle()
        self.assertIn("PermissionError", self.service.snapshot()["error"])
        self.service._latest = (np.zeros((2, 2, 3), np.uint8), {})
        with patch.object(self.service, "_supervise"), patch.object(self.service, "_write_sample"):
            self.service._io_cycle()
        self.assertEqual(self.service.snapshot()["error"], "")

    def test_fresh_frames_are_accepted_while_waiting_for_child_restart(self):
        self.service._thread = Mock()
        self.service._status = "RESTARTING"
        self.assertTrue(self.service.submit(np.zeros((2, 2, 3), np.uint8), now=100, roi=None))
        self.assertIsNone(self.service._latest[1]["roi"])

    def test_old_session_cannot_report_ready_after_restart(self):
        self.service._supervise(now=0)
        self.service._snapshot = dict(session="old", status="READY", error="", updated_at=100, recent=[])
        with patch.object(plates.time, "monotonic", return_value=1):
            self.assertEqual(self.service.snapshot()["status"], "STARTING")


class ProcessTreeStopTests(unittest.TestCase):
    def fixture(self):
        service = plates.PlateService("unused-fixture-root")
        launcher = Mock(pid=4242)
        launcher.poll.return_value = None
        launcher.wait.return_value = 0
        service._process = launcher
        child = Mock()
        owner = Mock()
        owner.children.return_value = [child]
        psutil = SimpleNamespace(Error=RuntimeError, Process=Mock(return_value=owner),
                                 wait_procs=Mock(side_effect=[([], [child]), ([child], [])]))
        return service, launcher, child, owner, psutil

    def test_hung_launcher_stops_only_its_captured_descendants(self):
        service, launcher, child, owner, psutil = self.fixture()
        with patch.dict(sys.modules, {"psutil": psutil}):
            service._stop_process()
        psutil.Process.assert_called_once_with(launcher.pid)
        owner.children.assert_called_once_with(recursive=True)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        launcher.terminate.assert_called_once()
        self.assertIsNone(service._process)

    def test_graceful_launcher_exit_still_reaps_its_surviving_child(self):
        service, launcher, child, owner, psutil = self.fixture()
        launcher.poll.side_effect = [None, 0]
        with patch.dict(sys.modules, {"psutil": psutil}):
            service._stop_process(graceful=True)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        launcher.terminate.assert_not_called()

    def test_missing_optional_import_still_stops_the_launcher(self):
        service, launcher, _, _, _ = self.fixture()
        with patch.dict(sys.modules, {"psutil": None}):
            service._stop_process()
        launcher.terminate.assert_called_once()
        launcher.wait.assert_called_once()

    def test_all_waits_share_one_shutdown_deadline(self):
        service, launcher, child, _, psutil = self.fixture()
        launcher.wait.side_effect = [plates.subprocess.TimeoutExpired("fixture", 2),
                                     plates.subprocess.TimeoutExpired("fixture", 0), 0]
        with patch.dict(sys.modules, {"psutil": psutil}), patch.object(
                plates.time, "monotonic", side_effect=[10, 11.9, 15.5, 16, 16, 16]):
            service._stop_process(graceful=True)
        self.assertEqual([call.kwargs["timeout"] for call in launcher.wait.call_args_list], [2, 0, 0])
        self.assertEqual([call.kwargs["timeout"] for call in psutil.wait_procs.call_args_list], [.5, 0])
        launcher.kill.assert_called_once()
        child.kill.assert_called_once()


class WorkerLoopTests(unittest.TestCase):
    def test_periodic_heartbeat_does_not_hide_persistent_inference_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "plate_recognition"
            runtime.mkdir(parents=True)
            metadata = dict(session="s", generation=1, sample_id="a", timestamp=100, roi=None)
            np.savez(runtime / "sample.npz", frame=np.zeros((2, 2, 3), np.uint8), metadata=json.dumps(metadata))
            states, clock, controls = [], [100], [0]
            def read(path, default=None):
                if Path(path).name == "control.json":
                    controls[0] += 1
                    return dict(session="s", heartbeat=clock[0], generation=1,
                                active=True, stop=controls[0] >= 3)
                return [] if Path(path).name in ("recent.json", "plate_allowlist.json") else default
            def fail_inference(frame):
                clock[0] = 104
                raise RuntimeError("fixture model failure")
            ort = SimpleNamespace(SessionOptions=lambda: SimpleNamespace())
            alpr = SimpleNamespace(ALPR=Mock(return_value=SimpleNamespace(predict=Mock(side_effect=fail_inference))))
            with patch.dict(sys.modules, {"onnxruntime": ort, "fast_alpr": alpr}), \
                 patch.object(plates, "read_json", side_effect=read), \
                 patch.object(plates, "atomic_json", side_effect=lambda path, state: states.append(dict(state)) if Path(path).name == "state.json" else None), \
                 patch.object(plates.time, "time", side_effect=lambda: clock[0]), \
                 patch.object(plates.time, "sleep"):
                self.assertEqual(plates.run_worker(root, "s"), 0)
            first_error = next(i for i, state in enumerate(states) if state["status"] == "ERROR")
            self.assertNotIn("READY", [state["status"] for state in states[first_error:]])
            self.assertEqual(states[-1]["processed_frames"], 0)

    def test_transient_control_read_and_state_write_do_not_end_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            controls = [dict(session="s", heartbeat=100, active=False), None,
                        dict(session="s", heartbeat=100, active=False),
                        dict(session="s", heartbeat=100, active=False, stop=True)]
            def read(path, default=None):
                if Path(path).name == "control.json":
                    return controls.pop(0)
                return [] if Path(path).name in ("recent.json", "plate_allowlist.json") else default
            failures = [True]
            def write(path, state):
                if Path(path).name != "state.json":
                    return
                calls.append(dict(state))
                if failures:
                    failures.pop()
                    raise PermissionError("fixture Windows sharing lock")
            ort = SimpleNamespace(SessionOptions=lambda: SimpleNamespace())
            fake_alpr = SimpleNamespace(ALPR=Mock(return_value=SimpleNamespace(predict=Mock())))
            with patch.dict(sys.modules, {"onnxruntime": ort, "fast_alpr": fake_alpr}), \
                 patch.object(plates, "read_json", side_effect=read), \
                 patch.object(plates, "atomic_json", side_effect=write), \
                 patch.object(plates.time, "time", return_value=100), \
                 patch.object(plates.time, "monotonic", return_value=0), \
                 patch.object(plates.time, "sleep"):
                self.assertEqual(plates.run_worker(root, "s"), 0)
            self.assertEqual(controls, [])
            self.assertEqual(calls[-1]["status"], "STOPPED")
            fake_alpr.ALPR.return_value.predict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
