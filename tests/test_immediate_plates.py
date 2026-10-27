"""Live multi-plate notifications, synthetic OCR and mocked Telegram only."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plate_recognition import LiveClipConsensus
from plate_history import load_history, record_clip_result
from record_protocol import atomic_json, read_json
from telegram_video import TelegramVideoQueue
from test_live_clip_learning import prediction


class ContinuousRecognitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = self.tracker()

    def tracker(self, request='clip'):
        live = LiveClipConsensus(self.root, continuous=True)
        live.set_context(dict(request_id=request, started_at=100))
        return live

    def votes(self, text, start, live=None):
        live = live or self.live
        events = [live.observe(prediction(text), start+i, str(start+i), []) for i in range(3)]
        return next((event for event in events if event), None)

    def test_two_cars_in_one_clip_and_cooldown_survives_restart_and_rollover(self):
        first = self.votes('AB1234', 101)
        second = self.votes('CD5678', 104)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['notification_mode'], 'immediate_plate')
        self.assertIsNone(self.votes('AB1234', 110))
        resumed = self.tracker('next-clip')
        self.assertIsNone(self.votes('AB1234', 150, resumed))
        self.assertIsNotNone(self.votes('AB1234', 283, resumed))

    def test_same_frame_can_confirm_two_plates_but_not_duplicate_votes(self):
        events = []
        for i in range(3):
            for text in ('AB1234', 'CD5678'):
                event = self.live.observe(prediction(text), 101+i, str(i), [])
                if event:
                    events.append(event)
        self.assertEqual({e['text'] for e in events}, {'AB1234', 'CD5678'})

    def test_all_live_clip_results_link_to_video_without_reopening_it(self):
        self.votes('AB1234', 101)
        self.votes('CD5678', 104)
        record_clip_result(self.root, self.root/'video.mp4', 100, 120, now=220, request_id='clip')
        history = load_history(self.root, now=220)
        self.assertEqual(len(history), 2)
        self.assertTrue(all(e['source_video'] == 'video.mp4' for e in history))
        self.assertFalse((self.root/'video.mp4').exists())

    def test_preview_results_do_not_create_fake_independent_training_clips(self):
        self.live.set_context(dict(request_id='preview', started_at=0, source='live'))
        self.votes('AB1234', 101)
        self.assertEqual(self.live.memory.entries(110), {})


class ImmediateDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.q = self.queue()
        p = patch('telegram_video.load_credentials', return_value=('fake', 'fake'))
        p.start()
        self.addCleanup(p.stop)

    def queue(self):
        queue = TelegramVideoQueue(self.root, Mock(), immediate_plates=True)
        queue.send_plate = Mock()
        return queue

    def event(self, identity, text='AB1234', stamp=1000, **extra):
        e = dict(id=identity, text=text, timestamp=stamp, confirmed=True,
                 complete_plate=True, notification_mode='immediate_plate', source='live_recording',
                 request_id='clip')
        e.update(extra)
        atomic_json(self.q.plate_directory/(identity+'.json'), e)
        return e

    def test_immediate_during_quiet_pending_recording_and_old_video_backlog(self):
        atomic_json(self.root/'runtime/telegram_quiet.json', dict(request_id='clip', quiet_until=1200))
        atomic_json(self.root/'runtime/record_request.json', dict(request_id='clip'))
        atomic_json(self.root/'runtime/recording_state.json', dict(status='RECORDING', request_id='clip', elapsed_seconds=10))
        self.q.enqueue(self.root/'old.mp4', dict(created_at=800, clip_started_at=800), 120, True)
        self.event('a')
        self.q.process_immediate_plates(1000)
        self.q.send_plate.assert_called_once()
        self.assertFalse(self.q.order_file.exists())

    def test_normalized_per_plate_180_seconds_persists_across_restart(self):
        self.event('a', 'AB-1234')
        self.q.process_immediate_plates(1000)
        self.event('b', 'ab 1234', 1001)
        self.event('c', 'CD5678', 1002)
        resumed = self.queue()
        resumed.process_immediate_plates(1002)
        self.assertEqual([c.args[0]['id'] for c in resumed.send_plate.call_args_list], ['c'])
        self.event('d', stamp=1179)
        resumed.process_immediate_plates(1179)
        self.assertEqual(resumed.send_plate.call_count, 1)
        self.event('e', stamp=1180)
        resumed.process_immediate_plates(1180)
        self.assertEqual(resumed.send_plate.call_count, 2)

    def test_different_observed_plates_are_not_merged_by_fuzzy_target(self):
        self.event('a', 'AB1234', matched='AB1234')
        self.event('b', 'AB1235', 1001, matched='AB1234')
        self.q.process_immediate_plates(1002)
        self.assertEqual(self.q.send_plate.call_count, 2)

    def test_failure_retries_but_other_car_can_notify(self):
        self.event('a')
        self.event('b', 'CD5678', 1001)
        self.q.send_plate.side_effect = [RuntimeError('synthetic'), None, None]
        self.q.process_immediate_plates(1002)
        self.assertEqual(self.q.send_plate.call_count, 2)
        self.q.process_immediate_plates(1003)
        self.assertEqual(self.q.send_plate.call_count, 2)
        self.q.process_immediate_plates(1022)
        self.assertEqual(self.q.send_plate.call_count, 3)

    def test_old_and_historical_events_do_not_flood_immediate_queue(self):
        self.event('old', notification_mode=None)
        self.event('replay', source='recording_replay')
        self.q.process_immediate_plates(1000)
        self.q.send_plate.assert_not_called()

    def test_successful_send_receipt_failure_does_not_resend_in_same_process(self):
        self.event('a')
        original = atomic_json
        def write(path, value):
            if value.get('sent_at'):
                raise PermissionError('synthetic')
            original(path, value)
        with patch('telegram_video.atomic_json', side_effect=write):
            self.q.process_immediate_plates(1000)
        self.q.process_immediate_plates(1020)
        self.q.send_plate.assert_called_once()
        self.assertEqual(read_json(self.q.plate_receipt('a'))['sent_at'], 1000)

    def test_plate_send_does_not_block_older_videos_or_ack_unsent_plate(self):
        self.event('a', stamp=1100)
        self.q.process_immediate_plates(1100)
        self.event('b', 'CD5678', 1120)
        paths = []
        for stamp in (1000, 800):
            video = self.root/('%s.mp4' % stamp)
            video.write_bytes(b'synthetic')
            paths.append(self.q.enqueue(video, dict(request_id='clip', created_at=stamp,
                clip_started_at=stamp, continuous_plate_notifications=True), 300, True))
        sent = []
        self.q.send = Mock(side_effect=lambda video, job, *_: sent.append(job['trigger_created_at']) or
            dict(notified_through=1200, notified_plates=[dict(id='b', timestamp=1120)]))
        self.q.process_once(1500)
        self.assertEqual(sent, [800, 1000])
        self.assertFalse(self.q.plate_receipt('b').exists())
        self.assertEqual(self.q.last_delivered_time(), 1000)

    def test_caption_contains_all_plates_from_exact_clip_only(self):
        self.event('a', stamp=1020)
        self.event('b', 'CD5678', 1030)
        self.event('other', 'EF9012', 1040, request_id='other')
        job = dict(request_id='clip', trigger_created_at=1000, clip_started_at=1000,
                   duration=120, complete=True, continuous_plate_notifications=True)
        text = self.q.caption(job)
        self.assertIn('AB1234', text)
        self.assertIn('CD5678', text)
        self.assertNotIn('EF9012', text)

    def test_stop_blocks_immediate_dispatch(self):
        self.event('a')
        (self.root/'runtime/stop.flag').touch()
        self.q.process_immediate_plates(1000)
        self.q.send_plate.assert_not_called()

    def test_real_send_guard_bypasses_quiet_only_in_immediate_mode(self):
        queue = TelegramVideoQueue(self.root, immediate_plates=True)
        atomic_json(self.root/'runtime/telegram_quiet.json', dict(request_id='clip', quiet_until=9999999999))
        with patch('requests.post', return_value=Mock(status_code=200, json=lambda: dict(ok=True))) as http:
            queue.send_plate(self.event('a'), 'fake', 'fake')
        http.assert_called_once()


if __name__ == '__main__':
    unittest.main()
