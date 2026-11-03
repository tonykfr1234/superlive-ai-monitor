"""Camera recovery orchestration without restarting a real Android app."""
from pathlib import Path
import subprocess
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

    def test_closed_app_restarts_without_waiting_for_launcher_pixels_to_freeze(self):
        self.stream.client.run.return_value = b''
        def cycle():
            if not self.navigator.restore.called:
                self.stream.camera_frame_ready(self.changed)
            self.stream.stop_event.set()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.navigator.restore.assert_called_once()
        self.stream.client.screenshot.assert_not_called()
        self.assertLess(self.now, 60)

    def test_quality_worker_does_not_block_frame_publishing(self):
        import threading
        self.stream.adaptive_quality = True
        self.stream._quality_ready_at = 1
        self.now = 50
        entered, release = threading.Event(), threading.Event()
        worker = Mock()
        def select(level):
            entered.set()
            release.wait(2)
            return True
        worker.set_quality.side_effect = select
        with patch.object(adb_capture, 'AdbClient', return_value=worker):
            self.stream.apply_activity_quality()
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.stream._quality_busy.is_set())
            self.stream.publish(self.frame, 10)
            self.assertEqual(adb_capture.HEADER.unpack(bytes(self.stream.memory.buf[:adb_capture.HEADER.size]))[0],12)
            release.set()
            self.stream._quality_thread.join(2)
        self.assertFalse(self.stream._quality_busy.is_set())
        self.assertEqual(self.stream._quality_applied,'low')

    def test_fullscreen_toolbar_container_does_not_mask_camera(self):
        import xml.etree.ElementTree as ET
        tree = ET.fromstring('<hierarchy><node package="com.tvt.superliveplus" resource-id="com.tvt.superliveplus:id/video_menu_layout_land" bounds="[0,0][64,32]" /></hierarchy>')
        self.stream.quality_ui(tree)
        self.stream._quality_busy.set()
        frame = self.stream.camera_without_controls(self.frame)
        np.testing.assert_array_equal(frame[10:20], self.frame[10:20])

    def test_recorder_reads_full_frame_while_ai_input_is_masked(self):
        self.stream._quality_busy.set()
        self.stream.publish(self.changed, 0)
        recorder = adb_capture.SharedAdbReader('unused-camera-recovery-test')
        recorder.memory = self.stream.memory
        np.testing.assert_array_equal(recorder.capture(), self.changed)
        masked = self.stream.capture()
        self.assertTrue((masked[:3] == 51).all())
        self.assertTrue((masked[-5:] == 51).all())
        np.testing.assert_array_equal(masked[10:20], self.changed[10:20])

    def test_closed_app_with_no_decoded_frame_is_restarted_before_quality(self):
        self.stream.adaptive_quality = True
        self.stream.client.run.return_value = b''
        self.stream.stream_once = Mock(side_effect=lambda: self.stream.stop_event.set())
        self.stream.run()
        self.navigator.restore.assert_called_once()
        self.stream.client.set_quality.assert_not_called()

    def test_activity_mode_recovers_without_highest_quality_gate(self):
        self.stream.adaptive_quality = True
        self.stream._recovery_pending = True
        self.stream.stream_once = Mock(side_effect=lambda: self.stream.stop_event.set())
        self.stream.run()
        self.navigator.restore.assert_called_once()
        self.stream.client.highest_quality.assert_not_called()
        self.stream.stream_once.assert_called_once()

    def test_activity_quality_failure_still_allows_capture(self):
        self.stream.adaptive_quality = True
        self.now = 50
        self.stream._quality_ready_at = 1
        self.stream.client.set_quality.side_effect = RuntimeError('Menu unavailable')
        self.stream.stream_once = Mock(side_effect=lambda: self.stream.stop_event.set())
        self.stream.run()
        self.stream.stream_once.assert_called_once()
        self.stream.client.highest_quality.assert_not_called()

    def test_only_recent_detection_requests_high_quality(self):
        with patch.object(adb_capture.time, 'time', return_value=100), patch.object(adb_capture, 'read_json', return_value={'active': True, 'timestamp': 99}):
            self.now = 10
            self.assertEqual(self.stream.desired_quality(), 'high')
        with patch.object(adb_capture, 'read_json', return_value={'active': False, 'timestamp': 100}):
            self.now = 20
            self.assertEqual(self.stream.desired_quality(), 'high')
            self.now = 69
            self.assertEqual(self.stream.desired_quality(), 'high')
            self.now = 71
            self.assertEqual(self.stream.desired_quality(), 'low')

    def run_quality_events(self, results, states):
        """Drive the switch loop through camera_activity states (one per tick)."""
        self.stream.adaptive_quality = True
        self.stream._quality_ready_at = 1
        self.stream._quality_applied = 'low'
        worker = Mock()
        worker.set_quality.side_effect = results
        with patch.object(adb_capture, 'AdbClient', return_value=worker), \
             patch.object(adb_capture.time, 'time', side_effect=lambda: self.now):
            for active in states:
                self.now += 10
                with patch.object(adb_capture, 'read_json',
                                  return_value={'active': active, 'timestamp': self.now}):
                    self.stream._activity_read_at = None
                    self.stream.apply_activity_quality()
                    if self.stream._quality_thread:
                        self.stream._quality_thread.join(2)
        return [call.args[0] for call in worker.set_quality.call_args_list]

    def test_one_confirmed_switch_to_high_per_event(self):
        levels = self.run_quality_events([True] * 5, [True] * 6)
        self.assertEqual(levels, ['high'])
        self.assertEqual(self.stream._quality_applied, 'high')

    def test_failed_high_switch_stops_after_three_attempts(self):
        levels = self.run_quality_events([RuntimeError('Quality menu did not open')] * 5, [True] * 8)
        self.assertEqual(levels, ['high'] * 3)
        self.assertEqual(self.stream._quality_applied, 'low')
        self.assertIn('不再重試', self.stream.quality)

    def test_retry_succeeds_within_limit_then_stops_clicking(self):
        levels = self.run_quality_events([RuntimeError('x'), True, True], [True] * 6)
        self.assertEqual(levels, ['high', 'high'])
        self.assertEqual(self.stream._quality_attempts, 2)

    def test_next_event_gets_fresh_attempts_after_giving_up(self):
        fail = RuntimeError('x')
        # Event 1 fails three times; quiet long enough to end it; event 2 succeeds.
        levels = self.run_quality_events([fail, fail, fail, True],
                                         [True] * 4 + [False] * 7 + [True])
        self.assertEqual(levels, ['high'] * 4)
        self.assertEqual(self.stream._quality_event, 2)
        self.assertEqual(self.stream._quality_applied, 'high')

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

    def test_quality_check_waits_for_next_frame_then_gives_up(self):
        def arrive(_):
            self.now += 1
            if self.now == 2:
                self.stream._raw_frame, self.stream._raw_frame_at = self.changed, self.now
        self.stream._raw_frame, self.stream._raw_frame_at = self.frame, -10
        with patch.object(adb_capture.time, 'sleep', side_effect=arrive):
            self.assertTrue((self.stream.quality_frame() == self.changed).all())
            self.stream._raw_frame_at = -10
            with self.assertRaisesRegex(RuntimeError, 'No fresh compositor frame'):
                self.stream.quality_frame()

    def test_app_check_skipped_while_quality_worker_uses_adb(self):
        self.stream._quality_busy.set()
        self.stream.client.run.side_effect = subprocess.TimeoutExpired('adb', 3)
        self.assertTrue(self.stream.camera_frame_ready(self.changed))
        self.stream.client.run.assert_not_called()

    def test_slow_app_check_tolerated_until_three_consecutive_timeouts(self):
        self.stream.client.run.side_effect = subprocess.TimeoutExpired('adb', 3)
        for step in range(2):
            self.now = step * 5
            self.assertTrue(self.stream.camera_frame_ready(self.changed if step % 2 else self.frame))
        self.now = 10
        with self.assertRaises(subprocess.TimeoutExpired):
            self.stream.camera_frame_ready(self.frame)

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
        self.assertAlmostEqual(self.stream.health.stale_for(), 179.05)
        self.stream.client.select.assert_called_once()
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
