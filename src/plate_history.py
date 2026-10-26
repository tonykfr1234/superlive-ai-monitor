"""Local, seven-day plate results. Historical replay never queues Telegram."""
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import time

from record_protocol import atomic_json, read_json
from video_location import _absolute_video

RETENTION_SECONDS = 7 * 24 * 60 * 60
TAIPEI = timezone(timedelta(hours=8))
OUTCOMES = {"no_plate", "insufficient", "error"}


def _stamp(event):
    try:
        value = float(event["timestamp"])
        return value if math.isfinite(value) else None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def _is_result(event):
    return (isinstance(event, dict) and bool(event.get("id")) and _stamp(event) is not None
            and (event.get("confirmed") is True or event.get("outcome") in OUTCOMES))


def record_result(root, event, now=None):
    """Idempotent per result ID; never treat an error as a negative detection."""
    now = time.time() if now is None else float(now)
    stamp = _stamp(event)
    if not _is_result(event) or not now-RETENTION_SECONDS <= stamp <= now+60:
        return False
    result = dict(event)
    source_path = (_absolute_video(result.get("source_video_path"))
                   or _absolute_video(result.get("source_video")))
    if source_path is not None:
        # Private runtime metadata only. The caller's live/Telegram event is
        # left untouched, and the visible filename remains a basename.
        result["source_video_path"] = str(source_path)
        result["source_video"] = source_path.name
    else:
        result.pop("source_video_path", None)
    if result.get("source_video"):
        result["source_video"] = str(result["source_video"]).replace("\\", "/").rsplit("/", 1)[-1]
    digest = hashlib.sha256(str(result["id"]).encode("utf-8")).hexdigest()
    folder = Path(root)/"runtime"/"plate_history"
    destination = folder/(digest+".json")
    atomic_json(destination, result)
    # A later completed replay supersedes the earlier per-clip summary.
    # Independent confirmed readings remain available for review.
    if result.get("source_video"):
        for path in folder.glob("*.json"):
            if path == destination:
                continue
            previous = read_json(path, {})
            if (_is_result(previous) and previous.get("confirmed") is not True
                    and previous.get("source_video") == result["source_video"]
                    and not (previous.get("source_video_path") and result.get("source_video_path")
                             and previous["source_video_path"] != result["source_video_path"])):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
    return True


def _read_history(root, now):
    """Return readable results and whether absence can safely be concluded."""
    items = {}
    readable = True
    runtime = Path(root)/"runtime"
    # Read the old journal too, so existing confirmed events survive upgrades.
    for folder in (runtime/"plate_events", runtime/"plate_history"):
        try:
            paths = list(folder.glob("*.json"))
        except OSError:
            readable = False
            continue
        for path in paths:
            event = read_json(path)
            if not _is_result(event):
                # A locked/truncated result is unknown, not evidence of no plate.
                readable = False
                continue
            stamp = _stamp(event)
            if now-RETENTION_SECONDS <= stamp <= now+60:
                items[str(event["id"])] = event
    return (sorted(items.values(), key=lambda event: (float(event["timestamp"]), str(event["id"])), reverse=True),
            readable)


def load_history(root, now=None):
    now = time.time() if now is None else float(now)
    return _read_history(root, now)[0]


