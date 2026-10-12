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
from pathlib import Path

from record_protocol import atomic_json, read_json, telegram_send_allowed

MAX_UPLOAD_BYTES = 49_000_000


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

    def enqueue(self, path, request, duration, complete):
        path = Path(path).resolve()
        job = dict(path=str(path), prepared_path=str(path.with_name(path.stem + "_telegram.mp4")),
            request_id=request.get("request_id"), count=request.get("count", 1),
            roi=request.get("roi"), duration=duration, complete=complete,
            created_at=time.time(), attempts=0, retry_at=0)
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
        destination = Path(job["prepared_path"])
        if destination.exists() and 0 < destination.stat().st_size <= MAX_UPLOAD_BYTES:
            return destination
        import imageio_ffmpeg
        temporary = destination.with_name(destination.stem + ".encoding.mp4")
        # 900 kb/s upper rate keeps a 300-second video comfortably below 50 MB.
        args = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", job["path"], "-an", "-vf", "scale=min(1280\\,iw):-2",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", "800k",
            "-maxrate", "900k", "-bufsize", "1800k", "-pix_fmt", "yuv420p",
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
        if process.returncode or not temporary.exists() or not 0 < temporary.stat().st_size <= MAX_UPLOAD_BYTES:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Video preparation failed")
        os.replace(temporary, destination)
        return destination

    def send(self, video, job, token, chat_id):
        import requests
        scope = "指定區域" if job.get("roi") else "全畫面"
        caption = "SuperLive AI：%s偵測到人員\n錄影：%.1f 秒\n人數：%s" % (scope, job["duration"], job.get("count", 1))
        if not job.get("complete"):
            caption += "\n錄影因停止或擷取中斷而提前結束"
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
        jobs = [(manifest, read_json(manifest, {})) for manifest in self.directory.glob("*.json")]
        jobs.sort(key=lambda item: item[1].get("created_at", 0))
        for manifest, job in jobs:
            if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=now):
                return
            if not job or job.get("retry_at", 0) > now:
                continue
            try:
                token, chat_id = load_credentials(self.root / "config" / "telegram.env")
                if not token or not chat_id:
                    raise RuntimeError("Telegram configuration missing")
                video = self.prepare(job)
                if self.stop_event.is_set():
                    return
                self.send(video, job, token, chat_id)
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
