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


class PlateService:
    """Nonblocking GUI-facing API; call pause while selecting/changing source.

    ``now`` in submit is a Unix timestamp (time.time), not monotonic time.
    ``roi`` is the same normalized rectangle/polygon used for person detection.
    ``recent`` returns observed plates newest first; matched is separate.
    """
    def __init__(self, root):
        self.root = Path(root)
        self.runtime = self.root / "runtime" / "plate_recognition"
        self.session = uuid.uuid4().hex
        self.generation = 0
        self._process = None
        self._thread = None
        self._log = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._active = False
        self._latest = None
        self._roi_signature = None
        self._last_submit = float("-inf")
        self._status = "STOPPED"
        self._error = ""
        self._snapshot = {}
        self._read_at = float("-inf")

    def start(self):
        if self._thread is not None:
            return self._status != "UNAVAILABLE"
        executable = self.root / ".plate-venv" / "Scripts" / "python.exe"
        if not executable.exists():
            self._status, self._error = "UNAVAILABLE", "Plate OCR environment is not installed"
            return False
        self.runtime.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._status = "STARTING"
        try:
            atomic_json(self.runtime / "control.json", self._control())
            self._log = (self.runtime / "worker.log").open("ab")
            self._process = subprocess.Popen(
                [str(executable), str(Path(__file__).resolve()), "--worker", "--root", str(self.root),
                 "--session", self.session], stdout=self._log, stderr=self._log,
                cwd=str(self.root), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._thread = threading.Thread(target=self._io_loop, name="plate-samples", daemon=True)
            self._thread.start()
            return True
        except OSError as error:
            self._status, self._error = "UNAVAILABLE", "Cannot start plate worker: " + type(error).__name__
            if self._log is not None:
                self._log.close()
                self._log = None
            return False

    def _control(self):
        return {"session": self.session, "generation": self.generation,
                "active": self._active and not self._closed, "heartbeat": time.time(),
                "stop": self._closed}

    def submit(self, frame, now=None, roi=None):
        if self._thread is None or self._closed or self._process.poll() is not None:
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

    def _io_loop(self):
        import numpy as np
        while not self._closed:
            self._wake.wait(1.0)
            self._wake.clear()
            with self._lock:
                sample, self._latest = self._latest, None
                control = self._control()
            try:
                atomic_json(self.runtime / "control.json", control)
                if sample is not None:
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
            except (OSError, ValueError) as error:
                self._error = "Plate sample unavailable: " + type(error).__name__
        try:
            atomic_json(self.runtime / "control.json", self._control())
        except OSError:
            pass

    def snapshot(self):
        now = time.monotonic()
        if now - self._read_at >= 0.5:
            self._read_at = now
            state = read_json(self.runtime / "state.json", {})
            if isinstance(state, dict) and state.get("session") == self.session:
                self._snapshot = state
        state = dict(self._snapshot)
        state.setdefault("status", self._status)
        state.setdefault("model", MODEL_LABEL)
        state.setdefault("error", self._error)
        state.setdefault("recent", [])
        if self._process is not None and self._process.poll() is not None and not self._closed:
            state.update(status="ERROR", error=state.get("error") or "Plate worker exited")
        elif self._closed:
            state["status"] = "STOPPED"
        elif self._thread is not None and not self._active:
            state["status"] = "PAUSED" if state["status"] == "READY" else state["status"]
        return state

    def recent(self):
        return list(self.snapshot().get("recent", []))[:5]

    def stop(self):
        self.pause()
        self._closed = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
        if self._log is not None:
            self._log.close()
        self._status = "STOPPED"


def run_worker(root, session):
    root = Path(root)
    runtime = root / "runtime" / "plate_recognition"
    recent_file = runtime / "recent.json"
    recent = read_json(recent_file, [])
    if not isinstance(recent, list):
        recent = []
    recent = [item for item in recent if isinstance(item, dict) and item.get("confirmed")][:5]

    def state(status, error=""):
        atomic_json(runtime / "state.json", {"session": session, "status": status,
                    "error": error, "model": MODEL_LABEL, "recent": recent,
                    "updated_at": time.time(), "tolerance": DEFAULT_MAX_DISTANCE})

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
        try:
            consensus.last_emitted[item.get("matched") or item["text"]] = float(item["timestamp"])
        except (KeyError, ValueError, TypeError):
            pass
    last_sample, context, last_state = None, None, 0.0
    while True:
        control = read_json(runtime / "control.json", {})
        now = time.time()
        if (not isinstance(control, dict) or control.get("session") != session
                or control.get("stop") is True):
            break
        if now - float(control.get("heartbeat", 0)) > HEARTBEAT_MAX_AGE:
            break
        current_context = (control.get("generation"), tuple(load_allowlist(root)))
        if current_context != context:
            consensus.reset()
            consensus.allowlist = list(current_context[1])
            context = current_context
        if now - last_state > 2:
            state("READY" if control.get("active") else "PAUSED")
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
            predictions = alpr.predict(frame)
            # Pause/ROI/source changes can happen while inference is running.
            current_control = read_json(runtime / "control.json", {})
            if not valid_sample(metadata, current_control, time.time()):
                continue
            if tuple(load_allowlist(root)) != context[1]:
                consensus.reset()
                continue
            height, width = frame.shape[:2]
            for prediction in predictions:
                if prediction.ocr is None:
                    continue
                box = prediction.detection.bounding_box
                bounds = [int(box.x1), int(box.y1), int(box.x2), int(box.y2)]
                if not box_in_roi(bounds, metadata.get("roi"), width, height):
                    continue
                selected, variants = recognition_candidates(alpr, frame, prediction)
                if selected is None or not valid_sample(metadata,
                        read_json(runtime / "control.json", {}), time.time()):
                    continue
                event = consensus.observe(selected["raw_text"], selected["confidence"],
                    metadata["timestamp"], metadata["sample_id"], bounds,
                    prediction.detection.confidence, variants=variants)
                if event is None:
                    continue
                atomic_json(root / "runtime" / "plate_events" / (event["id"] + ".json"), event)
                recent = [event] + recent[:4]
                atomic_json(recent_file, recent)
                state("READY")
        except (OSError, ValueError, KeyError):
            # A failed or partially replaced input is not a successful reading.
            time.sleep(0.1)
        except Exception as error:
            consensus.reset()
            state("ERROR", "Plate inference failed: " + type(error).__name__)
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
