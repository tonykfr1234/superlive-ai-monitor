"""Connection recovery without an emulator, including blocked pipe reads."""
import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import adb_capture


class DeviceConnectionTests(unittest.TestCase):
    def test_configured_tcp_device_connects_and_rechecks_online_state(self):
        client = adb_capture.AdbClient({'serial': '127.0.0.1:12345'})
        client.run = Mock(side_effect=[b'List of devices attached\n', b'connected\n',
                                      b'List of devices attached\n127.0.0.1:12345\tdevice\n'])
        client.select()
        self.assertEqual(client.run.call_args_list[1].args, ('connect', client.serial))
        self.assertEqual(client.run.call_count, 3)

    def test_adb_zero_exit_with_failed_connect_does_not_count_as_connected(self):
        client = adb_capture.AdbClient({'serial': '127.0.0.1:12345'})
        client.run = Mock(side_effect=[b'List of devices attached\n', b'failed to connect\n',
                                      b'List of devices attached\n127.0.0.1:12345\toffline\n'])
        with self.assertRaisesRegex(RuntimeError, 'not connected'):
            client.select()

    def test_never_guesses_other_devices_or_connects_usb_identifiers(self):
        for serial in ('', 'usb-serial'):
            client = adb_capture.AdbClient({'serial': serial})
            client.run = Mock(return_value=b'List of devices attached\none\tdevice\ntwo\tdevice\n')
            with self.assertRaises(RuntimeError):
                client.select()
            self.assertEqual(client.run.call_count, 1)

    def test_already_online_device_needs_no_connect(self):
        client = adb_capture.AdbClient({'serial': '127.0.0.1:12345'})
        client.run = Mock(return_value=b'List of devices attached\n127.0.0.1:12345\tdevice\n')
        client.select()
        self.assertEqual(client.run.call_count, 1)


class RetryTests(unittest.TestCase):
    def setUp(self):
        memory = Mock()
        memory.buf = bytearray(adb_capture.OFFSET + 256)
        with patch.object(adb_capture.shared_memory, 'SharedMemory', return_value=memory):
            self.stream = adb_capture.AdbStream('unused-test-root')
        self.stream.client = Mock()
        self.stream.client.highest_quality.return_value = True
        self.wait = Mock(return_value=False)
        self.stream.stop_event.wait = self.wait

    def finish_stream(self):
        self.stream.stop_event.set()

    def test_missing_emulator_at_start_recovers_without_restart(self):
        self.stream.client.select.side_effect = [RuntimeError('offline'), None]
        self.stream.stream_once = Mock(side_effect=self.finish_stream)
        self.stream.run()
        self.assertEqual(self.stream.client.select.call_count, 2)
        self.stream.stream_once.assert_called_once()
        self.assertEqual(self.wait.call_args_list[0].args, (5,))

    def test_false_and_exception_quality_checks_retry_until_verified(self):
        self.stream.client.highest_quality.side_effect = [False, RuntimeError('UI busy'), True]
        self.stream.stream_once = Mock(side_effect=self.finish_stream)
        self.stream.run()
        self.assertEqual(self.stream.client.highest_quality.call_count, 3)
        self.stream.stream_once.assert_called_once()
        self.assertEqual(self.stream.quality, '最高（已確認）')
        self.assertEqual([c.args[0] for c in self.wait.call_args_list[:2]], [5, 10])

    def test_backoff_is_bounded_and_stop_interrupts_retry(self):
        self.stream.client.select.side_effect = RuntimeError('offline')
        def wait(delay):
            if self.wait.call_count == 6:
                self.stream.stop_event.set()
        self.wait.side_effect = wait
        self.stream.run()
        self.assertEqual([c.args[0] for c in self.wait.call_args_list], [5, 10, 20, 30, 30, 30])
        self.stream.client.highest_quality.assert_not_called()

    def test_normal_rollover_does_not_reopen_quality_menu(self):
        self.stream.stream_once = Mock()
        def cycle():
            if self.stream.stream_once.call_count == 2:
                self.finish_stream()
        self.stream.stream_once.side_effect = cycle
        self.stream.run()
        self.assertEqual(self.stream.stream_once.call_count, 2)
        self.stream.client.highest_quality.assert_called_once()

    def test_offline_after_stream_failure_rechecks_quality_on_return(self):
        self.stream.client.select.side_effect = [None, RuntimeError('offline'), None]
        def cycle():
            if self.stream.stream_once.call_count == 1:
                raise TimeoutError('stalled')
            self.finish_stream()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.assertEqual(self.stream.client.highest_quality.call_count, 2)
        self.assertEqual(self.stream.stream_once.call_count, 2)

    def test_fast_restart_rechecks_quality_even_if_device_is_already_online(self):
        def cycle():
            if self.stream.stream_once.call_count == 1:
                raise TimeoutError('stalled')
            self.finish_stream()
        self.stream.stream_once = Mock(side_effect=cycle)
        self.stream.run()
        self.assertEqual(self.stream.client.highest_quality.call_count, 2)

    def test_blocked_decoder_read_times_out_kills_pipeline_and_invalidates_frame(self):
        released = threading.Event()
        class BlockedPipe:
            def read(self, size):
                released.wait(4)
                return b''
            def close(self):
                pass
        adb = Mock(stdout=io.BytesIO(), poll=Mock(return_value=None))
        decoder = Mock(stdout=BlockedPipe(), poll=Mock(return_value=None))
        decoder.terminate.side_effect = released.set
        self.stream.client.screenshot.return_value = np.zeros((2, 2, 3), np.uint8)
        self.stream.publish(np.zeros((2, 2, 3), np.uint8), 0)
        with patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='mock-ffmpeg'), \
             patch.object(adb_capture.subprocess, 'Popen', side_effect=[adb, decoder]), \
             patch.object(adb_capture.time, 'monotonic', side_effect=[0, 0, 16]):
            with self.assertRaisesRegex(TimeoutError, 'stalled'):
                self.stream.stream_once()
        decoder.terminate.assert_called_once()
        adb.terminate.assert_called_once()
        self.assertEqual(bytes(self.stream.memory.buf[:adb_capture.HEADER.size]), bytes(adb_capture.HEADER.size))
        self.assertFalse(any(t.name == 'SuperLiveADBReader' for t in threading.enumerate()))


if __name__ == '__main__':
    unittest.main()
