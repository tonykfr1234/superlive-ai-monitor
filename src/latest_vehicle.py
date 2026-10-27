"""Latest live vehicle observation, with conservative spatial OCR association."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import time
import hashlib
import re
import uuid

from record_protocol import atomic_json, read_json


TAIPEI = timezone(timedelta(hours=8))
VEHICLE_CLASSES = {2, 3, 5, 7}
HISTORY_SECONDS = 20.0
CONTINUITY_SECONDS = 2.0
SAMPLE_SKEW_SECONDS = 1.0
RETENTION_SECONDS = 7 * 24 * 60 * 60


def _stamp(value):
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _text(value):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 32 and value.isprintable() else ""


def _box(value, width, height):
    try:
        if len(value) != 4:
            return None
        x1, y1, x2, y2 = map(float, value)
        if 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height:
            return x1 / width, y1 / height, x2 / width, y2 / height
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _intersection(first, second):
    return max(0, min(first[2], second[2])-max(first[0], second[0])) * max(
        0, min(first[3], second[3])-max(first[1], second[1]))


def _area(box):
    return (box[2]-box[0]) * (box[3]-box[1])


def _continuous(previous, current):
    """Tolerate normal box jitter; added/departed vehicles invalidate old OCR."""
    if len(previous) != len(current):
        return None
    pairs = sorted(((_intersection(old, new) / (_area(old)+_area(new)-_intersection(old, new)), i, j)
                    for i, old in enumerate(previous) for j, new in enumerate(current)), reverse=True)
    old_used, new_used = {}, set()
    for overlap, old, new in pairs:
        if overlap >= .3 and old not in old_used and new not in new_used:
            old_used[old] = current[new]
            new_used.add(new)
    return [old_used[index] for index in range(len(previous))] if len(old_used) == len(previous) else None


class LatestVehicleTracker:
    """A short observation buffer, not a separate vehicle tracking model.

    Only an uninterrupted, spatially continuous group may retain its plate.
    Restored state is display-only: a new live observation starts a new group.
    """

    def __init__(self, root, now=None):
        self.path = Path(root) / "runtime" / "latest_vehicle.json"
        now = time.time() if now is None else float(now)
        saved = read_json(self.path, {})
        stamp = _stamp(saved.get("timestamp")) if isinstance(saved, dict) else None
        self.latest = {}
        if stamp is not None and now-RETENTION_SECONDS <= stamp <= now:
            self.latest = {"timestamp": stamp, "plate": "", "plate_timestamp": None}
            plate_stamp = _stamp(saved.get("plate_timestamp"))
            if plate_stamp is not None and stamp-RETENTION_SECONDS <= plate_stamp <= stamp + SAMPLE_SKEW_SECONDS:
                self.latest.update(plate=_text(saved.get("plate")), plate_timestamp=plate_stamp)
        # Cover the full OCR latency window even at the supported 60 FPS limit.
        self._observations = deque(maxlen=2048)
        self._boxes = []
        self._active = False
        self._context_start = 0.0
        self._last_observation = 0.0
        self._frame_size = None
        self._dirty = False
        self._last_write = -math.inf
        self.persistence_error = False
        self.image = None
        self.plate_image = None
        self.review_images = []
        self.review_colors = []
        self._candidate_timestamp = 0
        self._manual_receipt_id = None
        self._last_image_at = -math.inf
        self.image_path = self.path.with_suffix(".jpg")
        self.plate_image_path = self.path.with_name('latest_plate.jpg')
        # A hash binds the restored photo to the JSON observation. A crash between
        # the two atomic writes cannot display a previous vehicle under a new plate.
        if self.latest and isinstance(saved, dict) and saved.get("image_sha256"):
            try:
                import cv2
                import numpy as np
                data = self.image_path.read_bytes()
                if hashlib.sha256(data).hexdigest() == saved["image_sha256"]:
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    image_stamp = _stamp(saved.get("image_timestamp"))
                    if image is not None and image_stamp is not None and image_stamp <= self.latest["timestamp"]:
                        self.image = image
                        self.latest.update(image_timestamp=image_stamp, image_sha256=saved["image_sha256"])
            except (OSError, ValueError, ImportError):
                pass
        if self.latest.get('plate') and saved.get('plate_image_sha256'):
            try:
                import cv2
                import numpy as np
                data = self.plate_image_path.read_bytes()
                if hashlib.sha256(data).hexdigest() == saved['plate_image_sha256']:
                    self.plate_image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    self.latest['plate_image_sha256'] = saved['plate_image_sha256']
            except (OSError, ValueError, ImportError):
                pass
        if self.latest.get('plate'):
            self.latest['plate_display'] = _text(saved.get('plate_display')) or self.latest['plate']
            self.latest['user_corrected'] = saved.get('user_corrected') is True
        if self.latest:
            self.latest.update(group_id=saved.get('group_id') or uuid.uuid4().hex,
                               vehicle_count=saved.get('vehicle_count', 1))
            for key in ('plate_vehicle_index', 'observed_plate', 'database_matched'):
                if key in saved:
                    self.latest[key] = saved[key]

    def snapshot(self):
        return deepcopy(self.latest)

    def flush(self, force=False):
        """At most one fsync per second, except a new group/plate or suspension."""
        now = time.monotonic()
        if not self._dirty or not self.latest or (not force and now-self._last_write < 1):
            return
        try:
            atomic_json(self.path, self.latest)
        except OSError:
            self.persistence_error = True
        else:
            self._dirty = False
            self.persistence_error = False
        self._last_write = now

    def suspend(self):
        self._active = False
        self._boxes = []
        self._observations.clear()
        self.flush(force=True)

    def observe(self, detections, timestamp, frame_size, available=True, frame=None):
        """Observe only a successfully captured, inferred, non-editing ROI frame."""
        if not available:
            self.suspend()
            return
        stamp = _stamp(timestamp)
        if stamp is None or stamp <= self._last_observation or stamp <= self.latest.get("timestamp", 0):
            return
        width, height = frame_size
        boxes = [box for item in detections if item.get("class_id") in VEHICLE_CLASSES
                 for box in [_box(item.get("box"), width, height)] if box is not None]
        previous_time = self._last_observation
        self._last_observation = stamp
        if not boxes:
            # Keep the last actual vehicle time. Pending OCR can still use its
            # existing buffer until a new group or a capture pause replaces it.
            self._active = False
            self._boxes = []
            self.flush(force=True)
            return
        ordered = (_continuous(self._boxes, boxes) if self._active and self._frame_size == frame_size
                   and stamp-previous_time <= CONTINUITY_SECONDS else None)
        same_group = ordered is not None
        if not same_group:
            self._context_start = stamp
            self._observations.clear()
            self.latest = {"timestamp": stamp, "plate": "", "plate_timestamp": None}
            self.latest['group_id'] = uuid.uuid4().hex
            self.image = None
            self.plate_image = None
            self.review_images, self.review_colors = [], []
            self._last_image_at = -math.inf
        else:
            boxes = ordered
        self.latest["timestamp"] = stamp
        self.latest['vehicle_count'] = len(boxes)
        self._boxes, self._frame_size, self._active = boxes, frame_size, True
        self._observations.append((stamp, boxes, width, height))
        while self._observations and stamp-self._observations[0][0] > HISTORY_SECONDS:
            self._observations.popleft()
        self._dirty = True
        if frame is not None:
            self.capture_image(frame, boxes, stamp)
        self.flush(force=not same_group)

    def capture_image(self, frame, boxes, stamp):
        """Keep a bounded, local crop of this vehicle group; never use stale frames."""
        if stamp-self._last_image_at < 1 or not boxes:
            return
        import cv2
        height, width = frame.shape[:2]
        if self._frame_size != (width, height):
            return
        left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
        right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad_x, pad_y = (right-left)*.12, (bottom-top)*.12
        x1, y1 = max(0, int((left-pad_x)*width)), max(0, int((top-pad_y)*height))
        x2, y2 = min(width, math.ceil((right+pad_x)*width)), min(height, math.ceil((bottom+pad_y)*height))
        crop = frame[y1:y2, x1:x2].copy()
        if not crop.size:
            return
        scale = min(1, 960/crop.shape[1], 540/crop.shape[0])
        if scale < 1:
            crop = cv2.resize(crop, (max(1, round(crop.shape[1]*scale)), max(1, round(crop.shape[0]*scale))))
        self.image = crop
        from vehicle_registry import vehicle_color
        self.review_images, self.review_colors = [], []
        for bx1, by1, bx2, by2 in boxes:
            bounds = [int(bx1*width), int(by1*height), min(width, math.ceil(bx2*width)), min(height, math.ceil(by2*height))]
            single = frame[bounds[1]:bounds[3], bounds[0]:bounds[2]].copy()
            if not single.size:
                continue
            scale = min(1, 640/single.shape[1], 400/single.shape[0])
            if scale < 1:
                single = cv2.resize(single, (max(1, round(single.shape[1]*scale)), max(1, round(single.shape[0]*scale))))
            self.review_images.append(single)
            self.review_colors.append(vehicle_color(frame, bounds))
        self.latest["image_timestamp"] = stamp
        self.latest.pop("image_sha256", None)
        self._last_image_at = stamp
        try:
            ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                return
            data = encoded.tobytes()
            self.image_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.image_path.with_suffix(".jpg.tmp")
            temporary.write_bytes(data)
            temporary.replace(self.image_path)
            self.latest["image_sha256"] = hashlib.sha256(data).hexdigest()
        except (OSError, cv2.error):
            self.persistence_error = True

    def update_plates(self, events):
        if not self._observations or self.latest.get('manual_locked'):
            return
        for event in events:
            if (not isinstance(event, dict) or event.get("confirmed") is not True
                    or event.get("source") not in (None, "", "live", "live_recording")):
                continue
            text = _text(event.get("text"))
            observed_text = _text(event.get('observed_text')) or text
            stamp = _stamp(event.get("timestamp"))
            if (not text or stamp is None
                    or not self._context_start <= stamp <= self.latest["timestamp"] + SAMPLE_SKEW_SECONDS):
                continue
            evidence = event.get("evidence")
            if not isinstance(evidence, list):
                continue
            samples = {}
            for sample in evidence:
                if (not isinstance(sample, dict) or not sample.get("sample_id")
                        or not (_text(sample.get("text")) == observed_text
                                or (event.get('database_matched') and sample.get('registry_plate') == text))):
                    continue
                sample_time = _stamp(sample.get("timestamp"))
                if sample_time is None or not self._context_start <= sample_time <= stamp:
                    continue
                observed, boxes, width, height = min(self._observations, key=lambda row: abs(row[0]-sample_time))
                plate_box = _box(sample.get("bbox"), width, height)
                if abs(observed-sample_time) > SAMPLE_SKEW_SECONDS or plate_box is None:
                    continue
                # Require containment in exactly one vehicle, not a neighboring
                # plate or ambiguous overlapping boxes.
                matches = [index for index, box in enumerate(boxes)
                           if _intersection(plate_box, box) / _area(plate_box) >= .8]
                if len(matches) != 1:
                    continue
                samples.setdefault(matches[0], {})[str(sample["sample_id"])] = sample_time
            confirmed_times = [max(values.values()) for values in samples.values()
                               if len(values) >= 2 and len(set(values.values())) >= 2]
            if not confirmed_times:
                continue
            plate_time = max(confirmed_times)
            if plate_time > (self.latest.get("plate_timestamp") or 0):
                if self.latest.get('plate') != text:
                    self.plate_image = None
                    self.latest.pop('plate_image_sha256', None)
                self.latest.update(plate=text, plate_timestamp=plate_time)
                self.latest.update(plate_display=_text(event.get('display_text')) or text,
                                   user_corrected=event.get('user_corrected') is True,
                                   observed_plate=observed_text, database_matched=event.get('database_matched') is True)
                self.latest['plate_vehicle_index'] = max(
                    (i for i in samples if len(samples[i]) >= 2 and len(set(samples[i].values())) >= 2),
                    key=lambda i: max(samples[i].values()))
                image_meta = event.get('plate_image')
                if isinstance(image_meta, dict) and any(
                        len(values) >= 2 and len(set(values.values())) >= 2
                        and image_meta.get('sample_id') in values
                        and values[image_meta['sample_id']] == image_meta.get('timestamp')
                        for values in samples.values()):
                    self.load_plate_image(image_meta)
                self._dirty = True
        self.flush(force=True)

    def update_candidate(self, event):
        if not self._observations or not isinstance(event, dict) or self.latest.get('plate'):
            return
        stamp = _stamp(event.get('timestamp'))
        if stamp is None or stamp <= self._candidate_timestamp or not self._context_start <= stamp <= self.latest['timestamp']+SAMPLE_SKEW_SECONDS:
            return
        observed, boxes, width, height = min(self._observations, key=lambda row: abs(row[0]-stamp))
        box = _box(event.get('bbox'), width, height)
        if box is None or abs(observed-stamp) > SAMPLE_SKEW_SECONDS:
            return
        matches = [index for index, vehicle in enumerate(boxes) if _intersection(box, vehicle)/_area(box) >= .8]
        if len(matches) != 1:
            return
        self._candidate_timestamp = stamp
        self.latest.update(candidate_plate=_text(event.get('text')), plate_vehicle_index=matches[0])
        meta = event.get('plate_image')
        self.plate_image = None
        self.latest.pop('plate_image_sha256', None)
        if isinstance(meta, dict) and meta.get('timestamp') == stamp:
            self.load_plate_image(meta)
        self._dirty = True
        self.flush()

    def apply_manual_confirmation(self, receipt):
        if not isinstance(receipt, dict) or not receipt.get('id') or receipt['id'] == self._manual_receipt_id:
            return False
        self._manual_receipt_id = receipt['id']
        if receipt.get('group_id') != self.latest.get('group_id') or not _text(receipt.get('plate')):
            return False
        index = receipt.get('vehicle_index')
        if not isinstance(index, int) or not 0 <= index < self.latest.get('vehicle_count', 0):
            return False
        if index != self.latest.get('plate_vehicle_index'):
            self.plate_image = None
            self.latest.pop('plate_image_sha256', None)
        self.latest.update(plate=receipt['plate'], plate_display=receipt.get('plate_display') or receipt['plate'],
                           user_corrected=True, database_matched=False, plate_vehicle_index=index,
                           observed_plate=receipt.get('observed_plate', ''), manual_locked=True,
                           plate_timestamp=self.latest['timestamp'])
        self._dirty = True
        self.flush(force=True)
        return True

    def load_plate_image(self, metadata):
        name = metadata.get('file', '')
        if not isinstance(name, str) or not re.fullmatch('[0-9a-f]{64}\\.jpg', name):
            return
        try:
            import cv2
            import numpy as np
            path = self.path.parent/'plate_crops'/name
            if path.stat().st_size > 2*1024*1024:
                return
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != metadata.get('sha256'):
                return
            photo = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if photo is None:
                return
            self.plate_image = photo
            temporary = self.plate_image_path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(self.plate_image_path)
            self.latest['plate_image_sha256'] = digest
        except (OSError, ValueError, ImportError):
            pass


def latest_vehicle_text(record, chinese=True):
    label = "最近偵測到車輛：" if chinese else "Latest vehicle detected: "
    stamp = _stamp(record.get("timestamp")) if isinstance(record, dict) else None
    if stamp is None:
        return label + ("尚未偵測到車輛" if chinese else "No vehicle detected yet")
    try:
        date = datetime.fromtimestamp(stamp, TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return label + ("時間無效" if chinese else "Time unavailable")
    plate = _text(record.get("plate"))
    return label + date + ("（台灣時間）｜" if chinese else " (Taipei) | ") + (
        ("車牌：" if chinese else "Plate: ") + plate if plate else
        ("車牌未確認" if chinese else "Plate not confirmed"))
