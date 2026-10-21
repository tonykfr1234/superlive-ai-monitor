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
VEHICLE_TYPES = (("car", "汽車"), ("motorcycle", "機車"), ("bus", "巴士"), ("truck", "卡車"))


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

    def enqueue(self, path, request, duration, complete):
        path = Path(path).resolve()
        job = dict(path=str(path), prepared_path=str(path.with_name(path.stem + "_telegram.mp4")),
            request_id=request.get("request_id"), count=request.get("count", 1),
            roi=request.get("roi"), duration=duration, complete=complete,
            created_at=time.time(), attempts=0, retry_at=0)
        job["clip_started_at"] = request.get("clip_started_at", job["created_at"]-duration)
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

    def caption(self, job):
        scope = "指定區域" if job.get("roi") else "全畫面"
        counts = event_object_counts(job)
        vehicles = sum(counts[name] for name, _ in VEHICLE_TYPES)
        kind = "人物與車輛" if counts["person"] and vehicles else "車輛" if vehicles else "人物" if counts["person"] else "物件"
        text = ("SuperLive AI：%s移動觸發錄影\n觸發時類型：%s\n錄影：%.1f 秒\n人物：%d；車輛：%d" %
                (scope, kind, job["duration"], counts["person"], vehicles))
        if vehicles:
            text += "\n車種：" + "、".join("%s %d" % (label, counts[name]) for name, label in VEHICLE_TYPES if counts[name])
        if not job.get("complete"):
            text += "\n錄影因停止或擷取中斷而提前結束"
        start = float(job.get("clip_started_at", job.get("created_at", 0)-float(job["duration"])))
        end = start+float(job["duration"])
        events = [event for event in self.plate_events() if start <= float(event["timestamp"]) <= end]
        if events:
            text += "\n車牌紀錄（台灣時間）："
        elif vehicles:
            text += "\n車牌未確認"
        for index, event in enumerate(events):
            line = "\n"+event_line(event)
            if len(text)+len(line) > 960:
                text += "\n另有 %d 筆，詳見車牌通知。" % (len(events)-index)
                break
            text += line
        return text[:1024]

    def send_plate(self, event, token, chat_id):
        import requests
        if self.stop_event.is_set() or not telegram_send_allowed(self.root):
            raise TelegramDeferred("Telegram dispatch is currently deferred")
        response = requests.post("https://api.telegram.org/bot%s/sendMessage" % token,
            data=dict(chat_id=chat_id, text="SuperLive AI 車牌／車輛紀錄（台灣時間）\n"+event_line(event)), timeout=(15, 30))
        if response.status_code != 200 or not response.json().get("ok"):
            raise RuntimeError("Telegram plate delivery failure")

    def process_plates_once(self, now):
        for event in self.plate_events():
            if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=now):
                return
            key = "plate:"+str(event["id"])
            digest = hashlib.sha256(str(event["id"]).encode("utf-8")).hexdigest()
            manifest = self.plate_delivery / (digest+".json")
            state = read_json(manifest, {})
            if state.get("sent_at") or state.get("retry_at", 0) > now:
                continue
            try:
                if key not in self.sent_in_process:
                    token, chat_id = load_credentials(self.root / "config" / "telegram.env")
                    if not token or not chat_id:
                        raise RuntimeError("Telegram configuration missing")
                    self.send_plate(event, token, chat_id)
                    self.sent_in_process.add(key)
                atomic_json(manifest, dict(event_id=event["id"], sent_at=now))
                self.log("TELEGRAM_PLATE=PASS")
            except TelegramDeferred:
                return
            except Exception as error:
                if self.stop_event.is_set():
                    return
                attempts = int(state.get("attempts", 0))+1
                atomic_json(manifest, dict(event_id=event["id"], attempts=attempts,
                    retry_at=now+min(300, 10*2**min(attempts, 5))))
                self.log("TELEGRAM_PLATE=RETRY TYPE=%s ATTEMPT=%d" % (type(error).__name__, attempts))

    def send(self, video, job, token, chat_id):
        import requests
        caption = self.caption(job)
        with Path(video).open("rb") as stream:
            # Encoding can take minutes. Re-read the shared guard at dispatch,
            # including a new clip or rollover that began during preparation.
            if self.stop_event.is_set() or not telegram_send_allowed(self.root):
                raise TelegramDeferred("Telegram dispatch is currently deferred")
            response = requests.post("https://api.telegram.org/bot%s/sendVideo" % token,
                data=dict(chat_id=chat_id, caption=caption, duration=int(round(job["duration"])), supports_streaming="true"),
                files={"video": (Path(video).name, stream, "video/mp4")}, timeout=(15, 180))
        # Never log URLs, response bodies, or exception text (they can contain credentials).
        if response.status_code != 200:
            raise RuntimeError("Telegram HTTP failure")
        if not response.json().get("ok"):
            raise RuntimeError("Telegram API failure")

    def process_once(self, now=None):
        now = time.time() if now is None else now
        self.process_plates_once(now)
        jobs = [(manifest, read_json(manifest, {})) for manifest in self.directory.glob("*.json")]
        jobs.sort(key=lambda item: item[1].get("created_at", 0))
        for manifest, job in jobs:
            if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=now):
                return
            if not job or job.get("retry_at", 0) > now:
                continue
            try:
                key = "video:"+manifest.name
                if not job.get("sent_at") and key not in self.sent_in_process:
                    token, chat_id = load_credentials(self.root / "config" / "telegram.env")
                    if not token or not chat_id:
                        raise RuntimeError("Telegram configuration missing")
                    video = self.prepare(job)
                    if self.stop_event.is_set():
                        return
                    self.send(video, job, token, chat_id)
                    self.sent_in_process.add(key)
                job["sent_at"] = job.get("sent_at") or now
                with self.lock:
                    atomic_json(manifest, job)
                if self.cleanup_delivery_copy(job):
                    with self.lock:
                        manifest.unlink(missing_ok=True)
                self.log("TELEGRAM_VIDEO=PASS FILE=%s" % Path(job["path"]).name)
            except TelegramDeferred:
                # The job, its retry deadline and its failure count are intact.
                # This is a global quiet window, so later jobs must also wait.
                return
            except Exception as error:
                if self.stop_event.is_set():
                    return
                job["attempts"] = int(job.get("attempts", 0)) + 1
                job["retry_at"] = time.time() + min(300, 10 * 2 ** min(job["attempts"], 5))
                with self.lock:
                    atomic_json(manifest, job)
                self.log("TELEGRAM_VIDEO=RETRY TYPE=%s ATTEMPT=%d" % (type(error).__name__, job["attempts"]))

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception as error:
                self.log("TELEGRAM_QUEUE_ERROR=%s" % type(error).__name__)
            self.stop_event.wait(2)
