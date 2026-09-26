import os
import json
import time
import shutil
import ctypes
from pathlib import Path
from datetime import datetime

import cv2
import mss
import numpy as np
import psutil
import win32gui
import win32process

ROOT = Path.home() / "Downloads" / "SuperLive_AI"
CONFIG_FILE = ROOT / "config" / "recording_config.json"
RUNTIME = ROOT / "runtime"
LOG_DIR = ROOT / "logs" / "Stage5C"
STOP_FLAG = RUNTIME / "stop.flag"
RECORDER_PID = RUNTIME / "recorder.pid"
RECORDER_LOG = LOG_DIR / "recorder.log"

RUNTIME.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with RECORDER_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError("Recording config missing: %s" % CONFIG_FILE)
    with CONFIG_FILE.open("r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    required = [
        "record_root",
        "target_fps",
        "segment_minutes",
        "disk_check_seconds",
        "rotation_start_used_percent",
        "rotation_stop_used_percent",
        "capture_max_failures",
        "capture_retry_seconds",
    ]
    for key in required:
        if key not in cfg:
            raise RuntimeError("Missing config key: %s" % key)

    start = float(cfg["rotation_start_used_percent"])
    stop = float(cfg["rotation_stop_used_percent"])
    if not (0 < stop < start < 100):
        raise RuntimeError("Invalid rotation thresholds")

    return cfg


CFG = load_config()
RECORD_ROOT = Path(CFG["record_root"])
TARGET_FPS = float(CFG["target_fps"])
FRAME_INTERVAL = 1.0 / TARGET_FPS
SEGMENT_SECONDS = int(float(CFG["segment_minutes"]) * 60)
DISK_CHECK_SECONDS = max(10, int(CFG["disk_check_seconds"]))
ROTATE_START_PERCENT = float(CFG["rotation_start_used_percent"])
ROTATE_STOP_PERCENT = float(CFG["rotation_stop_used_percent"])
CAPTURE_MAX_FAILURES = int(CFG["capture_max_failures"])
CAPTURE_RETRY_SEC = float(CFG["capture_retry_seconds"])

RECORD_ROOT.mkdir(parents=True, exist_ok=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def pid_alive(pid):
    try:
        return bool(pid) and psutil.pid_exists(int(pid))
    except Exception:
        return False


if RECORDER_PID.exists():
    try:
        old_pid = int(RECORDER_PID.read_text(encoding="utf-8").strip())
    except Exception:
        old_pid = None
    if old_pid and old_pid != os.getpid() and pid_alive(old_pid):
        log("RECORDER_ALREADY_RUNNING PID=%s" % old_pid)
        raise SystemExit(2)

RECORDER_PID.write_text(str(os.getpid()), encoding="utf-8")


def disk_stats():
    usage = shutil.disk_usage(str(RECORD_ROOT))
    used = usage.total - usage.free
    pct = (used / usage.total * 100.0) if usage.total else 0.0
    return pct, usage.total, usage.free


def gb(v):
    return v / 1024 / 1024 / 1024


def cleanup_empty_dirs():
    try:
        dirs = sorted(
            [p for p in RECORD_ROOT.rglob("*") if p.is_dir()],
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for d in dirs:
            try:
                if d != RECORD_ROOT and not any(d.iterdir()):
                    d.rmdir()
            except Exception:
                pass
    except Exception:
        pass


def rotate_if_needed(current_file=None):
    used_percent, total, free = disk_stats()
    log("DISK_USAGE=%.2f%% FREE=%.2fGB TOTAL=%.2fGB" % (used_percent, gb(free), gb(total)))

    if used_percent < ROTATE_START_PERCENT:
        return used_percent

    log("RECORD_ROTATION=START threshold=%.1f%% target=%.1f%%" % (ROTATE_START_PERCENT, ROTATE_STOP_PERCENT))

    files = []
    for p in RECORD_ROOT.rglob("SuperLive_*.mp4"):
        try:
            if current_file and p.resolve() == Path(current_file).resolve():
                continue
            files.append(p)
        except Exception:
            continue

    files.sort(key=lambda p: p.stat().st_mtime)
    deleted = 0

    for p in files:
        used_percent, total, free = disk_stats()
        if used_percent <= ROTATE_STOP_PERCENT:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            deleted += 1
            log("RECORD_DELETE=%s SIZE=%.2fMB" % (str(p), size / 1024 / 1024))
        except Exception as e:
            log("RECORD_DELETE_FAIL=%s ERROR=%s" % (str(p), str(e)))

    cleanup_empty_dirs()
    used_percent, total, free = disk_stats()
    log("RECORD_ROTATION=END deleted=%d usage=%.2f%% free=%.2fGB" % (deleted, used_percent, gb(free)))
    return used_percent


def find_mumu():
    candidates = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            if proc.name().lower() != "mumunxdevice.exe":
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            if width >= 200 and height >= 300:
                candidates.append((hwnd, width * height))
        except Exception:
            pass

    win32gui.EnumWindows(callback, None)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1])[0]


def new_mss():
    try:
        return mss.MSS()
    except AttributeError:
        return mss.mss()


