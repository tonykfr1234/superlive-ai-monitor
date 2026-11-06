"""Compact, local Telegram delivery status for the latest vehicle card."""
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from record_protocol import read_json


TAIPEI = timezone(timedelta(hours=8))
ERROR_MATCH_SECONDS = 15
EVENT_MATCH_SECONDS = 5
LOG_TAIL_BYTES = 64 * 1024


def _number(value):
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _clock(value):
    number = _number(value)
    return (datetime.fromtimestamp(number, TAIPEI).strftime("%H:%M:%S")
            if number is not None else "--:--:--")


def _telegram_configured(root):
    path = Path(root) / "config" / "telegram.env"
    try:
        values = {}
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        return bool(values.get("BOT_TOKEN") and values.get("CHAT_ID"))
    except OSError:
        return False


def telegram_button_state(root):
    """State shown on the Telegram settings button.

    "enabled" once a bot and chat are configured and nothing is failing,
    "retrying" while any video, photo or plate job has failed and waits to
    retry, and "not_configured" without credentials.
    """
    if not _telegram_configured(root):
        return "not_configured"
    for folder in ("telegram_upload_queue", "telegram_photo_queue", "telegram_plate_delivery"):
        for path in (Path(root) / "runtime" / folder).glob("*.json"):
            job = read_json(path, {})
            if (isinstance(job, dict) and not job.get("sent_at") and not job.get("local_only")
                    and (_number(job.get("attempts")) or 0) > 0):
                return "retrying"
    return "enabled"


def _last_error(root):
    runtime = read_json(Path(root) / "runtime" / "recorder_last_error.json", {})
    if isinstance(runtime, dict) and _number(runtime.get("timestamp")) is not None:
        return runtime
    path = Path(root) / "logs" / "Stage5C" / "recorder.log"
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - LOG_TAIL_BYTES))
            text = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    matches = re.findall(
        r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
        r"EVENT_RECORD_ERROR=([A-Za-z_][A-Za-z0-9_]*)(?: DETAIL=(.*))?",
        text,
        re.MULTILINE,
    )
    if not matches:
        return {}
    stamp, kind, detail = matches[-1]
    try:
        parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
    except ValueError:
        return {}
    return {"timestamp": parsed.timestamp(), "error_type": kind, "message": detail.strip()}


def _error_reason(error):
    message = " ".join(str(error.get("message") or "").split()).strip()
    lowered = message.casefold()
    if "stale" in lowered or "frame" in lowered:
        return "ADB 新影格尚未就緒"
    if "window" in lowered:
        return "找不到模擬器畫面"
    if "writer" in lowered:
        return "影片寫入器無法啟動"
    return error.get("error_type") or "錄影失敗"


def _pending_jobs(root):
    result = []
    directory = Path(root) / "runtime" / "telegram_upload_queue"
    for path in directory.glob("*.json"):
        job = read_json(path, {})
        if not isinstance(job, dict) or job.get("sent_at"):
            continue
        stamp = _number(job.get("trigger_created_at", job.get("clip_started_at")))
        if stamp is not None:
            result.append((stamp, job))
    return sorted(result, key=lambda item: item[0])


def _photo_job(root, event_time):
    if event_time is None:
        return None
    directory = Path(root) / "runtime" / "telegram_photo_queue"
    matches = []
    for path in directory.glob("*.json"):
        job = read_json(path, {})
        stamp = _number(job.get("trigger_created_at")) if isinstance(job, dict) else None
        if stamp is not None and abs(stamp-event_time) <= EVENT_MATCH_SECONDS:
            matches.append(job)
    return max(matches, key=lambda job: float(job.get("trigger_created_at", 0))) if matches else None


def telegram_delivery_status(root, event_time=None, now=None):
    """Return one compact line for the lower-left vehicle card."""
    root = Path(root)
    now = time.time() if now is None else float(now)
    event_time = _number(event_time)
    if not _telegram_configured(root):
        return "Telegram：尚未設定，目前不會推播"

    state = read_json(root / "runtime" / "recording_state.json", {})
    state = state if isinstance(state, dict) else {}
    trigger = _number(state.get("trigger_created_at"))
    active_matches = (event_time is not None and trigger is not None
                      and abs(trigger-event_time) <= EVENT_MATCH_SECONDS)
    if state.get("status") in ("STARTING", "RECORDING") and active_matches:
        started = _number(state.get("clip_started_at"))
        if state.get("status") == "STARTING" or started is None:
            return "Telegram：錄影啟動中；完成後排隊上傳"
        earliest = started + 120
        latest = started + 300
        if now < earliest:
            return "Telegram：最快 %s 後（錄影滿 2 分鐘）" % _clock(earliest)
        return "Telegram：錄影中；結束後推播（最晚 %s）" % _clock(latest)

    jobs = _pending_jobs(root)
    matching = [(index, job) for index, (stamp, job) in enumerate(jobs)
                if event_time is not None and abs(stamp-event_time) <= EVENT_MATCH_SECONDS]
    if matching:
        index, job = matching[0]
        quiet = read_json(root / "runtime" / "telegram_quiet.json", {})
        quiet_until = _number(quiet.get("quiet_until")) if isinstance(quiet, dict) else None
        retry_at = _number(job.get("retry_at")) or 0
        if quiet_until is not None and quiet_until > now:
            return "Telegram：靜默至 %s，之後開始上傳" % _clock(quiet_until)
        if retry_at > now:
            return "Telegram：傳送重試。%s 再試" % _clock(retry_at)
        if state.get("status") in ("STARTING", "RECORDING"):
            return "Telegram：已排隊；等目前錄影結束"
        if index:
            return "Telegram：已排隊；前面還有 %d 支影片" % index
        return "Telegram：已排隊，下一支上傳"

    photo = _photo_job(root, event_time)
    if photo is not None:
        sent_at = _number(photo.get("sent_at"))
        retry_at = _number(photo.get("retry_at")) or 0
        if sent_at is not None:
            return "Telegram：錄影失敗，照片已推播 %s" % _clock(sent_at)
        if retry_at > now:
            return "Telegram：錄影失敗，照片 %s 重試" % _clock(retry_at)
        return "Telegram：錄影失敗，照片已排隊"

    error = _last_error(root)
    error_at = _number(error.get("timestamp"))
    if (error_at is not None and event_time is not None
            and 0 <= error_at-event_time <= ERROR_MATCH_SECONDS):
        return "Telegram：未排隊｜錄影失敗（%s）" % _error_reason(error)
    if jobs:
        return "Telegram：待傳 %d 支；本事件尚未建立錄影" % len(jobs)
    return "Telegram：尚未建立可推播的錄影"
