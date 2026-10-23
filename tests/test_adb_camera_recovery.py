"""Camera recovery orchestration without restarting a real Android app."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import adb_capture
from stream_health import FrameStallDetector


class CameraRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.wait_hook = None
        self.events = []
        self.frame = np.full((32, 64, 3), 80, np.uint8)
        self.changed = self.frame.copy()
        self.changed[25:30, 50:54] = 240
        self.navigator = Mock()
        self.navigator.restore.return_value = True
        memory = Mock()
        memory.buf = bytearray(adb_capture.OFFSET + 64 * 64 * 3)
        config = {'ensure_highest': True, 'recovery': {
            'enabled': True, 'device_name': 'Demo recorder', 'channel': 4,
            'stale_seconds': 60, 'retry_seconds': 30}}
        with patch.object(adb_capture, 'settings', return_value=config), \
             patch.object(adb_capture, 'SuperLiveRecovery', return_value=self.navigator), \
             patch.object(adb_capture.shared_memory, 'SharedMemory', return_value=memory):
            self.stream = adb_capture.AdbStream('unused-camera-recovery-test')
        self.stream.client = Mock()
        self.stream.client.highest_quality.return_value = True
        self.stream.stop_event.wait = Mock(side_effect=self.wait)
        clock = patch.object(adb_capture.time, 'monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def wait(self, seconds):
        if self.stream.stop_event.is_set():
            return True
        self.now += seconds
        if self.wait_hook:
            self.wait_hook(seconds)
        return self.stream.stop_event.is_set()

    def screenshot(self):
        self.events.append('png')
        return self.frame.copy()

    def restore(self):
        self.events.append('restore')
        return True

    def stop_after_wait(self, _):
        self.stream.stop_event.set()

    def test_confirmation_uses_three_separate_png_samples(self):
        self.stream.client.screenshot.side_effect = self.screenshot
        self.assertTrue(self.stream.confirm_camera_stalled())
        self.assertEqual(self.stream.client.screenshot.call_count, 3)
        self.assertEqual([call.args for call in self.stream.stop_event.wait.call_args_list],
                         [(1.2,), (1.2,)])
        self.navigator.restore.assert_not_called()

    def test_png_motion_identifies_a_stalled_pipeline_without_restart(self):
        self.stream.health.observe(self.frame)
        self.now = 60
        self.stream.client.screenshot.side_effect = [self.frame.copy(), self.changed.copy()]
        calls = []
        def cycle():
            calls.append(True)
            if len(calls) == 1:
                self.stream.camera_frame_ready(self.frame)
            self.assertTrue(self.stream.camera_frame_ready(self.changed))
            self.stream.stop_event.set()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.assertEqual(self.stream.client.screenshot.call_count, 2)
        self.assertEqual(self.stream.stream_once.call_count, 2)
        self.navigator.restore.assert_not_called()
        self.assertIsNone(self.stream._capture_failure_since)
        self.assertFalse(self.stream._recovery_pending)

    def test_frozen_camera_is_probed_before_any_app_restore(self):
        self.stream.health.observe(self.frame)
        self.now = 60
        self.stream.client.screenshot.side_effect = self.screenshot
        self.navigator.restore.side_effect = self.restore
        def cycle():
            if self.navigator.restore.call_count == 0:
                self.stream.camera_frame_ready(self.frame)
            self.stream.stop_event.set()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.assertEqual(self.events, ['png', 'png', 'png', 'restore'])
        self.navigator.restore.assert_called_once()
        self.assertEqual(self.stream.client.highest_quality.call_count, 2)
        self.assertTrue(self.stream._awaiting_fresh_camera)

    def test_failed_independent_probe_does_not_immediately_restart_app(self):
        self.stream.health.observe(self.frame)
        self.now = 60
        self.stream.client.screenshot.side_effect = RuntimeError('Screenshot unavailable')
        self.stream.stream_once = Mock(side_effect=lambda: self.stream.camera_frame_ready(self.frame))
        self.wait_hook = self.stop_after_wait
        self.stream.run()
        self.navigator.restore.assert_not_called()
        self.assertFalse(self.stream._recovery_pending)

    def test_offline_adb_never_attempts_app_restart_even_after_timeout(self):
        self.stream.client.select.side_effect = RuntimeError('Configured device offline')
        self.stream.stream_once = Mock()
        def finish_after_retries(_):
            if self.stream.stop_event.wait.call_count >= 6:
                self.stream.stop_event.set()
        self.wait_hook = finish_after_retries
        self.stream.run()
        self.assertGreater(self.now, 60)
        self.navigator.restore.assert_not_called()
        self.stream.client.highest_quality.assert_not_called()
        self.stream.stream_once.assert_not_called()

    def test_persistent_capture_failures_promote_only_after_timeout(self):
        attempted_at = []
        def restore():
            attempted_at.append(self.now)
            return True
        self.navigator.restore.side_effect = restore
        def cycle():
            if not attempted_at:
                raise TimeoutError('Decoder stalled')
            self.stream.stop_event.set()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.assertEqual(len(attempted_at), 1)
        self.assertGreaterEqual(attempted_at[0], 60)
        self.assertEqual(self.stream.stream_once.call_count, 5)
        self.assertTrue(self.stream._awaiting_fresh_camera)

    def test_pending_recovery_respects_cooldown_and_invalidates_old_frame(self):
        self.stream.publish(self.frame, 0)
        self.stream._recovery_pending = True
        self.stream._last_recovery_at = 10
        self.now = 20
        self.assertTrue(self.stream.restore_camera_if_needed())
        self.stream.stop_event.wait.assert_called_once_with(20)
        self.assertEqual(self.stream._last_recovery_at, 40)
        self.assertEqual(bytes(self.stream.memory.buf[:adb_capture.HEADER.size]),
                         bytes(adb_capture.HEADER.size))
        self.assertTrue(self.stream._awaiting_fresh_camera)

    def test_cooldown_wait_is_interruptible(self):
        self.stream._recovery_pending = True
        self.stream._last_recovery_at = 0
        self.now = 5
        self.wait_hook = self.stop_after_wait
        with self.assertRaises(InterruptedError):
            self.stream.restore_camera_if_needed()
        self.navigator.restore.assert_not_called()

    def test_restore_failure_never_enters_quality_or_capture(self):
        self.stream.publish(self.frame, 0)
        self.stream._recovery_pending = True
        self.navigator.restore.return_value = False
        self.stream.stream_once = Mock()
        self.wait_hook = self.stop_after_wait
        self.stream.run()
        self.navigator.restore.assert_called_once()
        self.stream.client.highest_quality.assert_not_called()
        self.stream.stream_once.assert_not_called()
        self.assertTrue(self.stream._recovery_pending)
        self.assertEqual(bytes(self.stream.memory.buf[:adb_capture.HEADER.size]),
                         bytes(adb_capture.HEADER.size))

    def test_quality_failure_after_restore_never_enters_capture(self):
        self.stream._recovery_pending = True
        self.stream.client.highest_quality.return_value = False
        self.stream.stream_once = Mock()
        self.wait_hook = self.stop_after_wait
        self.stream.run()
        self.navigator.restore.assert_called_once()
        self.stream.stream_once.assert_not_called()
        self.assertTrue(self.stream._awaiting_fresh_camera)
        self.assertEqual(self.stream.status, 'ADB_QUALITY_UNCONFIRMED')

    def test_restored_identical_frames_are_withheld_until_change(self):
        self.stream._recovery_pending = True
        self.stream.restore_camera_if_needed()
        self.assertFalse(self.stream.camera_frame_ready(self.frame))
        self.now = 1
        self.assertFalse(self.stream.camera_frame_ready(self.frame.copy()))
        self.now = 2
        self.assertTrue(self.stream.camera_frame_ready(self.changed))
        self.assertFalse(self.stream._awaiting_fresh_camera)
        self.assertIsNone(self.stream._fresh_reference)

    def test_restored_camera_that_never_changes_times_out_again(self):
        self.stream._recovery_pending = True
        self.stream.restore_camera_if_needed()
        self.assertFalse(self.stream.camera_frame_ready(self.frame))
        self.now = 59
        self.assertFalse(self.stream.camera_frame_ready(self.frame.copy()))
        self.now = 60
        with self.assertRaises(adb_capture.CameraFrameStalled):
            self.stream.camera_frame_ready(self.frame)
        self.assertTrue(self.stream._awaiting_fresh_camera)

    def test_normal_stream_rollover_preserves_health_timer(self):
        self.stream.health = FrameStallDetector(timeout_seconds=200)
        self.stream.health.observe(self.frame)
        self.stream.health.reset = Mock(wraps=self.stream.health.reset)
        self.now = 179
        def cycle():
            if self.stream.stream_once.call_count == 2:
                self.stream.stop_event.set()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.stream.health.reset.assert_not_called()
        self.assertEqual(self.stream.health.stale_for(), 179.5)
        self.stream.client.highest_quality.assert_called_once()
        self.navigator.restore.assert_not_called()

    def test_stopped_stream_cannot_restart_app_or_probe(self):
        self.stream._recovery_pending = True
        self.stream.stop_event.set()
        self.stream.run()
        self.stream.client.select.assert_not_called()
        self.navigator.restore.assert_not_called()
        with self.assertRaises(InterruptedError):
            self.stream.restore_camera_if_needed()
        self.navigator.restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