def open_writer(width, height):
    now = datetime.now()
    day_dir = RECORD_ROOT / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / ("SuperLive_%s.mp4" % now.strftime("%Y%m%d_%H%M%S"))
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), TARGET_FPS, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError("VideoWriter failed for %s" % path)
    log("SEGMENT_START=%s SIZE=%dx%d FPS=%.1f" % (str(path), width, height, TARGET_FPS))
    return writer, path, time.time(), (width, height)


log("RECORDER_START PID=%s" % os.getpid())
log("RECORD_ROOT=%s" % RECORD_ROOT)
log("ROTATION_POLICY=start_at_used_%.1f%% stop_at_used_%.1f%%" % (ROTATE_START_PERCENT, ROTATE_STOP_PERCENT))
log("SEGMENT_MINUTES=%.1f FPS=%.1f" % (SEGMENT_SECONDS / 60.0, TARGET_FPS))

writer = None
segment_path = None
segment_start = 0.0
active_size = None
sct = None
capture_failures = 0
hwnd = None
next_frame = time.perf_counter()
last_disk_check = 0.0

try:
    rotate_if_needed()

    while True:
        if STOP_FLAG.exists():
            log("RECORDER_STOP_REQUEST")
            break

        now_perf = time.perf_counter()
        if now_perf < next_frame:
            time.sleep(min(next_frame - now_perf, 0.01))
            continue

        if hwnd is None or not win32gui.IsWindow(hwnd):
            hwnd = find_mumu()
            if hwnd is None:
                log("WAITING_FOR_MUMU")
                time.sleep(5)
                next_frame = time.perf_counter() + FRAME_INTERVAL
                continue
            if win32gui.IsIconic(hwnd):
                try:
                    win32gui.ShowWindow(hwnd, 9)
                    time.sleep(1)
                except Exception:
                    pass
            log("MUMU_FOUND HWND=%s" % hwnd)

        try:
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            cr = win32gui.GetClientRect(hwnd)
            cw, ch = int(cr[2]), int(cr[3])
            if cw < 200 or ch < 300:
                raise RuntimeError("Invalid MuMu client size %dx%d" % (cw, ch))
        except Exception as e:
            log("WINDOW_ERROR=%s" % str(e))
            hwnd = None
            time.sleep(1)
            next_frame = time.perf_counter() + FRAME_INTERVAL
            continue

        need_segment = (
            writer is None
            or (time.time() - segment_start) >= SEGMENT_SECONDS
            or active_size != (cw, ch)
        )

        if need_segment:
            if writer is not None:
                writer.release()
                log("SEGMENT_CLOSE=%s" % segment_path)
                writer = None

            rotate_if_needed()
            writer, segment_path, segment_start, active_size = open_writer(cw, ch)

        if time.time() - last_disk_check >= DISK_CHECK_SECONDS:
            used_after = rotate_if_needed(current_file=segment_path)
            last_disk_check = time.time()

            if used_after >= ROTATE_START_PERCENT:
                if writer is not None:
                    writer.release()
                    log("SEGMENT_CLOSE_FOR_DISK_PRESSURE=%s" % segment_path)
                    writer = None

                rotate_if_needed(current_file=None)
                used_after, _, _ = disk_stats()

                if used_after >= ROTATE_START_PERCENT:
                    log("RECORDING_PAUSED_DISK_PRESSURE usage=%.2f%%" % used_after)
                    time.sleep(30)
                    next_frame = time.perf_counter() + FRAME_INTERVAL
                    continue

                writer, segment_path, segment_start, active_size = open_writer(cw, ch)

        if sct is None:
            sct = new_mss()

        try:
            shot = sct.grab({
                "left": int(cx),
                "top": int(cy),
                "width": int(cw),
                "height": int(ch),
            })
            frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
            if capture_failures:
                log("CAPTURE_RECOVERY=PASS previous_failures=%d" % capture_failures)
            capture_failures = 0
        except Exception as e:
            capture_failures += 1
            log("CAPTURE_ERROR=%d/%d type=%s message=%s" % (
                capture_failures,
                CAPTURE_MAX_FAILURES,
                type(e).__name__,
                str(e),
            ))
            try:
                if sct is not None:
                    sct.close()
            except Exception:
                pass
            sct = None
            hwnd = find_mumu()
            if capture_failures >= CAPTURE_MAX_FAILURES:
                raise RuntimeError("Recorder capture failed %d consecutive times" % capture_failures)
            time.sleep(CAPTURE_RETRY_SEC)
            next_frame = time.perf_counter() + FRAME_INTERVAL
            continue

        if writer is None:
            writer, segment_path, segment_start, active_size = open_writer(cw, ch)

        writer.write(frame)

        next_frame += FRAME_INTERVAL
        after = time.perf_counter()
        if next_frame < after - FRAME_INTERVAL:
            next_frame = after + FRAME_INTERVAL

finally:
    if writer is not None:
        try:
            writer.release()
            log("SEGMENT_CLOSE=%s" % segment_path)
        except Exception:
            pass
    try:
        if sct is not None:
            sct.close()
    except Exception:
        pass
    try:
        if RECORDER_PID.exists():
            RECORDER_PID.unlink()
    except Exception:
        pass
    log("RECORDER_STOP")