def replay_progress(root, now=None):
    """Aggregate the latest shard-count batch without starting replay or alerts."""
    now = time.time() if now is None else float(now)
    shards = []
    for path in (Path(root)/"runtime"/"plate_replay").glob("progress-*-of-*.json"):
        value = read_json(path)
        if not isinstance(value, dict):
            continue
        keys = ("shard_index", "shard_count", "total", "complete", "skipped", "pending", "errors")
        if any(not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 0
               for key in keys):
            continue
        index, count = value["shard_index"], value["shard_count"]
        if count < 1 or index >= count or path.name != "progress-%d-of-%d.json" % (index, count):
            continue
        processed = value["complete"]+value["skipped"]+value["errors"]
        if processed+value["pending"] > value["total"]:
            continue
        try:
            stamp = float(value["updated_at"])
            if isinstance(value["updated_at"], bool) or not math.isfinite(stamp) or stamp > now+1:
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if value.get("status") not in ("RUNNING", "COMPLETE", "COMPLETE_WITH_ERRORS", "PENDING"):
            continue
        shards.append(dict(value, updated_at=stamp, processed=processed))
    if not shards:
        return {}
    latest = max(shards, key=lambda value: value["updated_at"])
    shards = [value for value in shards if value["shard_count"] == latest["shard_count"]]
    result = {key: sum(value[key] for value in shards) for key in ("total", "processed", "pending", "errors")}
    running = any(value["status"] == "RUNNING" and -1 <= now-value["updated_at"] < 30 for value in shards)
    stale_running = any(value["status"] == "RUNNING" and now-value["updated_at"] >= 30 for value in shards)
    status = ("RUNNING" if running else "PAUSED" if stale_running else
              "COMPLETE_WITH_ERRORS" if result["errors"] else "PENDING" if result["pending"] else "COMPLETE")
    return dict(result, running=running, stale_running=stale_running, status=status,
                shard_count=latest["shard_count"], updated_at=latest["updated_at"])


def prune_history(root, now=None):
    """Only expire owned result JSON; source recordings are never touched."""
    now = time.time() if now is None else float(now)
    runtime = Path(root)/"runtime"
    removed = 0
    for folder in (runtime/"plate_history", runtime/"plate_events"):
        for path in folder.glob("*.json"):
            event = read_json(path, {})
            if not _is_result(event):
                continue
            stamp = _stamp(event)
            if stamp is not None and stamp < now-RETENTION_SECONDS:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    continue
    # Delivery receipts contain the event ID, not OCR text, but also expire.
    for path in (runtime/"telegram_plate_delivery").glob("*.json"):
        receipt = read_json(path, {})
        if not isinstance(receipt, dict) or not receipt.get("event_id"):
            continue
        stamp = receipt.get("sent_at") or receipt.get("retained_at")
        try:
            if stamp is not None and math.isfinite(float(stamp)) and float(stamp) < now-RETENTION_SECONDS:
                path.unlink(missing_ok=True)
        except (ValueError, TypeError, OSError):
            pass
    return removed


def record_clip_result(root, video, started_at, duration, now=None):
    """Keep a result for every completed clip, including unreadable plates."""
    now = time.time() if now is None else float(now)
    video = Path(video)
    video = video.resolve() if video.is_absolute() else (Path(root)/video).resolve()
    start, end = float(started_at), float(started_at)+float(duration)
    history, history_readable = _read_history(root, now)
    events = [event for event in history if event.get("confirmed") is True
              and start <= float(event["timestamp"]) <= end]
    if events:
        for event in events:
            record_result(root, dict(event, source_video=video.name, source_video_path=str(video)), now)
        return
    state = read_json(Path(root)/"runtime"/"plate_recognition"/"state.json")
    available, candidates = False, 0
    try:
        scanned = float(state["last_sample_at"])
        age = now-float(state["updated_at"])
        candidates = state["detections"]
        available = (history_readable and math.isfinite(scanned) and start <= scanned <= end+8
                     and math.isfinite(age) and -1 <= age <= 30
                     and state.get("status") in ("READY", "PAUSED")
                     and isinstance(candidates, int) and not isinstance(candidates, bool) and candidates >= 0)
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    outcome = ("insufficient" if candidates else "no_plate") if available else "error"
    record_result(root, dict(id="clip:"+Path(video).stem, timestamp=start,
        time=datetime.fromtimestamp(start, TAIPEI).isoformat(timespec="seconds"),
        confirmed=False, outcome=outcome,
        source="live_recording", source_video=video.name, source_video_path=str(video),
        error="" if available else "本段沒有可用的車牌掃描結果"), now)
