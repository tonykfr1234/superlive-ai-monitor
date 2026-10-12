"""Global Telegram quiet periods with synthetic files and mocked HTTP only."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from record_protocol import atomic_json, read_json
import telegram_video


class TelegramQuietTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = Mock()
        self.queue = telegram_video.TelegramVideoQueue(self.root, self.log)
        self.video = self.root / 'synthetic.mp4'
        self.video.write_bytes(b'synthetic video, never sent')
        self.manifest = self.queue.enqueue(self.video, {'request_id': 'older-event', 'count': 1}, 180, True)
        self.queue.prepare = Mock(return_value=self.video)
        self.credentials = patch.object(telegram_video, 'load_credentials', return_value=('fake-token', 'fake-chat'))
        self.credentials.start()
        self.addCleanup(self.credentials.stop)
        self.clock = patch.object(telegram_video.time, 'time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.http_patch = patch('requests.post', return_value=Mock(status_code=200, json=lambda: {'ok': True}))
        self.http = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def quiet_until(self, deadline):
        atomic_json(self.root / 'runtime' / 'telegram_quiet.json',
                    {'request_id': 'current-event', 'quiet_until': deadline})

    def test_global_quiet_defers_an_older_job_before_preparation(self):
        self.quiet_until(1120)
        original = self.manifest.read_bytes()
        self.queue.process_once(now=1000)
        self.queue.prepare.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(self.manifest.read_bytes(), original)
        self.log.assert_not_called()

    def test_retry_count_and_deadline_do_not_change_while_quiet(self):
        job = read_json(self.manifest)
        job.update(attempts=4, retry_at=990)
        atomic_json(self.manifest, job)
        self.quiet_until(1120)
        original = self.manifest.read_bytes()
        self.queue.process_once(now=1000)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.http.assert_not_called()

    def test_expired_quiet_guard_resumes_existing_queue_job(self):
        self.quiet_until(1000)
        self.queue.process_once(now=1000)
        self.http.assert_called_once()
        self.assertFalse(self.manifest.exists())
        self.assertTrue(self.video.exists())

    def test_new_quiet_period_during_preparation_defers_final_post(self):
        original = self.manifest.read_bytes()

        def prepare_then_new_clip(job):
            self.quiet_until(1120)
            return self.video

        self.queue.prepare.side_effect = prepare_then_new_clip
        self.queue.process_once(now=1000)
        self.queue.prepare.assert_called_once()
        self.http.assert_not_called()
        self.assertEqual(self.manifest.read_bytes(), original)
        self.log.assert_not_called()

    def test_direct_send_has_its_own_last_moment_guard(self):
        self.quiet_until(1120)
        with self.assertRaises(telegram_video.TelegramDeferred):
            self.queue.send(self.video, read_json(self.manifest), 'fake-token', 'fake-chat')
        self.http.assert_not_called()

    def test_direct_send_cannot_dispatch_after_shutdown(self):
        self.queue.stop_event.set()
        with self.assertRaises(telegram_video.TelegramDeferred):
            self.queue.send(self.video, read_json(self.manifest), 'fake-token', 'fake-chat')
        self.http.assert_not_called()

    def test_pending_record_request_blocks_previous_clip_upload(self):
        atomic_json(self.root / 'runtime' / 'record_request.json',
                    {'request_id': 'new-event', 'created_at': 1000})
        self.queue.process_once(now=1000)
        self.queue.prepare.assert_not_called()
        self.http.assert_not_called()
        self.assertTrue(self.manifest.exists())

    def test_starting_and_first_120_seconds_block_without_separate_guard(self):
        for state in ({'status': 'STARTING', 'request_id': 'new-event'},
                      {'status': 'RECORDING', 'request_id': 'new-event', 'elapsed_seconds': 119.999}):
            with self.subTest(status=state['status']):
                atomic_json(self.root / 'runtime' / 'recording_state.json', state)
                self.queue.process_once(now=1000)
                self.queue.prepare.assert_not_called()
                self.http.assert_not_called()
                self.assertTrue(self.manifest.exists())

    def test_restarted_queue_still_honors_persisted_quiet_guard(self):
        self.quiet_until(1120)
        restarted = telegram_video.TelegramVideoQueue(self.root, self.log)
        restarted.prepare = Mock(return_value=self.video)
        restarted.process_once(now=1000)
        restarted.prepare.assert_not_called()
        self.http.assert_not_called()
        self.assertTrue(self.manifest.exists())

    def test_quiet_begins_between_jobs_and_remaining_backlog_waits(self):
        second = self.queue.enqueue(self.video, {'request_id': 'second-old-event'}, 180, True)
        first_job, second_job = read_json(self.manifest), read_json(second)
        first_job['created_at'], second_job['created_at'] = 10, 20
        atomic_json(self.manifest, first_job)
        atomic_json(second, second_job)

        def complete_first_request(*args, **kwargs):
            self.quiet_until(1120)
            return Mock(status_code=200, json=lambda: {'ok': True})

        self.http.side_effect = complete_first_request
        self.queue.process_once(now=1000)
        self.http.assert_called_once()
        self.assertFalse(self.manifest.exists())
        self.assertTrue(second.exists())
        self.assertEqual(read_json(second)['attempts'], 0)


if __name__ == '__main__':
    unittest.main()
