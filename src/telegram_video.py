"""Durable Telegram queue; new dispatches honor every clip's quiet period.

Old clips and retries remain queued while any recording is in its first two
minutes. The guard is checked before preparation and immediately before POST.
A request already in flight before a quiet period begins cannot be recalled;
Telegram's eventual delivery time is outside this process's control. Network
work never holds a recorder transition lock or blocks capture.
"""
import os
import subprocess
import threading
import time
import uuid
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from record_protocol import atomic_json, read_json, telegram_send_allowed

MAX_UPLOAD_BYTES = 49_000_000
# Live OCR rejects samples older than eight seconds. Leave one queue polling
# interval as well so a later result cannot overtake an earlier inference.
ORDER_SETTLE_SECONDS = 10.0
TAIPEI = timezone(timedelta(hours=8))
VEHICLE_TYPES = (("car", "汽車"), ("motorcycle", "機車"), ("bus", "巴士"), ("truck", "卡車"))


def event_time(job):
    """The original trigger time, including compatible pre-upgrade journals."""
    value = job.get("trigger_created_at", job.get("clip_started_at"))
    if value is None:
        value = float(job.get("created_at", 0)) - float(job.get("duration", 0))
    result = float(value)
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError("Invalid event timestamp")
    return result


def taipei_time(timestamp):
    return datetime.fromtimestamp(float(timestamp), TAIPEI).strftime("%Y/%m/%d %H:%M:%S")


def active_trigger_time(state):
    """Conservative compatibility boundary while an old recorder is active."""
    try:
        stamp = state.get("trigger_created_at")
        if stamp is None:
            stamp = float(state["updated_at"]) - float(state["elapsed_seconds"]) - 30
        stamp = float(stamp)
        return stamp if math.isfinite(stamp) else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def event_object_counts(event):
    """Old journals count people; new requests explicitly distinguish classes."""
    def count(value):
        try:
            result = int(value)
            return result if not isinstance(value, bool) and result >= 0 and result == float(value) else 0
        except (ValueError, TypeError, OverflowError):
            return 0
    source = event.get("object_counts")
    if not isinstance(source, dict):
        source = {"person": event.get("count", 1)}
    return {name: count(source.get(name, 0)) for name in ("person", *(name for name, _ in VEHICLE_TYPES))}


