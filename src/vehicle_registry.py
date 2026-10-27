"""Private, human-confirmed SQLite vehicle registry and multi-frame matching."""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from collections import deque, Counter

from plate_corrections import normalized, display_plate

COLORS = {'unknown': '不確定', 'white': '白', 'silver': '銀', 'gray': '灰',
          'black': '黑', 'red': '紅', 'orange': '橘', 'yellow': '黃',
          'green': '綠', 'blue': '藍', 'purple': '紫', 'brown': '棕'}


def distance(a, b):
    a, b = normalized(a), normalized(b)
    row = list(range(len(b)+1))
    for i, left in enumerate(a, 1):
        next_row = [i]
        for j, right in enumerate(b, 1):
            next_row.append(min(row[j]+1, next_row[-1]+1, row[j-1]+(left != right)))
        row = next_row
    return row[-1]


class VehicleRegistry:
    def __init__(self, root):
        self.path = Path(root).resolve()/'config/vehicle_registry.sqlite3'

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS vehicles (
                plate TEXT PRIMARY KEY, color TEXT NOT NULL, enabled INTEGER NOT NULL,
                confirmed_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations (
                id INTEGER PRIMARY KEY, plate TEXT NOT NULL, color TEXT NOT NULL,
                observed TEXT NOT NULL, context TEXT NOT NULL, action TEXT NOT NULL,
                confirmed_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS preferences (
                id INTEGER PRIMARY KEY CHECK(id=1), tolerance INTEGER NOT NULL,
                use_color INTEGER NOT NULL, revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO preferences VALUES (1,3,1,0);
        ''')
        db.commit()
        return db

    def read(self):
        if not self.path.is_file():
            return [], dict(tolerance=3, use_color=True, revision=0)
        # Read-only connections cannot create an empty database on a bad path.
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('SELECT * FROM vehicles ORDER BY confirmed_at DESC')]
            settings = dict(db.execute('SELECT * FROM preferences WHERE id=1').fetchone())
        return rows, settings

    def confirm(self, plate, color='unknown', observed='', context=None, tolerance=3, use_color=True):
        if not re.fullmatch(r'[A-Za-z0-9 -]{4,14}', str(plate)):
            raise ValueError('請輸入英文、數字及連字號車牌。')
        plate = normalized(plate)
        if not 4 <= len(plate) <= 10 or not re.search('[A-Z]', plate) or not re.search('[0-9]', plate):
            raise ValueError('正確車牌需有英文及數字，共 4～10 碼。')
        if color not in COLORS or int(tolerance) not in (1, 2, 3):
            raise ValueError('顏色或容錯設定無效。')
        now = time.time()
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO vehicles VALUES (?,?,1,?) ON CONFLICT(plate) DO UPDATE SET '
                       'color=excluded.color,enabled=1,confirmed_at=excluded.confirmed_at', (plate, color, now))
            db.execute('INSERT INTO confirmations(plate,color,observed,context,action,confirmed_at) '
                       'VALUES (?,?,?,?,?,?)', (plate, color, normalized(observed), json.dumps(context or {}), 'confirm', now))
            db.execute('UPDATE preferences SET tolerance=?,use_color=?,revision=revision+1 WHERE id=1',
                       (int(tolerance), int(bool(use_color))))
        return dict(plate=plate, plate_display=display_plate(plate), color=color, confirmed_at=now)

    def disable(self, plate):
        with closing(self.connect()) as db, db:
            db.execute('UPDATE vehicles SET enabled=0 WHERE plate=?', (normalized(plate),))
            db.execute('UPDATE preferences SET revision=revision+1 WHERE id=1')
            db.execute('INSERT INTO confirmations(plate,color,observed,context,action,confirmed_at) '
                       'VALUES (?,?,?,?,?,?)', (normalized(plate), 'unknown', '', '{}', 'disable', time.time()))


def vehicle_color(frame, box):
    """Coarse body-color hint; near-monochrome/low-light scenes are unknown."""
    unknown = dict(name='unknown', confidence=0.)
    if frame is None:
        return unknown
    import cv2
    import numpy as np
    try:
        x1, y1, x2, y2 = map(float, box)
        height, width = frame.shape[:2]
        if not all(math.isfinite(x) for x in (x1, y1, x2, y2)) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return unknown
        scene = cv2.cvtColor(cv2.resize(frame, (80, 48)), cv2.COLOR_BGR2HSV)
        if np.mean(scene[:, :, 1] > 45) < .025 or np.mean(scene[:, :, 2]) < 45:
            return unknown
        # Interior bands favor body panels over road, plate, wheels and windshield.
        crop = frame[int(y1+(y2-y1)*.40):int(y1+(y2-y1)*.77),
                     int(x1+(x2-x1)*.15):int(x1+(x2-x1)*.85)]
        if crop.size < 120:
            return unknown
        hsv = cv2.cvtColor(cv2.resize(crop, (80, 40)), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        h, s, v = hsv.T
        labels = np.full(len(h), 'gray', dtype=object)
        labels[v < 60] = 'black'
        labels[(s < 45) & (v >= 185)] = 'white'
        labels[(s < 45) & (v >= 130) & (v < 185)] = 'silver'
        colored = (s >= 55) & (v >= 55)
        for name, mask in [('red', (h < 10) | (h >= 170)), ('orange', (h >= 10) & (h < 22)),
                           ('yellow', (h >= 22) & (h < 36)), ('green', (h >= 36) & (h < 88)),
                           ('blue', (h >= 88) & (h < 132)), ('purple', (h >= 132) & (h < 170))]:
            labels[colored & mask] = name
        labels[colored & (h >= 8) & (h < 25) & (v < 140)] = 'brown'
        name, count = Counter(labels).most_common(1)[0]
        ratio = count / len(labels)
        if ratio < .60:
            return unknown
        return dict(name=str(name), confidence=round(min(.95, ratio), 3))
    except (ValueError, TypeError, cv2.error):
        return unknown


def color_for_plate(frame, plate_box, vehicles):
    try:
        px1, py1, px2, py2 = map(float, plate_box)
        area = (px2-px1)*(py2-py1)
        if area <= 0:
            return dict(name='unknown', confidence=0.)
        contained = []
        for vehicle in vehicles or []:
            if vehicle.get('class_id') not in (2, 3, 5, 7):
                continue
            x1, y1, x2, y2 = vehicle['box']
            overlap = max(0, min(px2, x2)-max(px1, x1))*max(0, min(py2, y2)-max(py1, y1))
            if overlap/area >= .8:
                contained.append(vehicle['box'])
        if len(contained) == 1:
            return vehicle_color(frame, contained[0])
    except (KeyError, TypeError, ValueError):
        pass
    return dict(name='unknown', confidence=0.)


def compatible_color(stored, observed):
    return stored == observed or {stored, observed} <= {'silver', 'gray', 'white'}


def match_registry(raw, color, rows, settings):
    """Database has priority within tolerance; ties/conflicting color stay unresolved."""
    raw = normalized(raw)
    if not 4 <= len(raw) <= 10:
        return None
    limit = max(1, min(3, int(settings.get('tolerance', 3))))
    reliable = (settings.get('use_color', True) and color.get('name') in COLORS
                and color.get('name') != 'unknown' and color.get('confidence', 0) >= .65)
    candidates = []
    for row in rows:
        if not row.get('enabled'):
            continue
        delta = distance(raw, row['plate'])
        if delta > limit:
            continue
        if delta == 0:
            return dict(plate=row['plate'], distance=0, color_used=False)
        known = row.get('color') in COLORS and row['color'] != 'unknown'
        if reliable and known and not compatible_color(row['color'], color['name']):
            continue
        color_match = bool(reliable and known and compatible_color(row['color'], color['name']))
        candidates.append((delta, not color_match, row['plate'], color_match))
    candidates.sort()
    if not candidates:
        return None
    first = candidates[0]
    if len(candidates) > 1 and candidates[1][:2] == first[:2]:
        return None
    return dict(plate=first[2], distance=first[0], color_used=first[3])


class DatabaseConsensus:
    def __init__(self, root):
        self.registry = VehicleRegistry(root)
        self.rows, self.settings = [], dict(tolerance=3, use_color=True, revision=-1)
        self.queues = {}
        self.last_reload = -math.inf
        self.pending = False

    def reset(self):
        self.queues.clear()

    def reload(self):
        now = time.monotonic()
        if now-self.last_reload < .5:
            return
        self.last_reload = now
        try:
            rows, settings = self.registry.read()
        except (OSError, sqlite3.Error):
            rows, settings = [], dict(tolerance=3, use_color=True, revision=-1)
        if settings.get('revision') != self.settings.get('revision'):
            self.reset()
        self.rows, self.settings = rows, settings

    def observe(self, prediction, timestamp, sample_id):
        self.reload()
        self.pending = False
        try:
            timestamp = float(timestamp)
            box = list(map(float, prediction['bbox']))
            detector = float(prediction['detector_confidence'])
            if (len(box) != 4 or not all(math.isfinite(v) for v in [timestamp, *box, detector])
                    or not .4 <= detector <= 1 or box[2] <= box[0] or box[3] <= box[1] or not sample_id):
                return None
        except (KeyError, TypeError, ValueError):
            return None
        variants = prediction.get('variants') or [prediction.get('selected')]
        options = []
        color = prediction.get('vehicle_color') or dict(name='unknown', confidence=0.)
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            try:
                confidence = float(variant.get('confidence', 0))
                minimum = float(variant.get('min_character_confidence', confidence))
                if not .65 <= confidence <= 1 or not .35 <= minimum <= 1:
                    continue
            except (ValueError, TypeError):
                continue
            match = match_registry(variant.get('raw_text'), color, self.rows, self.settings)
            if any(row.get('enabled') and 4 <= len(normalized(variant.get('raw_text'))) <= 10
                   and distance(variant.get('raw_text'), row['plate']) <= self.settings.get('tolerance', 3)
                   for row in self.rows):
                self.pending = True
            if match:
                options.append((match, variant, confidence))
        # Conflicting crop/scale variants are not independent frames or a vote.
        if not options or len({option[0]['plate'] for option in options}) != 1:
            return None
        match, variant, confidence = max(options, key=lambda o: o[2])
        self.queues = {key: q for key, q in self.queues.items() if q and timestamp-q[-1]['timestamp'] <= 8}
        queue = self.queues.setdefault(match['plate'], deque(maxlen=8))
        if queue:
            previous = queue[-1]
            old = previous['bbox']
            overlap = max(0, min(old[2], box[2])-max(old[0], box[0]))*max(0, min(old[3], box[3])-max(old[1], box[1]))
            area = min((old[2]-old[0])*(old[3]-old[1]), (box[2]-box[0])*(box[3]-box[1]))
            if timestamp <= previous['timestamp'] or any(s['sample_id'] == str(sample_id) for s in queue):
                return None
            if overlap/area < .25:
                queue.clear()
        raw = normalized(variant['raw_text'])
        queue.append(dict(text=raw, raw_text=variant['raw_text'], registry_plate=match['plate'],
                          confidence=confidence, detector_confidence=detector, timestamp=timestamp,
                          sample_id=str(sample_id), bbox=box, vehicle_color=color, database_distance=match['distance']))
        while queue and timestamp-queue[0]['timestamp'] > 8:
            queue.popleft()
        if len(queue) < 3 or timestamp-queue[0]['timestamp'] < .75:
            return None
        return dict(text=match['plate'], observed_text=raw, display_text=display_plate(match['plate']),
                    timestamp=timestamp, confirmed=True, confidence=confidence, evidence=list(queue),
                    database_matched=True, database_distance=max(s['database_distance'] for s in queue),
                    database_color_used=match['color_used'], vehicle_color=color,
                    correction_source='human_confirmed_database', learning_influenced=True)
