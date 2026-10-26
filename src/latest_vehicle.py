"""Latest live vehicle observation, with conservative spatial OCR association."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import time

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

    def observe(self, detections, timestamp, frame_size, available=True):
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
        else:
            boxes = ordered
        self.latest["timestamp"] = stamp
        self._boxes, self._frame_size, self._active = boxes, frame_size, True
        self._observations.append((stamp, boxes, width, height))
        while self._observations and stamp-self._observations[0][0] > HISTORY_SECONDS:
            self._observations.popleft()
        self._dirty = True
        self.flush(force=not same_group)

    def update_plates(self, events):
        if not self._observations:
            return
        for event in events:
            if (not isinstance(event, dict) or event.get("confirmed") is not True
                    or event.get("source") not in (None, "", "live", "live_recording")):
                continue
            text = _text(event.get("text"))
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
                        or _text(sample.get("text")) != text):
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
                self.latest.update(plate=text, plate_timestamp=plate_time)
                self._dirty = True
        self.flush(force=True)


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
