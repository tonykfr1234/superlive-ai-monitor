"""Atomic, local IPC between the person detector and event recorder."""
import json
import math
import os
import tempfile
import time
from pathlib import Path

from region_policy import validate_roi


MIN_RECORD_SECONDS = 120.0
MAX_RECORD_SECONDS = 300.0


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/antivirus may briefly hold the destination without
        # delete sharing. Retry replacement; never truncate the existing JSON.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def recorder_ready(state, pending=False, now=None, pid_alive=None):
    """Only a fresh idle recorder can accept another detection/notification."""
    if pending or not isinstance(state, dict) or state.get("status") != "IDLE":
        return False
    now = time.time() if now is None else now
    try:
        age = now - float(state["updated_at"])
        pid = int(state["pid"])
        return 0 <= age < 10 and pid > 0 and (pid_alive is None or pid_alive(pid))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def recording_active(state, now=None, pid_alive=None):
    """Identify a fresh live recording event that may receive an end signal."""
    if (not isinstance(state, dict) or state.get("status") != "RECORDING"
            or not isinstance(state.get("request_id"), str) or not state["request_id"]):
        return False
    now = time.time() if now is None else now
    try:
        age = now - float(state["updated_at"])
        pid = int(state["pid"])
        if isinstance(state["pid"], bool) or float(state["pid"]) != pid:
            return False
        return 0 <= age < 10 and pid > 0 and (pid_alive is None or pid_alive(pid))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def valid_end_request(request, request_id, now=None):
    """Accept a fresh event end, validating any immediate successor in full."""
    if (not isinstance(request, dict) or not isinstance(request_id, str) or not request_id
            or request.get("request_id") != request_id or request.get("reason") not in ("NO_MOVEMENT", "MOVEMENT")):
        return False
    now = time.time() if now is None else now
    try:
        if isinstance(request["created_at"], bool):
            return False
        created_at = float(request["created_at"])
        age = now - created_at
        if not (math.isfinite(created_at) and math.isfinite(age) and 0 <= age <= 10):
            return False
        if request["reason"] == "NO_MOVEMENT":
            return True
        successor = request.get("next_request")
        if not isinstance(successor, dict):
            return False
        successor_id = successor.get("request_id")
        if (not isinstance(successor_id, str) or not successor_id.strip()
                or len(successor_id) > 200 or successor_id == request_id):
            return False
        if isinstance(successor.get("created_at"), bool):
            return False
        successor_age = now - float(successor["created_at"])
        count, confidence = successor["count"], successor["confidence"]
        if (not math.isfinite(successor_age) or not 0 <= successor_age <= 10
                or not isinstance(count, int) or isinstance(count, bool) or count <= 0
                or isinstance(confidence, bool) or not math.isfinite(float(confidence))
                or not 0 < float(confidence) <= 1):
            return False
        validate_roi(successor["roi"])
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def telegram_send_allowed(root, now=None):
    """Fail closed during a pending/startup/first-two-minute recording window.

    Quiet reservations survive recorder failure and manual stop. A standalone
    upload queue with no recorder IPC remains usable for recovery and testing.
    """
    runtime = Path(root) / "runtime"
    now = time.time() if now is None else now
    try:
        if not math.isfinite(float(now)) or (runtime / "record_request.json").exists():
            return False
        quiet_file = runtime / "telegram_quiet.json"
        if quiet_file.exists():
            quiet = read_json(quiet_file)
            if (not isinstance(quiet, dict) or not isinstance(quiet.get("request_id"), str)
                    or not quiet["request_id"] or isinstance(quiet.get("quiet_until"), bool)):
                return False
            quiet_until = float(quiet["quiet_until"])
            if not math.isfinite(quiet_until) or quiet_until > now:
                return False
        state_file = runtime / "recording_state.json"
        if not state_file.exists():
            return True
        state = read_json(state_file)
        if not isinstance(state, dict):
            return False
        status = state.get("status")
        if status == "RECORDING":
            if (not isinstance(state.get("request_id"), str) or not state["request_id"]
                    or isinstance(state.get("elapsed_seconds"), bool)):
                return False
            elapsed = float(state["elapsed_seconds"])
            return math.isfinite(elapsed) and elapsed >= MIN_RECORD_SECONDS
        return status in ("IDLE", "PAUSED", "STOPPED", "ERROR", "UPLOADING")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False
