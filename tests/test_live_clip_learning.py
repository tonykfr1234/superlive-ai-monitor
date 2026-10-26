"""Synthetic clips only; no model downloads, camera files or network calls."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plate_recognition import (LiveClipConsensus, PlateLearningMemory, complete_plate,
                               recording_plate_context)
from plate_replay import main as replay_main
from plate_history import load_history, record_clip_result
from record_protocol import atomic_json, read_json
from telegram_video import TelegramVideoQueue
import plate_recognition as plates


def prediction(text='AB1234', confidence=.95, box=(10, 10, 80, 30), variants=None):
    selected = dict(raw_text=text, confidence=confidence, variant='original')
    return dict(selected=selected, variants=variants or [selected], bbox=box, detector_confidence=.95)


class LiveClipLearningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = LiveClipConsensus(self.root)
        self.context = dict(request_id='synthetic-1', started_at=100, file=str(self.root/'clip.mp4'))
        self.live.set_context(self.context)

    def votes(self, live=None, text='AB1234', start=101, confidence=.95, variants=None):
        live = live or self.live
        event = None
        for i in range(3):
            event = live.observe(prediction(text, confidence, variants=variants), start+i,
                                 'sample-%s-%d' % (start, i), ['AB1234'])
        return event

    def test_one_complete_result_per_clip_and_no_second_after_restart(self):
        event = self.votes()
        self.assertEqual(event['text'], 'AB1234')
        self.assertTrue(event['complete_plate'])
        self.assertEqual(len(list((self.root/'runtime/plate_events').glob('*.json'))), 1)
        self.assertIsNone(self.votes(text='CD5678', start=105))
        resumed = LiveClipConsensus(self.root)
        resumed.set_context(self.context)
        self.assertIsNone(self.votes(resumed, text='CD5678', start=110))
        self.assertEqual(resumed.winner()['text'], 'AB1234')

    def test_next_clip_gets_its_own_plate_and_independent_learning_vote(self):
        first = self.votes()
        self.live.set_context(dict(self.context, request_id='synthetic-2', started_at=200))
        second = self.votes(start=201)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.live.memory.entries(204)), 2)

    def test_no_context_or_before_clip_never_emits(self):
        self.assertIsNone(self.votes(start=90))
        self.live.set_context(None)
        self.assertIsNone(self.votes(start=105))

    def test_incomplete_low_confidence_and_allowlist_guess_do_not_confirm(self):
        for text, confidence in [('AB123', .99), ('A1234', .99), ('AB1234', .84), ('123456', .99)]:
            self.assertIsNone(self.votes(text=text, confidence=confidence))
        self.assertIsNone(self.live.winner())

    def test_different_fuzzy_readings_cannot_pool_votes(self):
        for i, text in enumerate(['AB1234', 'AB1235', 'AB1236']):
            self.assertIsNone(self.live.observe(prediction(text), 101+i, str(i), ['AB1234']))

    def test_one_blurred_character_is_not_hidden_by_high_average(self):
        variant = dict(raw_text='AB1234', confidence=.93, min_character_confidence=.40)
        self.assertIsNone(self.votes(variants=[variant]))

    def test_memory_does_not_override_strong_new_reading_or_ambiguous_prior(self):
        memory = self.live.memory
        entries = {str(i): dict(text='AB1234', timestamp=100+i, aliases=['AB1235']) for i in range(3)}
        atomic_json(memory.path, dict(clips=entries))
        best, changed = memory.choose([dict(raw_text='AB1235', confidence=.99),
                                       dict(raw_text='AB1234', confidence=.90)], 110)
        self.assertEqual(best['raw_text'], 'AB1235')
        self.assertFalse(changed)
        entries.update({str(i+3): dict(text='AB1235', timestamp=103+i, aliases=['AB1234']) for i in range(3)})
        atomic_json(memory.path, dict(clips=entries))
        best, changed = memory.choose([dict(raw_text='AB1235', confidence=.96),
                                       dict(raw_text='AB1234', confidence=.93)], 110)
        self.assertEqual(best['raw_text'], 'AB1235')
        self.assertFalse(changed)

    def test_separated_plate_boxes_cannot_pool_votes(self):
        for i, box in enumerate([(10, 10, 80, 30), (110, 10, 180, 30), (10, 10, 80, 30)]):
            self.assertIsNone(self.live.observe(prediction(box=box), 101+i, str(i), []))

    def test_duplicate_sample_and_pause_do_not_accumulate_votes(self):
        for i in range(4):
            self.assertIsNone(self.live.observe(prediction(), 101+i, 'same', []))
        self.live.reset()
        self.assertIsNone(self.live.observe(prediction(), 106, 'new', []))

    def test_locked_winner_never_authorizes_a_second_result(self):
        self.votes()
        with patch('plate_recognition.read_json', return_value=None):
            self.assertTrue(self.live.winner()['unavailable'])
            self.assertIsNone(self.votes(text='CD5678', start=110))

    def test_result_is_attached_to_its_video_without_reading_video(self):
        event = self.votes()
        record_clip_result(self.root, self.root/'clip.mp4', 100, 120, now=220,
                           request_id='synthetic-1')
        history = load_history(self.root, now=220)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['id'], event['id'])
        self.assertEqual(history[0]['source_video'], 'clip.mp4')
        self.assertFalse((self.root/'clip.mp4').exists())

    def test_memory_only_ranks_current_observed_variants_after_three_clips(self):
        variants = [dict(raw_text='AB1234', confidence=.96), dict(raw_text='AB1235', confidence=.92)]
        for clip in range(3):
            self.live.set_context(dict(self.context, request_id='clip-'+str(clip), started_at=100+clip*20))
            self.votes(start=101+clip*20, variants=variants)
        current = [dict(raw_text='AB1235', confidence=.96), dict(raw_text='AB1234', confidence=.93)]
        chosen, influenced = self.live.memory.choose(current, 160)
        self.assertEqual(chosen['raw_text'], 'AB1234')
        self.assertTrue(influenced)
        chosen, influenced = self.live.memory.choose([current[0]], 160)
        self.assertEqual(chosen['raw_text'], 'AB1235')
        self.assertFalse(influenced)  # Memory never invents a missing candidate.

    def test_repeating_one_clip_does_not_train_three_times(self):
        event = self.votes(variants=[dict(raw_text='AB1234', confidence=.96), dict(raw_text='AB1235', confidence=.92)])
        for _ in range(10):
            self.live.memory.learn(event, 104)
        self.assertEqual(len(self.live.memory.entries(104)), 1)
        chosen, influenced = self.live.memory.choose([
            dict(raw_text='AB1235', confidence=.96), dict(raw_text='AB1234', confidence=.93)], 104)
        self.assertFalse(influenced)
        self.assertEqual(chosen['raw_text'], 'AB1235')

    def test_influenced_fuzzy_replay_and_low_quality_results_do_not_train(self):
        event = self.votes(confidence=.87)
        self.assertEqual(self.live.memory.entries(104), {})
        for changed in [dict(event, learning_influenced=True), dict(event, source='recording_replay'),
                        dict(event, complete_plate=False)]:
            self.live.memory.learn(changed, 104)
        self.assertEqual(self.live.memory.entries(104), {})

    def test_memory_expires_after_seven_days_and_no_stale_prior_is_used(self):
        self.votes()
        self.live.memory.prune(104+7*86400)
        self.assertEqual(read_json(self.live.memory.path)['clips'], {})

    def test_learning_write_failure_does_not_erase_confirmed_result(self):
        from plate_recognition import atomic_json as original
        def write(path, value):
            if Path(path).name == 'memory.json':
                raise PermissionError('synthetic lock')
            original(path, value)
        with patch('plate_recognition.atomic_json', side_effect=write):
            self.assertIsNotNone(self.votes())
        self.assertTrue(self.live.memory.error)
        self.assertEqual(self.live.winner()['text'], 'AB1234')

    def test_new_caption_uses_only_this_clips_complete_winner(self):
        event = self.votes()
        wrong = dict(event, id='unrelated', request_id='other', text='CD5678', timestamp=104)
        atomic_json(self.root/'runtime/plate_events/unrelated.json', wrong)
        q = TelegramVideoQueue(self.root)
        path = q.enqueue(self.root/'clip.mp4', dict(request_id='synthetic-1', created_at=100,
            clip_started_at=100, object_counts=dict(car=1), single_plate_per_clip=True), 120, True)
        caption = q.caption_content(read_json(path))
        self.assertEqual([p['id'] for p in caption['plates']], [event['id']])
        self.assertNotIn('CD5678', caption['text'])

    def test_recording_context_rejects_idle_stale_or_previous_clip_sample(self):
        path = self.root/'runtime/recording_state.json'
        state = dict(status='RECORDING', request_id='clip', clip_started_at=100, updated_at=103)
        atomic_json(path, state)
        self.assertIsNotNone(recording_plate_context(self.root, 102, 104))
        self.assertIsNone(recording_plate_context(self.root, 99, 104))
        self.assertIsNone(recording_plate_context(self.root, 102, 114))
        atomic_json(path, dict(state, status='IDLE'))
        self.assertIsNone(recording_plate_context(self.root, 102, 104))

    def test_historical_cli_does_not_discover_files_or_load_model_by_default(self):
        with patch('plate_replay.discover_videos') as discover, patch('plate_replay.run_batch') as batch:
            self.assertEqual(replay_main(['--root', str(self.root), '--record-root', 'unused']), 0)
        discover.assert_not_called()
        batch.assert_not_called()

    def test_complete_format_does_not_claim_every_sequence_is_a_full_plate(self):
        for text in ['AB1234', '1234AB', 'ABC1234']:
            self.assertTrue(complete_plate(text))
        for text in ['AB123', 'ABC', '1234567', 'AB1234XYZ']:
            self.assertFalse(complete_plate(text))

    def run_live_worker(self, idle=False, roll_over=False):
        clock, polls, reads = [101.0], [0], [0]
        original_read = plates.read_json
        def read(path, default=None):
            name = Path(path).name
            if name == 'control.json':
                polls[0] += 1
                return dict(session='test', generation=1, heartbeat=clock[0], active=True,
                            stop=polls[0] > 22)
            if name == 'recording_state.json':
                return dict(status='IDLE' if idle else 'RECORDING', request_id='next' if roll_over and reads[0] else 'first',
                            clip_started_at=100, updated_at=clock[0], file=str(self.root/'live.mp4'))
            return original_read(path, default)
        def sample(*args):
            clock[0] += 1
            reads[0] += 1
            return np.zeros((50, 100, 3), np.uint8), dict(session='test', generation=1,
                sample_id='frame-'+str(reads[0]), timestamp=clock[0], roi=None)
        detector = Mock(return_value=[prediction()])
        ort = SimpleNamespace(SessionOptions=lambda: SimpleNamespace())
        alpr = SimpleNamespace(ALPR=Mock(return_value=object()))
        with patch.dict(sys.modules, {'onnxruntime': ort, 'fast_alpr': alpr}), \
             patch.object(plates, 'read_json', side_effect=read), \
             patch.object(plates, 'read_plate_sample', side_effect=sample) as reader, \
             patch.object(plates, 'detect_plate_observations', detector), \
             patch.object(plates.time, 'time', side_effect=lambda: clock[0]), \
             patch.object(plates.time, 'sleep'):
            self.assertEqual(plates.run_worker(self.root, 'test'), 0)
        return detector, reader

    def test_worker_does_not_read_frames_or_infer_while_recorder_idle(self):
        detector, reader = self.run_live_worker(idle=True)
        detector.assert_not_called()
        reader.assert_not_called()

    def test_worker_stops_inference_after_first_complete_plate(self):
        detector, reader = self.run_live_worker()
        self.assertEqual(detector.call_count, 3)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(len(list((self.root/'runtime/plate_events').glob('*.json'))), 1)

    def test_worker_discards_sample_when_recording_changes_before_inference(self):
        self.run_live_worker(roll_over=True)
        events = [read_json(path) for path in (self.root/'runtime/plate_events').glob('*.json')]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['request_id'], 'next')
        self.assertGreaterEqual(min(e['timestamp'] for e in events[0]['evidence']), 103)


if __name__ == '__main__':
    unittest.main()
