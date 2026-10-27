"""Private vehicle registry, manual review and conservative fuzzy matching."""
from pathlib import Path
from contextlib import closing
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from vehicle_registry import VehicleRegistry, DatabaseConsensus, match_registry, vehicle_color, color_for_plate
from plate_review import prepare_review, save_confirmation
from plate_recognition import LiveClipConsensus, publish_candidate
from latest_vehicle import LatestVehicleTracker
from record_protocol import read_json
from preview_ui import PreviewController, PreviewLayout
from telegram_video import event_line
from plate_history import prune_history


class VehicleRegistryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.registry = VehicleRegistry(self.root)

    def test_confirm_persists_preferences_audit_and_disable_is_reversible(self):
        self.registry.confirm('ABC-1234', 'blue', 'ABX1234', {'snapshot_id': 'test'}, 2, True)
        rows, settings = VehicleRegistry(self.root).read()
        self.assertEqual(rows[0]['plate'], 'ABC1234')
        self.assertEqual(settings['tolerance'], 2)
        self.registry.disable('ABC1234')
        self.assertFalse(self.registry.read()[0][0]['enabled'])
        self.registry.confirm('ABC1234', 'blue')
        self.assertTrue(self.registry.read()[0][0]['enabled'])
        import sqlite3
        with closing(sqlite3.connect(self.registry.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM confirmations').fetchone()[0], 3)

    def test_invalid_plate_never_creates_database(self):
        for text in ('A', 'ABCDEFG', '1234567', "ABC'; DROP TABLE vehicles"):
            with self.assertRaises(ValueError):
                self.registry.confirm(text)
        self.assertFalse(self.registry.path.exists())

    def match(self, raw, color=None):
        rows, settings = self.registry.read()
        return match_registry(raw, color or {'name': 'unknown', 'confidence': 0}, rows, settings)

    def test_one_two_three_differences_and_missing_chars_use_unique_registry(self):
        self.registry.confirm('ABC1234')
        for raw, expected in [('ABX1234', 1), ('ABX1284', 2), ('ZBX1284', 3), ('AB1234', 1), ('A1234', 2), ('1234', 3)]:
            with self.subTest(raw=raw):
                self.assertEqual(self.match(raw)['plate'], 'ABC1234')
                self.assertEqual(self.match(raw)['distance'], expected)
        self.assertIsNone(self.match('ZXY9284'))
        self.assertIsNone(self.match('234'))

    def test_tied_candidates_need_reliable_matching_color(self):
        self.registry.confirm('ABC1234', 'blue')
        self.registry.confirm('ABC1235', 'red')
        self.assertIsNone(self.match('ABC1236'))
        self.assertEqual(self.match('ABC1236', dict(name='red', confidence=.9))['plate'], 'ABC1235')
        self.assertIsNone(self.match('ABC1236', dict(name='red', confidence=.2)))
        self.assertIsNone(self.match('ABC1236', dict(name='green', confidence=.9)))
        self.assertEqual(self.match('ABC1234', dict(name='red', confidence=.9))['plate'], 'ABC1234')

    def prediction(self, raw='ABX1234', box=None, color='unknown'):
        return dict(selected=dict(raw_text=raw, confidence=.9), detector_confidence=.95,
                    bbox=box or [25, 35, 65, 48], vehicle_color=dict(name=color, confidence=.9))

    def test_consensus_independent_frames_spatial_consistency_and_raw_evidence(self):
        self.registry.confirm('ABC1234')
        c = DatabaseConsensus(self.root)
        self.assertIsNone(c.observe(self.prediction(), 100, 'a'))
        self.assertIsNone(c.observe(self.prediction(), 100.5, 'a'))
        self.assertIsNone(c.observe(self.prediction('ABC1284'), 101, 'b'))
        event = c.observe(self.prediction('ZBC1234'), 102, 'c')
        self.assertEqual(event['text'], 'ABC1234')
        self.assertEqual({e['text'] for e in event['evidence']}, {'ABX1234', 'ABC1284', 'ZBC1234'})
        self.assertIn('資料庫匹配', event_line(event))
        c.reset()
        c.observe(self.prediction(), 103, 'd')
        c.observe(self.prediction(), 104, 'e')
        self.assertIsNone(c.observe(self.prediction(box=[110, 35, 150, 48]), 105, 'f'))

    def test_registry_change_clears_pending_votes(self):
        self.registry.confirm('ABC1234')
        c = DatabaseConsensus(self.root)
        c.observe(self.prediction(), 100, 'a')
        c.observe(self.prediction(), 101, 'b')
        self.registry.disable('ABC1234')
        c.last_reload = -float('inf')
        self.assertIsNone(c.observe(self.prediction(), 102, 'c'))
        self.assertFalse(c.queues)

    def test_low_light_and_monochrome_are_unknown_and_wrong_vehicle_is_not_used(self):
        self.assertEqual(vehicle_color(np.full((100, 160, 3), 100, np.uint8), [10, 10, 100, 80])['name'], 'unknown')
        self.assertEqual(vehicle_color(np.zeros((100, 160, 3), np.uint8), [10, 10, 100, 80])['name'], 'unknown')
        frame = np.full((100, 160, 3), (255, 0, 0), np.uint8)
        car = dict(class_id=2, box=[10, 10, 100, 80])
        self.assertEqual(color_for_plate(frame, [25, 35, 65, 48], [car])['name'], 'blue')
        self.assertEqual(color_for_plate(frame, [110, 35, 150, 48], [car])['name'], 'unknown')
        self.assertEqual(color_for_plate(frame, [25, 35, 65, 48], [car, car])['name'], 'unknown')

    def tracker(self):
        frame = np.full((100, 160, 3), (255, 0, 0), np.uint8)
        tracker = LatestVehicleTracker(self.root, now=100)
        for stamp in (100, 101, 102):
            tracker.observe([dict(class_id=2, box=[10, 10, 100, 80])], stamp, (160, 100), frame=frame)
        return tracker, frame

    def test_unknown_candidate_has_crop_and_review_does_not_need_confirmed_ocr(self):
        tracker, frame = self.tracker()
        publish_candidate(self.root, self.prediction(), dict(timestamp=102, sample_id='candidate', session='fixture'), frame)
        tracker.update_candidate(read_json(self.root/'runtime/plate_candidate.json'))
        self.assertIsNotNone(tracker.plate_image)
        self.assertEqual(tracker.latest['candidate_plate'], 'ABX1234')
        self.assertFalse(tracker.latest['plate'])
        path = prepare_review(self.root, tracker)
        snapshot = read_json(path)
        original = (path.parent/snapshot['vehicles'][0]['file']).read_bytes()
        tracker.image[:] = 0
        self.assertEqual((path.parent/snapshot['vehicles'][0]['file']).read_bytes(), original)
        save_confirmation(self.root, snapshot, 0, 'ABC-1234', 'blue', 3, True)
        receipt = read_json(self.root/'runtime/manual_plate_confirmation.json')
        self.assertTrue(tracker.apply_manual_confirmation(receipt))
        self.assertEqual(tracker.latest['plate'], 'ABC1234')
        self.assertTrue(tracker.latest['manual_locked'])
        self.assertFalse((self.root/'runtime/plate_events').exists())

    def test_delayed_manual_confirmation_cannot_relabel_a_new_vehicle(self):
        tracker, frame = self.tracker()
        snapshot = read_json(prepare_review(self.root, tracker))
        tracker.observe([dict(class_id=2, box=[110, 10, 150, 80])], 105, (160, 100), frame=frame)
        save_confirmation(self.root, snapshot, 0, 'ABC1234', 'blue', 3, True)
        self.assertFalse(tracker.apply_manual_confirmation(read_json(self.root/'runtime/manual_plate_confirmation.json')))
        self.assertFalse(tracker.latest['plate'])
        self.assertEqual(self.registry.read()[0][0]['plate'], 'ABC1234')

    def test_live_database_event_crop_association_and_canonical_notification_cooldown(self):
        self.registry.confirm('ABC1234', 'blue')
        tracker, frame = self.tracker()
        c = LiveClipConsensus(self.root, continuous=True)
        c.set_context(dict(request_id='fixture', started_at=100))
        event = None
        for stamp, raw in [(100, 'ABX1234'), (101, 'ABC1284'), (102, 'ZBC1234')]:
            event = c.observe(self.prediction(raw, color='blue'), stamp, str(stamp), [], frame)
        self.assertEqual(event['text'], 'ABC1234')
        tracker.update_plates([event])
        self.assertEqual(tracker.latest['plate'], 'ABC1234')
        self.assertTrue(tracker.latest['database_matched'])
        self.assertIsNotNone(tracker.plate_image)
        for stamp in (103, 104, 105):
            self.assertIsNone(c.observe(self.prediction('ABC1234', color='blue'), stamp, str(stamp), [], frame))
        self.assertFalse(c.memory.entries(105))

    def test_ambiguous_registry_result_stays_pending_instead_of_notifying_raw_guess(self):
        self.registry.confirm('ABC1234')
        self.registry.confirm('ABC1235')
        c = LiveClipConsensus(self.root, continuous=True)
        c.set_context(dict(request_id='fixture', started_at=100))
        for stamp in (100, 101, 102, 103):
            self.assertIsNone(c.observe(self.prediction('ABC1236'), stamp, str(stamp), []))
        self.assertFalse((self.root/'runtime/plate_events').exists())

    def test_review_button_and_shortcut_work_in_reordered_dashboard(self):
        c = PreviewController()
        c.layout = PreviewLayout.create(1600, 900, 640, 720, dashboard=True,
                                        panel_order=['controls', 'latest', 'camera', 'plates'])
        self.assertEqual(c.key(ord('p')).kind, 'review_plate')
        r = c.layout.buttons['review_plate']
        c.mouse('down', r.x+2, r.y+2)
        self.assertEqual(c.mouse('up', r.x+2, r.y+2).kind, 'review_plate')

    def test_review_photos_expire_but_confirmed_database_persists(self):
        tracker, frame = self.tracker()
        path = prepare_review(self.root, tracker)
        snapshot = read_json(path)
        self.registry.confirm('ABC1234')
        unrelated = path.parent.parent/'unrelated'
        unrelated.mkdir()
        (unrelated/'keep.txt').write_text('not an owned review')
        prune_history(self.root, now=snapshot['captured_at']+8*86400)
        self.assertFalse(path.parent.exists())
        self.assertTrue((unrelated/'keep.txt').exists())
        self.assertEqual(self.registry.read()[0][0]['plate'], 'ABC1234')


if __name__ == '__main__':
    unittest.main()
