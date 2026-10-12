"""Independent event recorder; ends on confirmed inactivity or five-minute limit."""
import ctypes
import math
import os
import shutil
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from record_protocol import atomic_json, read_json, valid_end_request, MIN_RECORD_SECONDS, MAX_RECORD_SECONDS
from telegram_video import TelegramVideoQueue
from capture_region import capture_frame, CaptureRegionResolver
from hq_video import HighQualityWriter
from daily_report import DailyReports, TAIPEI

ROOT = Path.home() / "Downloads" / "SuperLive_AI"
EVENT_SECONDS = MAX_RECORD_SECONDS
REQUEST_MAX_AGE = 30.0


class QueuePersistenceError(RuntimeError):
    """Do not advertise IDLE if the completed clip could not be queued."""


class FrameTimeline:
    """Map elapsed time to fixed-FPS frame slots, including missed slots."""
    def __init__(self, fps, start, seconds=EVENT_SECONDS):
        self.fps, self.start, self.seconds = fps, start, seconds
        self.total = int(round(fps * seconds))
        self.written = 0

    def due(self, now):
        target = min(self.total, max(1, int((now - self.start) * self.fps) + 1))
        return max(0, target - self.written)

    def done(self, now):
        return now >= self.start + self.seconds


def valid_request(request, now):
    if not isinstance(request, dict) or not isinstance(request.get("request_id"), str):
        return False
    if not request["request_id"] or len(request["request_id"]) > 200:
        return False
    try:
        age = now - float(request["created_at"])
        return math.isfinite(age) and -5 <= age <= REQUEST_MAX_AGE
    except (KeyError, ValueError, TypeError):
        return False


def rotation_candidates(record_root, protected):
    protected = {Path(p).resolve() for p in protected}
    candidates = []
    for path in Path(record_root).rglob("SuperLive_*.mp4"):
        try:
            if path.resolve() not in protected:
                candidates.append((path.stat().st_mtime, path))
        except OSError:
            pass
    return [path for _, path in sorted(candidates)]


