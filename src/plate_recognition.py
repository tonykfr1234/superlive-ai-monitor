"""Optional, isolated plate detection/OCR and evidence-preserving matching.

The GUI process needs only numpy. FastALPR/OpenCV-headless live exclusively in
``.plate-venv``. A single replaceable sample bounds the queue when OCR is slow.
No image, plate, or allowlist is sent to an external service by this module.
"""
import argparse
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from record_protocol import atomic_json, read_json
from region_policy import box_in_roi, validate_roi
from plate_history import load_history, record_result, prune_history, replay_progress


DETECTOR_MODEL = "yolo-v9-t-384-license-plate-end2end"
OCR_MODEL = "cct-s-v2-global-model"
MODEL_LABEL = "YOLOv9 plate + CCT-S OCR"
TAIPEI = timezone(timedelta(hours=8))
DEFAULT_MAX_DISTANCE = 2
MAX_SAMPLE_AGE = 8.0
HEARTBEAT_MAX_AGE = 12.0


def normalize_plate(text):
    """Use ASCII letters/digits; formatting and letter case are irrelevant."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def edit_distance(left, right):
    """Levenshtein distance: substitutions, missing and extra characters count."""
    left, right = normalize_plate(left), normalize_plate(right)
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, 1):
        current = [row]
        for col, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (char != other)))
        previous = current
    return previous[-1]


def match_plate(text, allowlist, max_distance=DEFAULT_MAX_DISTANCE):
    """Return a unique nearest match, retaining observed text and ambiguity.

    A fuzzy match is a candidate association, never a corrected OCR reading.
    A tie between equally close entries requires review rather than guessing.
    """
    observed = normalize_plate(text)
    result = {"text": observed, "matched": None, "distance": None,
              "match_status": "unmatched", "candidates": []}
    if not 4 <= len(observed) <= 12:
        result["match_status"] = "insufficient"
        return result
    targets = sorted({normalize_plate(value) for value in allowlist
                      if 4 <= len(normalize_plate(value)) <= 12})
    ranked = sorted((edit_distance(observed, value), value) for value in targets)
    if not ranked or ranked[0][0] > int(max_distance):
        return result
    distance = ranked[0][0]
    candidates = [value for score, value in ranked if score == distance]
    result.update(distance=distance, candidates=candidates)
    if len(candidates) != 1:
        result["match_status"] = "ambiguous"
    else:
        result.update(matched=candidates[0], match_status="exact" if distance == 0 else "fuzzy")
    return result


def character_confidence(text, values):
    """Exclude trailing OCR padding probabilities from visible-character mean.

    FastPlateOCR returns probabilities for all ten slots even for a six-letter
    plate. Padding probabilities must not make unreadable characters look good.
    """
    visible = str(text or "").rstrip("_ ")
    positions = [i for i, char in enumerate(visible.upper()) if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
    if not positions:
        return 0.0
    try:
        if isinstance(values, (float, int)):
            scores = [float(values)]
        else:
            if len(values) <= max(positions):
                return 0.0
            scores = [float(values[i]) for i in positions]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores):
            return 0.0
        return sum(scores) / len(scores)
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0


def load_allowlist(root):
    data = read_json(Path(root) / "config" / "plate_allowlist.json", [])
    if isinstance(data, dict):
        data = data.get("plates", [])
    if not isinstance(data, list):
        return []
    return sorted({normalize_plate(item) for item in data if isinstance(item, str)
                   and 4 <= len(normalize_plate(item)) <= 12})


class PlateConsensus:
    """Require independent frames and retain the observations behind a match."""
    def __init__(self, allowlist=(), max_distance=2, samples=3, window=8.0,
                 debounce=60.0, min_confidence=0.72):
        self.allowlist = list(allowlist)
        self.max_distance = max_distance
        self.samples = max(3, int(samples))
        self.window = float(window)
        self.debounce = float(debounce)
        self.min_confidence = float(min_confidence)
        self.observations = {}
        self.last_emitted = {}

    def reset(self):
        # A pause/config/source change must never combine old and new evidence.
        self.observations.clear()

    def observe(self, raw_text, confidence, timestamp, sample_id, bbox=None,
                detector_confidence=1.0, variants=None):
        try:
            now, confidence = float(timestamp), float(confidence)
            detector_confidence = float(detector_confidence)
        except (TypeError, ValueError, OverflowError):
            return None
        if (not all(math.isfinite(value) for value in (now, confidence, detector_confidence))
                or not self.min_confidence <= confidence <= 1
                or not 0.4 <= detector_confidence <= 1 or not sample_id):
            return None
        match = match_plate(raw_text, self.allowlist, self.max_distance)
        if match["match_status"] == "insufficient":
            return None
        key = match["matched"] or match["text"]
        for old_key in list(self.observations):
            queue = self.observations[old_key]
            while queue and now - queue[0]["timestamp"] > self.window:
                queue.popleft()
            if not queue:
                del self.observations[old_key]
        queue = self.observations.setdefault(key, deque(maxlen=24))
        if any(item["sample_id"] == str(sample_id) for item in queue):
            return None
        if queue and now <= queue[-1]["timestamp"]:
            return None
        evidence = {"raw_text": str(raw_text), "text": match["text"],
                    "confidence": confidence, "detector_confidence": detector_confidence,
                    "timestamp": now, "sample_id": str(sample_id),
                    "bbox": list(bbox) if bbox is not None else None,
                    "matched": match["matched"], "distance": match["distance"]}
        if variants is not None:
            evidence["variants"] = list(variants)
        queue.append(evidence)
        if len(queue) < self.samples or now - queue[0]["timestamp"] < 0.75:
            return None
        previous = self.last_emitted.get(key)
        if previous is not None and now - previous < self.debounce:
            return None
        counts = Counter(item["text"] for item in queue)
        best = max(queue, key=lambda item: (counts[item["text"]], item["confidence"], item["timestamp"]))
        chosen = match_plate(best["raw_text"], self.allowlist, self.max_distance)
        self.last_emitted[key] = now
        return dict(chosen, id=uuid.uuid4().hex, timestamp=now,
                    time=datetime.fromtimestamp(now, TAIPEI).isoformat(timespec="seconds"),
                    raw_text=best["raw_text"], confidence=best["confidence"],
                    detector_confidence=best["detector_confidence"], bbox=best["bbox"],
                    confirmed=True, samples=len(queue), evidence=list(queue),
                    model=MODEL_LABEL, tolerance=int(self.max_distance))


def valid_sample(metadata, control, now):
    """Reject a stale frame or a frame from before pause/source/ROI changes."""
    try:
        return (isinstance(metadata, dict) and isinstance(control, dict)
                and control.get("active") is True
                and metadata.get("session") == control.get("session")
                and metadata.get("generation") == control.get("generation")
                and 0 <= float(now) - float(control["heartbeat"]) <= HEARTBEAT_MAX_AGE
                and 0 <= float(now) - float(metadata["timestamp"]) <= MAX_SAMPLE_AGE)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def recognition_candidates(alpr, frame, prediction):
    """Try one crop enlargement; choose by OCR confidence, never by allowlist.

    Each source frame still contributes one consensus vote. Upscaling changes
    interpolation, not the amount of camera detail, and both readings survive.
    """
    import cv2
    original = prediction.ocr
    candidates = []
    if original is not None:
        candidates.append({"raw_text": original.text,
                           "confidence": character_confidence(original.text, original.confidence),
                           "variant": "original"})
    box = prediction.detection.bounding_box
    height, width = frame.shape[:2]
    crop = frame[max(0, int(box.y1)):min(height, int(box.y2)),
                 max(0, int(box.x1)):min(width, int(box.x2))]
    if crop.size and crop.shape[0] < 96 and crop.shape[1] < 384:
        enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
        try:
            reading = alpr.ocr.predict(enlarged)
            if reading is not None:
                candidates.append({"raw_text": reading.text,
                    "confidence": character_confidence(reading.text, reading.confidence),
                    "variant": "lanczos3"})
        except Exception:
            pass  # A failed second attempt must not fabricate a replacement.
    valid = [item for item in candidates if 4 <= len(normalize_plate(item["raw_text"])) <= 12]
    return (max(valid, key=lambda item: item["confidence"]) if valid else None), candidates


def detect_plate_observations(alpr, frame):
    """Scan the full image plus overlapping tiles for small distant plates.

    A 1600-pixel camera view shrunk to a 384-pixel detector input can erase a
    readable plate. Four 60% tiles preserve more pixels without requiring a
    larger model. All boxes return to source coordinates and overlapping views
    of one physical plate become one observation, retaining all OCR variants.
    No allowlist is consulted when choosing the observed text.
    """
    height, width = frame.shape[:2]
    regions = [(0, 0, width, height)]
    if width > 960 or height > 540:
        tile_width, tile_height = math.ceil(width * 0.6), math.ceil(height * 0.6)
        regions += [(x, y, x + tile_width, y + tile_height)
                    for y in (0, height - tile_height)
                    for x in (0, width - tile_width)]
    observations = []
    for region in regions:
        x1, y1, x2, y2 = region
        tile = frame[y1:y2, x1:x2]
        for prediction in alpr.predict(tile):
            box = prediction.detection.bounding_box
            bounds = [max(x1, int(box.x1) + x1), max(y1, int(box.y1) + y1),
                      min(x2, int(box.x2) + x1), min(y2, int(box.y2) + y1)]
            if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                continue
            selected, variants = recognition_candidates(alpr, tile, prediction)
            confidence = float(prediction.detection.confidence)
            observations.append({"bbox": bounds, "detector_confidence": confidence,
                "selected": selected, "variants": [dict(variant, source_region=list(region),
                    source_bbox=bounds[:], detector_confidence=confidence) for variant in variants]})

    def same_plate(left, right):
        ax1, ay1, ax2, ay2 = left
        bx1, by1, bx2, by2 = right
        intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
        if not intersection:
            return False
        a, b = (ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1)
        return (intersection / (a + b - intersection) >= 0.35
                or (min(a, b) / max(a, b) >= 0.5 and intersection / min(a, b) >= 0.75))

    # Keep the strongest readable observation, independently of any target text.
    # A detection below the consensus threshold must not suppress a usable one.
    observations.sort(key=lambda item: (
        item["detector_confidence"] >= 0.4, item["selected"] is not None,
        item["selected"]["confidence"] if item["selected"] is not None else 0,
        item["detector_confidence"]), reverse=True)
    merged = []
    for observation in observations:
        existing = next((item for item in merged if same_plate(item["bbox"], observation["bbox"])), None)
        if existing is None:
            merged.append(observation)
        else:
            existing["variants"].extend(observation["variants"])
    return merged


class PlateService:
    """Bounded samples and worker recovery happen outside the GUI thread.

    ``now`` in submit is a Unix timestamp. The monitor supplies ``roi=None``
    for full-frame plate detection, independently of the person region.
    """
    START_TIMEOUT = 120.0
    STATE_MAX_AGE = 30.0
    HEALTHY_RESET_SECONDS = 30.0

    def __init__(self, root):
        self.root = Path(root)
        self.runtime = self.root / "runtime" / "plate_recognition"
        self.session = uuid.uuid4().hex
        self.generation = 0
        self._process = None
        self._thread = None
        self._log = None
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._active = False
        self._latest = None
        self._roi_signature = None
        self._last_submit = float("-inf")
        self._status = "STOPPED"
        self._error = ""
        self._io_error = ""
        self._snapshot = {}
        self._read_at = float("-inf")
        self._started_at = 0.0
        self._retry_at = 0.0
        self._retry_count = 0
        self._healthy_since = None
        self._error_since = None

    def start(self):
        if self._thread is not None:
            return not self._closed and self._thread.is_alive()
        executable = self.root / ".plate-venv" / "Scripts" / "python.exe"
        if not executable.exists():
            self._status, self._error = "UNAVAILABLE", "Plate OCR environment is not installed"
            return False
        try:
            self.runtime.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._status, self._error = "UNAVAILABLE", "Cannot prepare plate worker: " + type(error).__name__
            return False
        self._closed = False
        self._status = "STARTING"
        self._thread = threading.Thread(target=self._io_loop, name="plate-samples", daemon=True)
        self._thread.start()
        self._wake.set()
        return True

    def _control(self):
        return {"session": self.session, "generation": self.generation,
                "active": self._active and not self._closed, "heartbeat": time.time(),
                "stop": self._closed}

    def _spawn_worker(self, now):
        # Only the I/O thread starts children. The process lock ensures stop()
        # can never miss a child registered concurrently with shutdown.
        with self._process_lock:
            if self._closed:
                return
            with self._lock:
                self.session = uuid.uuid4().hex
                self.generation += 1
                self._latest = None
                self._active = False
                self._roi_signature = None
                self._last_submit = float("-inf")
                self._snapshot = {"recent": self._snapshot.get("recent", [])}
                self._read_at = float("-inf")
                control = self._control()
            # A locked old file is harmless: its session will never match.
            try:
                (self.runtime / "sample.npz").unlink(missing_ok=True)
            except OSError:
                pass
            atomic_json(self.runtime / "control.json", control)
            if self._closed:
                return
            if self._log is None:
                self._log = (self.runtime / "worker.log").open("ab")
            executable = self.root / ".plate-venv" / "Scripts" / "python.exe"
            self._process = subprocess.Popen(
                [str(executable), str(Path(__file__).resolve()), "--worker", "--root", str(self.root),
                 "--session", self.session], stdout=self._log, stderr=self._log,
                cwd=str(self.root), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._started_at = now
            self._healthy_since = self._error_since = None
            self._status, self._error = "STARTING", ""

    def _stop_process(self, graceful=False):
        with self._process_lock:
            process, self._process = self._process, None
        if process is None:
            return
        # Windows venv python.exe is a launcher: terminating it alone leaves
        # the real OCR child alive, especially when native inference is stuck.
        # Capture only this launcher's descendants before it can disappear.
        try:
            import psutil
        except ImportError:
            psutil = None
        descendants = []
        if psutil is not None and isinstance(process.pid, int):
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:
                pass
        deadline = time.monotonic() + (6.0 if graceful else 4.0)

        def remaining(limit):
            return max(0.0, min(limit, deadline-time.monotonic()))

        if graceful and process.poll() is None:
            try:
                process.wait(timeout=remaining(2.0))
            except (OSError, subprocess.TimeoutExpired):
                pass
        for child in reversed(descendants):
            try:
                child.terminate()
            except psutil.Error:
                pass
        try:
            if process.poll() is None:
                process.terminate()
        except OSError:
            pass
        if descendants:
            _, alive = psutil.wait_procs(descendants, timeout=remaining(2.0))
            for child in alive:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            if alive:
                psutil.wait_procs(alive, timeout=remaining(1.0))
        try:
            try:
                process.wait(timeout=remaining(1.0))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=remaining(1.0))
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _read_state(self):
        state = read_json(self.runtime / "state.json", {})
        with self._lock:
            if isinstance(state, dict) and state.get("session") == self.session:
                self._snapshot = state
            return dict(self._snapshot)

    def _health_issue(self, now, state):
        process = self._process
        if process is None:
            return None
        if process.poll() is not None:
            return "Plate worker exited"
        if state.get("session") != self.session or state.get("status") == "STARTING":
            return "Plate worker startup timed out" if now-self._started_at > self.START_TIMEOUT else None
        try:
            age = time.time()-float(state["updated_at"])
            if math.isfinite(age) and -1 <= age <= self.STATE_MAX_AGE:
                return None
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
        return "Plate worker heartbeat is stale"

    def _schedule_restart(self, now, reason):
        self._stop_process()
        if self._closed:
            return
        self._retry_count += 1
        self._retry_at = now + min(60.0, 5.0 * 2 ** min(self._retry_count-1, 4))
        self._healthy_since = self._error_since = None
        self._status, self._error = "RESTARTING", reason
        with self._lock:
            self.generation += 1
            self._latest = None
        print("PLATE_RESTART attempt=%d delay=%ds reason=%s" %
              (self._retry_count, self._retry_at-now, reason), flush=True)

    def _supervise(self, now=None):
        if self._closed:
            return
        now = time.monotonic() if now is None else now
        state = self._read_state()
        issue = self._health_issue(now, state)
        if issue:
            self._schedule_restart(now, issue)
        elif self._process is not None and state.get("session") == self.session:
            status = state.get("status")
            if status in ("ERROR", "UNAVAILABLE", "STOPPED"):
                self._healthy_since = None
                if self._error_since is None:
                    self._error_since = now
                elif now-self._error_since >= self.STATE_MAX_AGE:
                    self._schedule_restart(now, "Plate worker remains unavailable")
            elif status in ("READY", "PAUSED"):
                self._error_since = None
                if self._healthy_since is None:
                    self._healthy_since = now
                elif now-self._healthy_since >= self.HEALTHY_RESET_SECONDS:
                    self._retry_count = 0
        if self._process is None and not self._closed and now >= self._retry_at:
            try:
                self._spawn_worker(now)
            except OSError as error:
                self._schedule_restart(now, "Cannot start plate worker: " + type(error).__name__)

    def submit(self, frame, now=None, roi=None):
        if self._thread is None or self._closed:
            return False
        now = time.time() if now is None else float(now)
        if not math.isfinite(now) or frame is None or not getattr(frame, "size", 0):
            return False
        try:
            roi = validate_roi(roi)
            signature = (tuple(frame.shape), json.dumps(roi, sort_keys=True))
        except (TypeError, ValueError, AttributeError):
            return False
        with self._lock:
            if self._closed:
                return False
            if signature != self._roi_signature:
                self.generation += 1
                self._roi_signature = signature
                self._last_submit = float("-inf")
            if now - self._last_submit < 0.5:
                return False
            self._last_submit = now
            self._active = True
            self._latest = (frame.copy(), {"timestamp": now, "session": self.session,
                "generation": self.generation, "sample_id": uuid.uuid4().hex, "roi": roi})
        self._wake.set()
        return True

    def pause(self):
        with self._lock:
            if self._active or self._latest is not None:
                self.generation += 1
            self._active = False
            self._latest = None
            self._last_submit = float("-inf")
        self._wake.set()

    def _write_sample(self, sample):
        import numpy as np
        frame, metadata = sample
        fd, temporary = tempfile.mkstemp(prefix="sample-", suffix=".tmp", dir=self.runtime)
        try:
            with os.fdopen(fd, "wb") as stream:
                np.savez(stream, frame=frame, metadata=json.dumps(metadata))
            for attempt in range(6):
                try:
                    os.replace(temporary, self.runtime / "sample.npz")
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.01 * (2 ** attempt))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _io_cycle(self):
        self._supervise()
        if self._closed:
            return
        with self._lock:
            sample, self._latest = self._latest, None
            control = self._control()
        try:
            atomic_json(self.runtime / "control.json", control)
            if sample is not None:
                self._write_sample(sample)
            if sample is not None or not self._active:
                self._io_error = ""
        except (OSError, ValueError) as error:
            self._io_error = "Plate sample unavailable: " + type(error).__name__

    def _io_loop(self):
        while not self._closed:
            self._wake.wait(1.0)
            self._wake.clear()
            try:
                self._io_cycle()
            except Exception as error:
                # Keep the supervisor alive even if one I/O cycle fails.
                self._io_error = "Plate service unavailable: " + type(error).__name__
        try:
            atomic_json(self.runtime / "control.json", self._control())
        except OSError:
            pass

    def snapshot(self):
        now = time.monotonic()
        if now - self._read_at >= 0.5:
            self._read_at = now
            self._read_state()
        state = dict(self._snapshot)
        if state.get("session") != self.session:
            state = {"recent": state.get("recent", [])}
        state.setdefault("status", self._status)
        state.setdefault("model", MODEL_LABEL)
        state.setdefault("error", self._error)
        state.setdefault("recent", [])
        if self._closed:
            state["status"] = "STOPPED"
        elif self._status == "RESTARTING":
            state.update(status="RESTARTING", error=self._error)
        else:
            issue = self._health_issue(now, state)
            if issue:
                state.update(status="RESTARTING", error=issue)
                self._wake.set()
            elif self._io_error:
                state.update(status="ERROR", error=self._io_error)
            elif self._thread is not None and not self._active and state["status"] == "READY":
                state["status"] = "PAUSED"
        state["restart_count"] = self._retry_count
        state["retry_in"] = max(0, math.ceil(self._retry_at-now)) if state["status"] == "RESTARTING" else 0
        return state

    def recent(self):
        return list(self.snapshot().get("recent", []))

    def stop(self):
        with self._lock:
            self._closed = True
            self._active = False
            self._latest = None
            self.generation += 1
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._stop_process(graceful=True)
        with self._process_lock:
            if self._log is not None:
                self._log.close()
                self._log = None
        self._status = "STOPPED"


class ControlLease:
    """A transient unreadable control file is not an explicit stop request."""
    def __init__(self, session, now=None):
        self.session = session
        self.last_seen = time.monotonic() if now is None else float(now)

    def inspect(self, control, wall_now=None, now=None):
        wall_now = time.time() if wall_now is None else float(wall_now)
        now = time.monotonic() if now is None else float(now)
        if isinstance(control, dict):
            owner = control.get("session")
            if isinstance(owner, str) and owner:
                if owner == self.session and control.get("stop") is True:
                    return "stop"
                try:
                    raw = control["heartbeat"]
                    stamp = float(raw)
                    age = wall_now-stamp
                    valid = not isinstance(raw, bool) and math.isfinite(stamp) and 0 <= age <= HEARTBEAT_MAX_AGE
                except (KeyError, ValueError, TypeError, OverflowError):
                    valid = False
                if valid:
                    if owner != self.session:
                        return "stop"
                    self.last_seen = now
                    return "active" if control.get("active") is True else "paused"
        return "stop" if now-self.last_seen > HEARTBEAT_MAX_AGE else "wait"


def run_worker(root, session):
    root = Path(root)
    runtime = root / "runtime" / "plate_recognition"
    recent_file = runtime / "recent.json"
    recent = load_history(root)
    processed_frames, last_sample_at, detections = 0, None, 0
    state_write_error = ""
    last_prune = float("-inf")

    def state(status, error=""):
        nonlocal state_write_error, recent, last_prune
        try:
            if time.monotonic()-last_prune >= 60:
                prune_history(root)
                last_prune = time.monotonic()
            recent = load_history(root)
            atomic_json(recent_file, recent)
            atomic_json(runtime / "state.json", {"session": session, "status": status,
                    "error": error, "model": MODEL_LABEL, "recent": recent,
                    "updated_at": time.time(), "tolerance": DEFAULT_MAX_DISTANCE,
                    "processed_frames": processed_frames, "last_sample_at": last_sample_at,
                    "detections": detections, "replay": replay_progress(root)})
            state_write_error = ""
            return True
        except (OSError, ValueError, TypeError) as failure:
            name = type(failure).__name__
            if name != state_write_error:
                print("PLATE_STATE_WRITE_ERROR=" + name, flush=True)
            state_write_error = name
            return False

    state("STARTING")
    try:
        import numpy as np
        import onnxruntime as ort
        from fast_alpr import ALPR
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        alpr = ALPR(detector_model=DETECTOR_MODEL, ocr_model=OCR_MODEL,
                    detector_providers=["CPUExecutionProvider"],
                    ocr_providers=["CPUExecutionProvider"], ocr_device="cpu",
                    detector_sess_options=options, ocr_sess_options=options)
    except Exception as error:
        # Log exception type only: provider/download errors can contain paths/URLs.
        state("UNAVAILABLE", "Plate model unavailable: " + type(error).__name__)
        return 1
    consensus = PlateConsensus(load_allowlist(root))
    for item in recent:
        if item.get("confirmed") is not True:
            continue
        try:
            key = item.get("matched") or item["text"]
            consensus.last_emitted[key] = max(float(item["timestamp"]),
                                             consensus.last_emitted.get(key, float("-inf")))
        except (KeyError, ValueError, TypeError):
            pass
    last_sample, context, last_state = None, None, 0.0
    lease = ControlLease(session)
    inference_error = ""
    while True:
        control = read_json(runtime / "control.json", None)
        now = time.time()
        control_status = lease.inspect(control, wall_now=now)
        if control_status == "stop":
            break
        if control_status == "wait":
            consensus.reset()
            if now-last_state > 2:
                state("WAITING", "Waiting for parent control heartbeat")
                last_state = now
            time.sleep(0.1)
            continue
        current_context = (control.get("generation"), tuple(load_allowlist(root)))
        if current_context != context:
            consensus.reset()
            consensus.allowlist = list(current_context[1])
            context = current_context
        if now - last_state > 2:
            state("ERROR" if inference_error else ("READY" if control.get("active") else "PAUSED"),
                  inference_error)
            last_state = now
        if not control.get("active"):
            time.sleep(0.1)
            continue
        try:
            with np.load(runtime / "sample.npz", allow_pickle=False) as data:
                metadata = json.loads(str(data["metadata"].item()))
                if metadata.get("sample_id") == last_sample or not valid_sample(metadata, control, now):
                    time.sleep(0.05)
                    continue
                frame = data["frame"].copy()
            last_sample = metadata["sample_id"]
            predictions = detect_plate_observations(alpr, frame)
            # Pause/ROI/source changes can happen while inference is running.
            current_control = read_json(runtime / "control.json", {})
            if not valid_sample(metadata, current_control, time.time()):
                continue
            if tuple(load_allowlist(root)) != context[1]:
                consensus.reset()
                continue
            inference_error = ""
            processed_frames += 1
            last_sample_at = metadata["timestamp"]
            detections = len(predictions)
            height, width = frame.shape[:2]
            for prediction in predictions:
                selected = prediction["selected"]
                if selected is None:
                    continue
                bounds = prediction["bbox"]
                if not box_in_roi(bounds, metadata.get("roi"), width, height):
                    continue
                if not valid_sample(metadata,
                        read_json(runtime / "control.json", {}), time.time()):
                    continue
                event = consensus.observe(selected["raw_text"], selected["confidence"],
                    metadata["timestamp"], metadata["sample_id"], bounds,
                    prediction["detector_confidence"], variants=prediction["variants"])
                if event is None:
                    continue
                atomic_json(root / "runtime" / "plate_events" / (event["id"] + ".json"), event)
                record_result(root, event)
                state("READY")
        except (OSError, ValueError, KeyError) as error:
            # A failed or partially replaced input is not a successful reading.
            inference_error = "Plate sample or inference unavailable: " + type(error).__name__
            state("ERROR", inference_error)
            time.sleep(0.1)
        except Exception as error:
            consensus.reset()
            inference_error = "Plate inference failed: " + type(error).__name__
            state("ERROR", inference_error)
            time.sleep(1.0)
    state("STOPPED")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    if not args.worker:
        parser.error("Use PlateService from the GUI; standalone use requires --worker")
    raise SystemExit(run_worker(args.root, args.session))