def event_line(event):
    """Plain text preserves the observed plate separately from its fuzzy match."""
    def compact(value):
        return " ".join(str(value).split())[:64]
    stamp = datetime.fromtimestamp(float(event["timestamp"]), timezone(timedelta(hours=8))).strftime("%m/%d %H:%M:%S")
    observed = compact(event.get("text") or event.get("raw_text") or "?")
    text = "%s 辨識：%s" % (stamp, observed)
    if event.get("matched"):
        text += "；名單：%s（差 %s 碼）" % (compact(event["matched"]), compact(event.get("distance", "?")))
    else:
        text += "；未符合指定名單"
    if event.get("source") == "recording_replay":
        text += "；原片補辨識"
        video = str(event.get("source_video") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if video:
            text += "：" + compact(video)
        try:
            offset = float(event["offset_seconds"])
            if math.isfinite(offset) and offset >= 0:
                text += "（影片 +%.1f 秒）" % offset
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    return text


class TelegramDeferred(Exception):
    """A quiet period or shutdown postponed delivery without a failed attempt."""


def load_credentials(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values.get("BOT_TOKEN", ""), values.get("CHAT_ID", "")


class TelegramVideoQueue:
    def __init__(self, root, log=print):
        self.root = Path(root)
        self.directory = self.root / "runtime" / "telegram_upload_queue"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.plate_directory = self.root / "runtime" / "plate_events"
        self.plate_delivery = self.root / "runtime" / "telegram_plate_delivery"
        self.plate_delivery.mkdir(parents=True, exist_ok=True)
        self.sent_in_process = set()
        self.sent_details_in_process = {}
        self.order_file = self.root / "runtime" / "telegram_delivery_order.json"

    def enqueue(self, path, request, duration, complete):
        path = Path(path).resolve()
        job = dict(path=str(path), prepared_path=str(path.with_name(path.stem + "_telegram.mp4")),
            request_id=request.get("request_id"), count=request.get("count", 1),
            roi=request.get("roi"), duration=duration, complete=complete,
            created_at=time.time(), attempts=0, retry_at=0)
        job["clip_started_at"] = request.get("clip_started_at", job["created_at"]-duration)
        job["trigger_created_at"] = request.get("created_at", job["clip_started_at"])
        if isinstance(request.get("object_counts"), dict):
            job["object_counts"] = event_object_counts(request)
        manifest = self.directory / (uuid.uuid4().hex + ".json")
        with self.lock:
            atomic_json(manifest, job)
        return manifest

    def protected_paths(self):
        result = set()
        with self.lock:
            for manifest in self.directory.glob("*.json"):
                job = read_json(manifest, {})
                for key in ("path", "prepared_path"):
                    if job.get(key):
                        result.add(Path(job[key]).resolve())
                if job.get("prepared_path"):
                    prepared = Path(job["prepared_path"])
                    result.add(prepared.with_name(prepared.stem + ".encoding.mp4").resolve())
        return result

    def start(self):
        self.thread = threading.Thread(target=self.run, name="TelegramVideoUploader", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            # Encoding checks the stop event every 100 ms and reaps its child.
            self.thread.join(timeout=6)

    def prepare(self, job):
        source = Path(job["path"])
        if 0 < source.stat().st_size <= MAX_UPLOAD_BYTES:
            return source
        destination = Path(job["prepared_path"])
        if destination.resolve() == source.resolve():
            raise ValueError("Delivery copy must not overwrite original")
        if destination.exists() and 0 < destination.stat().st_size <= MAX_UPLOAD_BYTES:
            return destination
        import imageio_ffmpeg
        temporary = destination.with_name(destination.stem + ".encoding.mp4")
        # Try full-resolution constant-quality video first. Only oversized
        # delivery copies use a duration-based bitrate budget; originals stay HQ.
        self.encode(job, temporary, ["-crf", "20"])
        if temporary.stat().st_size > MAX_UPLOAD_BYTES:
            duration = max(1.0, float(job["duration"]))
            bitrate = max(64, int(46_000_000 * 8 / duration / 1000))
            self.encode(job, temporary, ["-b:v", str(bitrate)+"k",
                "-maxrate", str(bitrate)+"k", "-bufsize", str(bitrate*2)+"k"])
        if not 0 < temporary.stat().st_size <= MAX_UPLOAD_BYTES:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Video exceeds delivery size limit")
        os.replace(temporary, destination)
        return destination

    def encode(self, job, temporary, quality):
        import imageio_ffmpeg
        args = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", job["path"], "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "2", *quality, "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(temporary)]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 300
        try:
            while process.poll() is None:
                if self.stop_event.wait(0.1):
                    raise InterruptedError("Video preparation cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Video preparation timeout")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if process.returncode or not temporary.exists() or temporary.stat().st_size <= 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Video preparation failed")

    def cleanup_delivery_copy(self, job):
        """Remove only this job's conventional derivative, never its original."""
        source = Path(job["path"]).resolve()
        expected = source.with_name(source.stem + "_telegram.mp4")
        prepared = Path(job.get("prepared_path", expected)).resolve()
        if prepared != expected or prepared == source:
            return True
        cleaned = True
        for path in (prepared, prepared.with_name(prepared.stem + ".encoding.mp4")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleaned = False
                self.log("TELEGRAM_TEMP_CLEANUP=FAILED FILE=%s" % path.name)
        return cleaned

    def plate_events(self):
        result = []
        for manifest in self.plate_directory.glob("*.json"):
            event = read_json(manifest, {})
            try:
                if event.get("confirmed") and event.get("id") and float(event["timestamp"]) > 0:
                    event_line(event)  # Reject malformed timestamps without blocking other events.
                    result.append(event)
            except (ValueError, TypeError, KeyError, OSError, OverflowError):
                continue
        return sorted(result, key=lambda item: float(item["timestamp"]))

    def caption_content(self, job):
        """Return precisely the observations included in Telegram's caption."""
        scope = "指定區域" if job.get("roi") else "全畫面"
        counts = event_object_counts(job)
        vehicles = sum(counts[name] for name, _ in VEHICLE_TYPES)
        kind = "人物與車輛" if counts["person"] and vehicles else "車輛" if vehicles else "人物" if counts["person"] else "物件"
        triggered = event_time(job)
        start = float(job.get("clip_started_at", job.get("created_at", 0)-float(job["duration"])))
        end = start+float(job["duration"])
        text = ("SuperLive AI：%s移動觸發錄影\n觸發時類型：%s\n錄影：%.1f 秒\n人物：%d；車輛：%d" %
                (scope, kind, job["duration"], counts["person"], vehicles))
        text += "\n觸發時間（台灣）：" + taipei_time(triggered)
        text += "\n片段：%s ～ %s" % (taipei_time(start), taipei_time(end))
        if vehicles:
            text += "\n車種：" + "、".join("%s %d" % (label, counts[name]) for name, label in VEHICLE_TYPES if counts[name])
        if not job.get("complete"):
            text += "\n錄影因停止或擷取中斷而提前結束"
        boundary = self.caption_boundary(job)
        # Capture setup may lag the trigger. Its pre-first-frame observations
        # still belong to this event; never discard them behind the watermark.
        events = [event for event in self.plate_events()
                  if triggered <= float(event["timestamp"]) <= end
                  and float(event["timestamp"]) < boundary]
        if events:
            text += "\n車牌紀錄（台灣時間）："
        elif vehicles:
            text += "\n車牌未確認"
        included = []
        for index, event in enumerate(events):
            line = "\n"+event_line(event)
            # Reserve the latest-observation footer and overflow explanation.
            # Stop at the first omitted event: a later, shorter result must not
            # jump ahead of the pending standalone notifications.
            footer = self.latest_observation_line(triggered if vehicles else None, event)
            if len(text)+len(line)+len(footer) > 930:
                text += "\n另有 %d 筆，詳見車牌通知。" % (len(events)-index)
                break
            text += line
            included.append(event)
        text += self.latest_observation_line(triggered if vehicles else None, included[-1] if included else None)
        return dict(text=text[:1024], plates=[dict(id=event["id"], timestamp=float(event["timestamp"]))
                    for event in included], through=max([triggered]+[float(event["timestamp"]) for event in included]))

    def caption_boundary(self, current):
        """Do not put a later clip's observations into an earlier message."""
        triggered = event_time(current)
        boundary = float("inf")
        for manifest in self.directory.glob("*.json"):
            other = read_json(manifest, {})
            if not other or other.get("sent_at"):
                continue
            if (other.get("request_id") == current.get("request_id")
                    and other.get("path") == current.get("path")):
                continue
            stamp = event_time(other)
            if stamp >= triggered:
                boundary = min(boundary, stamp)
        state = read_json(self.root / "runtime/recording_state.json", {})
        if (state.get("status") in ("STARTING", "RECORDING")
                and state.get("request_id") != current.get("request_id")):
            stamp = active_trigger_time(state)
            if stamp is None:
                boundary = triggered
            elif stamp >= triggered:
                boundary = min(boundary, stamp)
        return boundary

    @staticmethod
    def latest_observation_line(vehicle_time=None, plate=None):
        if plate is not None and (vehicle_time is None or float(plate["timestamp"]) >= vehicle_time):
            observed = " ".join(str(plate.get("text") or plate.get("raw_text") or "?").split())[:64]
            return "\n最近車輛／車牌：%s；辨識：%s" % (taipei_time(plate["timestamp"]), observed)
        if vehicle_time is not None:
            return "\n最近偵測到車輛：%s；車牌未確認" % taipei_time(vehicle_time)
        return ""

    def caption(self, job):
        return self.caption_content(job)["text"]

    def send_plate(self, event, token, chat_id):
        import requests
        if self.stop_event.is_set() or not telegram_send_allowed(self.root):
            raise TelegramDeferred("Telegram dispatch is currently deferred")
        response = requests.post("https://api.telegram.org/bot%s/sendMessage" % token,
            data=dict(chat_id=chat_id, text="SuperLive AI 車牌／車輛紀錄（台灣時間）\n"+event_line(event)
                      +self.latest_observation_line(plate=event)), timeout=(15, 30))
        if response.status_code != 200 or not response.json().get("ok"):
            raise RuntimeError("Telegram plate delivery failure")

    def send(self, video, job, token, chat_id):
        import requests
        from telegram_upload import MultipartUploadBody
        content = self.caption_content(job)
        with Path(video).open("rb") as stream:
            # Encoding can take minutes. Re-read the shared guard at dispatch,
            # including a new clip or rollover that began during preparation.
            if self.stop_event.is_set() or not telegram_send_allowed(self.root):
                raise TelegramDeferred("Telegram dispatch is currently deferred")
            body = MultipartUploadBody(fields=dict(chat_id=chat_id, caption=content["text"],
                duration=int(round(job["duration"])), supports_streaming="true"),
                stream=stream, filename=Path(video).name, stop_event=self.stop_event)
            response = requests.post("https://api.telegram.org/bot%s/sendVideo" % token,
                data=body, headers={"Content-Type": body.content_type}, timeout=(15, 180))
        # Never log URLs, response bodies, or exception text (they can contain credentials).
        if response.status_code != 200:
            raise RuntimeError("Telegram HTTP failure")
        if not response.json().get("ok"):
            raise RuntimeError("Telegram API failure")
        return dict(notified_plates=content["plates"], notified_through=content["through"])

    def plate_receipt(self, identity):
        digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
        return self.plate_delivery / (digest+".json")

    def last_delivered_time(self):
        if not self.order_file.exists():
            # Do not infer a watermark from old-version receipts: its existing
            # out-of-order video backlog still needs to be delivered first.
            return float("-inf")
        state = read_json(self.order_file)
        value = float(state["event_timestamp"])
        if not math.isfinite(value):
            raise ValueError("Invalid delivery order state")
        return value

    def advance_delivery_time(self, stamp):
        if stamp > self.last_delivered_time():
            atomic_json(self.order_file, dict(event_timestamp=stamp))

    def finish_video(self, manifest, job):
        """Finish durable receipts before permitting the next network request."""
        # Old-version sent jobs can remain for cleanup. They must not establish
        # a new watermark ahead of that version's undelivered video backlog.
        if "notified_through" in job:
            self.advance_delivery_time(float(job["notified_through"]))
        for event in job.get("notified_plates", []):
            receipt = self.plate_receipt(event["id"])
            previous = read_json(receipt, {})
            if not previous.get("sent_at"):
                atomic_json(receipt, dict(event_id=event["id"], sent_at=job["sent_at"],
                    event_timestamp=event["timestamp"], delivered_via_video=manifest.stem))
        if self.cleanup_delivery_copy(job):
            with self.lock:
                manifest.unlink(missing_ok=True)

    def pending_items(self):
        items = []
        for manifest in self.directory.glob("*.json"):
            job = read_json(manifest)
            if not isinstance(job, dict):
                # Unknown video metadata must not silently let newer work pass.
                raise ValueError("Unreadable pending video")
            if not job.get("sent_at"):
                items.append((event_time(job), 0, "video:"+manifest.name, manifest, job, job))
        for event in self.plate_events():
            manifest = self.plate_receipt(event["id"])
            state = read_json(manifest, {})
            if state.get("sent_at") or state.get("local_only"):
                continue
            items.append((float(event["timestamp"]), 1, "plate:"+str(event["id"]), manifest, event, state))
        return sorted(items, key=lambda item: item[:3])

    def active_clip_blocks(self, stamp, kind, data, now):
        state = read_json(self.root / "runtime" / "recording_state.json", {})
        if state.get("status") != "RECORDING":
            return False
        # A completed clip may already be queued while the recorder's final
        # heartbeat still says RECORDING. Its presence is the completion signal.
        active_id = state.get("request_id")
        if kind == 0 and data.get("request_id") == active_id:
            return False
        for manifest in self.directory.glob("*.json"):
            job = read_json(manifest, {})
            if active_id and job.get("request_id") == active_id:
                return False
        start = active_trigger_time(state)
        return start is None or stamp >= start

    def process_plates_once(self, now):
        # Compatibility for callers: never run an independent plate-only pass
        # that could overtake a video waiting in the shared timeline.
        self.process_once(now)

    def process_once(self, now=None):
        fixed_now = now
        now = time.time() if now is None else now
        if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=now):
            return
        # New-version successful plate receipts carry their observation time.
        # Reconcile a crash/write failure between receipt and watermark writes.
        for manifest in self.plate_delivery.glob("*.json"):
            receipt = read_json(manifest, {})
            if receipt.get("sent_at") and "event_timestamp" in receipt:
                self.advance_delivery_time(float(receipt["event_timestamp"]))
        # A successful POST may have been followed by an interrupted receipt or
        # cleanup. Complete that bookkeeping first, without uploading it again.
        for manifest in self.directory.glob("*.json"):
            job = read_json(manifest, {})
            if job.get("sent_at"):
                self.finish_video(manifest, job)
        # Re-read before every dispatch. Encoding/upload can take minutes, during
        # which additional events or quiet reservations may arrive.
        for _ in range(100):
            now = time.time() if fixed_now is None else fixed_now
            if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=now):
                return
            items = self.pending_items()
            if not items:
                return
            stamp, kind, key, manifest, data, state = items[0]
            if self.active_clip_blocks(stamp, kind, data, now):
                return
            if state.get("retry_at", 0) > now:
                return  # The oldest item's backoff is a queue-wide barrier.
            if kind == 1 and stamp > now-ORDER_SETTLE_SECONDS:
                return
            try:
                watermark = self.last_delivered_time()
                if kind == 1 and stamp < watermark:
                    # Late OCR cannot be inserted above an already delivered
                    # message. Keep it in local history / its video's caption.
                    atomic_json(manifest, dict(event_id=data["id"], local_only=True,
                        reason="older_than_last_notification", event_timestamp=stamp,
                        retained_at=now))
                    self.log("TELEGRAM_PLATE=LOCAL_ONLY REASON=LATE_OBSERVATION")
                    continue
                if kind == 0 and stamp < watermark and key not in self.sent_in_process:
                    # This is outside the normal recorder protocol (for example
                    # a manually restored old journal). Never discard its video.
                    raise RuntimeError("Older video requires chronological recovery")
                if key not in self.sent_in_process:
                    token, chat_id = load_credentials(self.root / "config" / "telegram.env")
                    if not token or not chat_id:
                        raise RuntimeError("Telegram configuration missing")
                    if kind == 0:
                        video = self.prepare(data)
                        if self.stop_event.is_set():
                            return
                        # Preparation may expose an earlier pending event.
                        current = self.pending_items()
                        if not current or current[0][2] != key:
                            return
                        if self.active_clip_blocks(stamp, kind, data, now):
                            return
                        details = self.send(video, data, token, chat_id)
                        self.sent_details_in_process[key] = details if isinstance(details, dict) else {}
                    else:
                        self.send_plate(data, token, chat_id)
                    self.sent_in_process.add(key)
                if kind == 0:
                    data.update(self.sent_details_in_process.get(key, {}))
                    data.setdefault("notified_through", stamp)
                    data["sent_at"] = now
                    with self.lock:
                        atomic_json(manifest, data)
                    self.finish_video(manifest, data)
                    self.log("TELEGRAM_VIDEO=PASS FILE=%s" % Path(data["path"]).name)
                else:
                    state.update(event_id=data["id"], sent_at=now, event_timestamp=stamp)
                    atomic_json(manifest, state)
                    self.advance_delivery_time(stamp)
                    self.log("TELEGRAM_PLATE=PASS")
            except TelegramDeferred:
                return
            except Exception as error:
                if self.stop_event.is_set():
                    return
                attempts = int(state.get("attempts", 0))+1
                state.update(attempts=attempts, retry_at=now+min(300, 10*2**min(attempts, 5)))
                if kind == 1:
                    state["event_id"] = data["id"]
                with self.lock:
                    atomic_json(manifest, state)
                self.log("TELEGRAM_%s=RETRY TYPE=%s ATTEMPT=%d" %
                         ("VIDEO" if kind == 0 else "PLATE", type(error).__name__, attempts))
                return  # Failures never permit a newer notification to pass.

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception as error:
                self.log("TELEGRAM_QUEUE_ERROR=%s" % type(error).__name__)
            self.stop_event.wait(2)