class Recorder:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.root / "logs" / "Stage5C"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.stop = self.runtime / "stop.flag"
        self.pid_file = self.runtime / "recorder.pid"
        self.request_file = self.runtime / "record_request.json"
        self.end_request_file = self.runtime / "record_end_request.json"
        self.quiet_file = self.runtime / "telegram_quiet.json"
        self.state_file = self.runtime / "recording_state.json"
        self.config = read_json(self.root / "config" / "recording_config.json", {})
        self.record_root = Path(self.config.get("record_root", "C:/SuperLive_Recordings"))
        self.record_root.mkdir(parents=True, exist_ok=True)
        self.fps = float(self.config.get("target_fps", 10))
        if not math.isfinite(self.fps) or not 1 <= self.fps <= 60:
            raise ValueError("target_fps must be between 1 and 60")
        self.rotate_start = float(self.config.get("rotation_start_used_percent", 80))
        self.rotate_stop = float(self.config.get("rotation_stop_used_percent", 78))
        if not 0 < self.rotate_stop < self.rotate_start < 100:
            raise ValueError("Invalid disk rotation thresholds")
        self.state = "STARTING"
        self.request_id = None
        self.next_request = None
        self.last_publish = 0
        self.capture_resolver = CaptureRegionResolver()
        self.uploader = TelegramVideoQueue(self.root, self.log)
        self.reports = DailyReports(self.root, self.record_root, self.log)

    def log(self, message):
        line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        print(line, flush=True)
        with (self.log_dir / "recorder.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def publish(self, status=None, **extra):
        if status is not None:
            self.state = status
        atomic_json(self.state_file, dict(status=self.state, pid=os.getpid(),
            updated_at=time.time(), request_id=self.request_id, **extra))
        self.last_publish = time.monotonic()

    def heartbeat(self, **extra):
        if time.monotonic() - self.last_publish >= 0.5:
            self.publish(**extra)

    def reserve_quiet(self, request_id):
        atomic_json(self.quiet_file, {
            "request_id": request_id, "quiet_until": time.time() + MIN_RECORD_SECONDS,
        })

    def accept_end(self, signal, request_id, elapsed, started=None):
        """Enforce clip timing locally even if a detector sends an early signal."""
        if (not math.isfinite(elapsed) or elapsed < MIN_RECORD_SECONDS
                or not valid_end_request(signal, request_id)):
            return False
        if elapsed >= EVENT_SECONDS:
            # A pre-cap movement can arrive during the final capture call.
            # Preserve its successor without extending the capped old video.
            stamp = signal.get("created_monotonic")
            if (signal["reason"] != "MOVEMENT" or started is None
                    or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                    or not math.isfinite(stamp)
                    or not MIN_RECORD_SECONDS <= stamp - started < EVENT_SECONDS):
                return False
        if signal["reason"] == "MOVEMENT":
            self.next_request = deepcopy(signal["next_request"])
            self.next_request["trigger_type"] = "MOVEMENT_ROLLOVER"
            self.reports.record_trigger(self.next_request)
            # Close the delivery window before the old clip is enqueued. The
            # next first frame renews this reservation from its actual start.
            self.reserve_quiet(self.next_request["request_id"])
            self.request_id = self.next_request["request_id"]
            self.publish("STARTING", elapsed_seconds=0)
        self.log("EVENT_RECORD_END=%s REQUEST=%s" % (signal["reason"], request_id))
        return True

    def disk_percent(self):
        usage = shutil.disk_usage(str(self.record_root))
        return (usage.total - usage.free) / usage.total * 100

    def rotate(self, current=None):
        if self.disk_percent() < self.rotate_start:
            return True
        protected = self.uploader.protected_paths()
        if current:
            protected.add(Path(current).resolve())
        for path in rotation_candidates(self.record_root, protected):
            if self.disk_percent() <= self.rotate_stop:
                break
            try:
                path.unlink()
                self.log("RECORD_DELETE=%s" % path)
            except OSError:
                self.log("RECORD_DELETE_FAIL=%s" % path.name)
        return self.disk_percent() < self.rotate_start

    def find_window(self):
        choices = []
        def callback(hwnd, _):
            try:
                if not self.win32gui.IsWindowVisible(hwnd):
                    return
                _, pid = self.win32process.GetWindowThreadProcessId(hwnd)
                if self.psutil.Process(pid).name().lower() != "mumunxdevice.exe":
                    return
                left, top, right, bottom = self.win32gui.GetWindowRect(hwnd)
                if right - left >= 200 and bottom - top >= 300:
                    choices.append(((right-left)*(bottom-top), hwnd))
            except Exception:
                pass
        self.win32gui.EnumWindows(callback, None)
        if not choices:
            raise RuntimeError("MuMu window unavailable")
        hwnd = max(choices)[1]
        if self.win32gui.IsIconic(hwnd):
            self.win32gui.ShowWindow(hwnd, 9)
        return hwnd

    def capture(self, sct, hwnd):
        frame, _ = capture_frame(sct, hwnd, self.win32gui, self.cv2, self.np,
                                 resolver=self.capture_resolver)
        return frame

    def record(self, request):
        """Queue each completed presence event, preserving interrupted clips too."""
        writer = sct = path = timeline = None
        complete = False
        self.next_request = None
        self.request_id = request["request_id"]
        try:
            self.reports.record_trigger(request)
            hwnd = self.find_window()
            sct = self.mss.MSS() if hasattr(self.mss, "MSS") else self.mss.mss()
            frame = self.capture(sct, hwnd)
            height, width = frame.shape[:2]
            size = (width // 2 * 2, height // 2 * 2)
            frame = self.cv2.resize(frame, size)
            now = datetime.fromtimestamp(request.get("created_at", time.time()), TAIPEI)
            day_dir = self.record_root / now.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            path = day_dir / ("SuperLive_%s_%s.mp4" % (now.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:8]))
            self.reports.record_trigger(request, path.name)
            writer = self.make_writer(path, size)
            if not writer.isOpened():
                raise RuntimeError("VideoWriter failed")
            started = time.monotonic()
            timeline = FrameTimeline(self.fps, started)
            self.reserve_quiet(request["request_id"])
            self.publish("RECORDING", file=str(path), duration_seconds=EVENT_SECONDS, elapsed_seconds=0)
            self.log("EVENT_RECORD_START=%s REQUEST=%s" % (path, self.request_id))
            failures = 0
            last_disk_check = started
            max_failures = max(1, int(self.config.get("capture_max_failures", 5)))
            disk_interval = max(10, float(self.config.get("disk_check_seconds", 60)))
            while not self.stop.exists():
                now_mono = time.monotonic()
                due = timeline.due(now_mono)
                if due:
                    # Duplicate missed slots rather than speeding up playback on slow PCs.
                    for _ in range(due):
                        writer.write(frame)
                    timeline.written += due
                self.heartbeat(file=str(path), elapsed_seconds=min(EVENT_SECONDS, now_mono-started))
                if timeline.done(now_mono):
                    self.accept_end(read_json(self.end_request_file), request.get("request_id"),
                                    now_mono-started, started=started)
                    complete = True
                    break
                if self.accept_end(read_json(self.end_request_file), request.get("request_id"), now_mono-started):
                    complete = True
                    # Retain the event-scoped file: unlinking could remove a
                    # newer replacement. Its ID cannot stop a later event.
                    break
                if now_mono - last_disk_check >= disk_interval:
                    last_disk_check = now_mono
                    if not self.rotate(path):
                        self.log("EVENT_RECORD_INTERRUPTED=DISK_PRESSURE")
                        break
                next_slot = started + timeline.written / self.fps
                time.sleep(max(0, min(0.05, next_slot-time.monotonic())))
                if time.monotonic() < next_slot:
                    continue
                try:
                    frame = self.cv2.resize(self.capture(sct, hwnd), size)
                    failures = 0
                except Exception as error:
                    failures += 1
                    self.log("CAPTURE_ERROR=%s COUNT=%d" % (type(error).__name__, failures))
                    if failures >= max_failures:
                        raise RuntimeError("Capture unavailable; partial clip preserved")
                    try:
                        sct.close()
                    except Exception:
                        pass
                    sct = self.mss.MSS() if hasattr(self.mss, "MSS") else self.mss.mss()
                    try:
                        hwnd = self.find_window()
                    except RuntimeError:
                        pass
            return complete
        finally:
            if writer is not None:
                writer.release()
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass
            if path and timeline and timeline.written and path.exists() and path.stat().st_size:
                try:
                    self.uploader.enqueue(path, request, timeline.written / self.fps, complete)
                except Exception as error:
                    raise QueuePersistenceError("Unable to persist video upload") from error
                self.log("EVENT_RECORD_QUEUED=%s COMPLETE=%s" % (path.name, complete))

    def make_writer(self, path, size):
        return HighQualityWriter(path, self.fps, size)

    def run(self):
        import cv2
        import mss
        import numpy as np
        import psutil
        import win32gui
        import win32process
        self.cv2, self.mss, self.np = cv2, mss, np
        self.psutil, self.win32gui, self.win32process = psutil, win32gui, win32process
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        try:
            old = int(self.pid_file.read_text().strip())
        except (OSError, ValueError):
            old = None
        if old and old != os.getpid() and psutil.pid_exists(old):
            self.log("RECORDER_ALREADY_RUNNING")
            return
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        self.publish("STARTING")
        self.uploader.start()
        self.reports.start()
        try:
            while not self.stop.exists():
                if not self.rotate():
                    self.publish("PAUSED", reason="DISK_PRESSURE_PENDING_UPLOADS_PRESERVED")
                    for _ in range(20):
                        if self.stop.exists():
                            break
                        self.heartbeat()
                        time.sleep(0.1)
                    continue
                consume_file = self.next_request is None
                if not consume_file:
                    request = self.next_request
                    self.next_request = None
                else:
                    self.request_id = None
                    self.publish("IDLE")
                    while not self.stop.exists() and not self.request_file.exists():
                        self.heartbeat()
                        time.sleep(0.1)
                    if self.stop.exists():
                        break
                    request = read_json(self.request_file, {})
                    if not valid_request(request, time.time()):
                        self.log("RECORD_REQUEST=EXPIRED_OR_INVALID")
                        self.request_file.unlink(missing_ok=True)
                        continue
                self.request_id = request["request_id"]
                self.reserve_quiet(self.request_id)
                self.publish("STARTING")
                if consume_file:
                    self.request_file.unlink(missing_ok=True)
                try:
                    self.record(request)
                except QueuePersistenceError:
                    raise
                except Exception as error:
                    self.log("EVENT_RECORD_ERROR=%s" % type(error).__name__)
                    self.publish("PAUSED", reason=type(error).__name__)
                    for _ in range(20):
                        if self.stop.exists():
                            break
                        self.heartbeat()
                        time.sleep(0.1)
        finally:
            self.reports.stop()
            self.uploader.stop()
            self.publish("STOPPED")
            self.pid_file.unlink(missing_ok=True)
            self.log("RECORDER_STOP")


if __name__ == "__main__":
    Recorder().run()
