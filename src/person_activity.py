"""Person/vehicle activity and continuous inactivity, independent of camera APIs.

``PersonActivityTracker.update(people, width, height, now)`` accepts the filtered
object dictionaries from ``objects_in_region`` and returns whether one moved
or arrived. The legacy class name remains compatible. Keep this tracker across
recording boundaries; call ``reset()``
when the selected region changes. Coordinates are normalized, so resizing the
capture does not count as motion. ``now`` must be a monotonic timestamp.

Motion means a center change of at least 1% of either frame dimension (minimum
3 pixels), or a width/height change of at least 2% (minimum 6 pixels). A stable
anchor accumulates slow displacement instead of comparing only adjacent frames.
These box-based thresholds suppress small detector jitter; they do not infer
body/limb motion that leaves the person box unchanged.

``InactivityTimer.observe(request_id, moving, now)`` returns True after 120
continuous observable seconds without activity for that recording. Call
``suspend()`` during missing capture/inference or region editing: the next
observation begins a new interval. ``reset()`` clears only the timer's event.
"""
from dataclasses import dataclass
import math


def _geometry(box):
    left, top, right, bottom = box
    return ((left+right)/2, (top+bottom)/2, right-left, bottom-top)


def _iou(first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, right-left)*max(0.0, bottom-top)
    first_area = (first[2]-first[0])*(first[3]-first[1])
    second_area = (second[2]-second[0])*(second[3]-second[1])
    return intersection/(first_area+second_area-intersection)


def _finite_positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("%s must be finite and positive" % name)
    return value


def _timestamp(now):
    now = float(now)
    if not math.isfinite(now):
        raise ValueError("now must be a finite monotonic timestamp")
    return now


@dataclass
class _Track:
    box: tuple
    anchor: tuple
    category: str = "person"
    missing_since: object = None


class PersonActivityTracker:
    """Associate boxes without track IDs and return one activity flag per frame.

    ``missing_seconds`` tolerates brief observed dropouts. Time during which
    update is not called never expires an otherwise continuously visible track.
    Invalid person boxes raise ValueError so callers can suspend their timer
    rather than accidentally treating unknown detections as confirmed absence.
    """

    def __init__(self, missing_seconds=1.0):
        self.missing_seconds = _finite_positive(missing_seconds, "missing_seconds")
        self.reset()

    def reset(self):
        self._tracks = []
        self._last_update = None

    def update(self, people, width, height, now):
        width = _finite_positive(width, "width")
        height = _finite_positive(height, "height")
        now = _timestamp(now)
        if self._last_update is not None and now < self._last_update:
            raise ValueError("now must not move backwards")
        boxes = []
        categories = []
        for person in people:
            try:
                left, top, right, bottom = map(float, person["box"])
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError("person box must contain four finite coordinates") from error
            if (not all(math.isfinite(value) for value in (left, top, right, bottom))
                    or right <= left or bottom <= top):
                raise ValueError("person box must be finite and have positive area")
            boxes.append((left/width, top/height, right/width, bottom/height))
            # Car/bus/truck classification can fluctuate for one vehicle, but
            # a nearby person must never inherit that vehicle's motion anchor.
            categories.append("person" if person.get("class_id", 0) == 0 else "vehicle")

        # Only observed absence starts expiry. A slow inference, paused startup,
        # or recording rollover must not turn an unchanged person into an arrival.
        tracks = [track for track in self._tracks
                  if track.missing_since is None or now-track.missing_since <= self.missing_seconds]
        candidates = []
        for track_index, track in enumerate(tracks):
            previous = _geometry(track.box)
            for person_index, box in enumerate(boxes):
                if track.category != categories[person_index]:
                    continue
                current = _geometry(box)
                distance = math.hypot(current[0]-previous[0], current[1]-previous[1])
                overlap = _iou(track.box, box)
                proximity = max(0.04, min(0.20, 0.5*max(
                    math.hypot(previous[2], previous[3]), math.hypot(current[2], current[3]))))
                if overlap >= 0.05 or distance <= proximity:
                    candidates.append((1.0-overlap+2.0*distance, track_index, person_index))

        matched_tracks, matched_people = set(), set()
        moving = False
        thresholds = (max(0.01, 3.0/width), max(0.01, 3.0/height),
                      max(0.02, 6.0/width), max(0.02, 6.0/height))
        for _, track_index, person_index in sorted(candidates):
            if track_index in matched_tracks or person_index in matched_people:
                continue
            track, box = tracks[track_index], boxes[person_index]
            current = _geometry(box)
            displaced = any(abs(value-anchor)+1e-12 >= threshold
                            for value, anchor, threshold in zip(current, track.anchor, thresholds))
            if displaced:
                moving = True
                track.anchor = current
            track.box = box
            track.missing_since = None
            matched_tracks.add(track_index)
            matched_people.add(person_index)

        for index, track in enumerate(tracks):
            if index not in matched_tracks and track.missing_since is None:
                track.missing_since = now
        for index, box in enumerate(boxes):
            if index not in matched_people:
                tracks.append(_Track(box, _geometry(box), categories[index]))
                moving = True
        self._tracks = tracks
        self._last_update = now
        return moving


class InactivityTimer:
    """Require a continuous known interval without movement for one event ID."""

    def __init__(self, seconds=120):
        self.seconds = _finite_positive(seconds, "seconds")
        self.reset()

    def reset(self):
        self._request_id = None
        self._inactive_since = None
        self._last_observed = None

    def suspend(self):
        """Discard elapsed inactivity because an intervening period is unknown."""
        self._inactive_since = None
        self._last_observed = None

    def observe(self, request_id, moving, now):
        now = _timestamp(now)
        if not request_id:
            self.reset()
            return False
        if self._last_observed is not None and now < self._last_observed:
            raise ValueError("now must not move backwards")
        if request_id != self._request_id:
            self._request_id = request_id
            self._inactive_since = None
        if self._inactive_since is None or moving:
            self._inactive_since = now
        self._last_observed = now
        return not moving and now-self._inactive_since >= self.seconds
