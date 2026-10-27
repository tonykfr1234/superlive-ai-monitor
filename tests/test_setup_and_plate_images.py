"""Synthetic setup, reconnection and exact-frame plate evidence checks."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from record_protocol import atomic_json, read_json
from setup_wizard import parse_plates, parse_aliases, save_settings
from plate_corrections import prefer_verified, correct_event, save_plate_crop
from plate_recognition import LiveClipConsensus, PlateLearningMemory
from latest_vehicle import LatestVehicleTracker
from adb_capture import AdbClient
import adb_discovery


class SetupAndPlateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_invalid_settings_do_not_change_credentials_or_capture(self):
        atomic_json(self.root/'config/capture_config.json', {'original': True})
        with self.assertRaises(ValueError):
            save_settings(self.root, {}, 'invalid', '', '', '', '')
        self.assertEqual(read_json(self.root/'config/capture_config.json'), {'original': True})
        self.assertFalse((self.root/'config/telegram.env').exists())
        for value in ('AB1234=CD5678\nCD5678=EF9012', 'AB1234=CD5678\nAB1234=EF9012'):
            with self.assertRaises(ValueError):
                parse_aliases(value)

    def test_settings_are_local_normalized_and_optional_telegram_is_empty(self):
        self.assertEqual(parse_plates('ABC-1234，CD5678\nABC1234'), ['ABC1234', 'CD5678'])
        save_settings(self.root, {'source': 'adb'}, '', '', 'ABC-1234', 'ABC-1234', 'ABC123A=ABC-1234')
        self.assertEqual(read_json(self.root/'config/plate_corrections.json')['aliases'], {'ABC123A': 'ABC1234'})
        self.assertEqual((self.root/'config/telegram.env').read_text(), 'BOT_TOKEN=\nCHAT_ID=\n')
        self.assertFalse((self.root/'config/telegram.env.tmp').exists())

    def test_verified_preference_never_invents_a_reading_or_chooses_a_weak_candidate(self):
        best = dict(raw_text='ABC123A', confidence=.95)
        weak = dict(raw_text='ABC1234', confidence=.7)
        config = {'verified': ['ABC-1234']}
        self.assertEqual(prefer_verified([best], best, config), (best, False))
        self.assertEqual(prefer_verified([best, weak], best, config), (best, False))
        good = dict(raw_text='ABC1234', confidence=.93)
        self.assertEqual(prefer_verified([best, good], best, config), (good, True))

    def test_only_explicit_exact_alias_corrects_and_preserves_raw_evidence(self):
        evidence = [{'text': 'ABC123A'}]
        event = correct_event({'text': 'ABC123A', 'evidence': evidence}, {'aliases': {'ABC123A': 'ABC-1234'}})
        self.assertEqual(event['text'], 'ABC1234')
        self.assertEqual(event['observed_text'], 'ABC123A')
        self.assertEqual(event['display_text'], 'ABC-1234')
        self.assertEqual(event['evidence'], evidence)
        neighbor = correct_event({'text': 'ABC123B'}, {'aliases': {'ABC123A': 'ABC1234'}})
        self.assertEqual(neighbor['text'], 'ABC123B')

    def event(self, text='ABC1234'):
        return dict(id='fixture', confirmed=True, text=text, timestamp=102,
                    source='live_recording', evidence=[
                        dict(text=text, timestamp=100+i, sample_id=str(i), bbox=[25, 35, 65, 48])
                        for i in range(3)])

    def test_plate_crop_uses_exact_sample_and_associated_car_then_restores_hash(self):
        frame = np.zeros((100, 160, 3), np.uint8)
        frame[35:48, 25:65] = (220, 240, 240)
        event = self.event()
        save_plate_crop(self.root, event, frame, '2')
        tracker = LatestVehicleTracker(self.root, now=100)
        for stamp in (100, 101, 102):
            tracker.observe([dict(class_id=2, box=[10, 10, 100, 80])], stamp, (160, 100), frame=frame)
        tracker.update_plates([event])
        self.assertIsNotNone(tracker.plate_image)
        self.assertGreater(tracker.plate_image.mean(), 100)
        restored = LatestVehicleTracker(self.root, now=103)
        self.assertIsNotNone(restored.plate_image)
        tracker.plate_image_path.write_bytes(b'wrong photo')
        self.assertIsNone(LatestVehicleTracker(self.root, now=103).plate_image)
        tracker.observe([dict(class_id=2, box=[105, 10, 150, 80])], 104, (160, 100), frame=frame)
        self.assertIsNone(tracker.plate_image)

    def test_missing_or_wrong_sample_cannot_create_crop(self):
        event = self.event()
        save_plate_crop(self.root, event, np.zeros((100, 160, 3), np.uint8), 'unrelated')
        self.assertNotIn('plate_image', event)
        event['evidence'][-1]['text'] = 'CD5678'
        save_plate_crop(self.root, event, np.zeros((100, 160, 3), np.uint8), '2')
        self.assertNotIn('plate_image', event)

    def test_corrected_event_still_associates_using_raw_samples(self):
        event = self.event('ABC123A')
        correct_event(event, {'aliases': {'ABC123A': 'ABC1234'}})
        frame = np.full((100, 160, 3), 180, np.uint8)
        save_plate_crop(self.root, event, frame, '2')
        tracker = LatestVehicleTracker(self.root, now=100)
        for stamp in (100, 101, 102):
            tracker.observe([dict(class_id=2, box=[10, 10, 100, 80])], stamp, (160, 100))
        tracker.update_plates([event])
        self.assertEqual(tracker.snapshot()['plate'], 'ABC1234')
        self.assertTrue(tracker.snapshot()['user_corrected'])
        self.assertIsNotNone(tracker.plate_image)

    def test_aliases_share_the_canonical_cooldown_without_training_on_corrections(self):
        atomic_json(self.root/'config/plate_corrections.json', {'aliases': {'ABC123A': 'ABC1234'}})
        consensus = LiveClipConsensus(self.root, continuous=True)
        consensus.set_context(dict(request_id='fixture', started_at=100))
        def feed(text, start):
            result = None
            for i in range(3):
                variant = dict(raw_text=text, confidence=.95)
                result = consensus.observe(dict(selected=variant, bbox=[25, 35, 65, 48],
                    detector_confidence=.95), start+i, str(start+i), []) or result
            return result
        # Use a format-valid erroneous plate as the alias source.
        atomic_json(self.root/'config/plate_corrections.json', {'aliases': {'ABC1235': 'ABC1234'}})
        first = feed('ABC1235', 100)
        self.assertEqual(first['text'], 'ABC1234')
        self.assertFalse(PlateLearningMemory(self.root).entries(103))
        self.assertIsNone(feed('ABC1234', 105))
        self.assertIsNotNone(feed('ABC1234', 285))

    @patch('adb_discovery.discover')
    def test_runtime_retries_saved_device_without_switching(self, discover):
        client = AdbClient(dict(auto_detect=True, serial='127.0.0.1:21503'))
        discover.return_value = ('fixture-adb', [])
        with self.assertRaises(RuntimeError):
            client.select()
        discover.return_value = ('fixture-adb', ['127.0.0.1:21503'])
        client.select()
        self.assertEqual(client.serial, '127.0.0.1:21503')
        discover.return_value = ('fixture-adb', ['127.0.0.1:21503', '127.0.0.1:16384'])
        with self.assertRaises(RuntimeError):
            AdbClient(dict(auto_detect=True)).select()

    @patch('adb_discovery.emulator_processes', return_value=[])
    @patch('adb_discovery.candidate_paths', return_value=['fixture-adb'])
    @patch('adb_discovery.run')
    def test_discovery_connects_only_loopback_and_filters_usb(self, run, paths, processes):
        run.return_value = 'List of devices attached\nphysical-usb\tdevice\n127.0.0.1:21503\tdevice\n'
        executable, devices = adb_discovery.discover()
        self.assertEqual(devices, ['127.0.0.1:21503'])
        connects = [call.args for call in run.call_args_list if call.args[1] == 'connect']
        self.assertTrue(connects)
        self.assertTrue(all(c[2].startswith('127.0.0.1:') for c in connects))


if __name__ == '__main__':
    unittest.main()
