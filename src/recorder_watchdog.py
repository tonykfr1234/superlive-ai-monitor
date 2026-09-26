import os
import time
import subprocess
from pathlib import Path
from datetime import datetime

import psutil

ROOT = Path.home() / "Downloads" / "SuperLive_AI"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
RECORDER = ROOT / "scripts" / "stage5c_recorder.py"
RUNTIME = ROOT / "runtime"
LOG_DIR = ROOT / "logs" / "Stage5C"
STOP_FLAG = RUNTIME / "stop.flag"
PID_FILE = RUNTIME / "recorder_watchdog.pid"
LOG = LOG_DIR / "recorder_watchdog.log"
CHILD_LOG = LOG_DIR / "recorder_process.log"
RESTART_DELAY = 10

RUNTIME.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(text):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pid_alive(pid):
    try:
        return bool(pid) and psutil.pid_exists(int(pid))
    except Exception:
        return False


if PID_FILE.exists():
    try:
        old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        old_pid = None
    if old_pid and old_pid != os.getpid() and pid_alive(old_pid):
        log("RECORDER_WATCHDOG_ALREADY_RUNNING PID=%s" % old_pid)
        raise SystemExit(2)

PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
process = None
child_stream = None

try:
    log("RECORDER_WATCHDOG_START PID=%s" % os.getpid())

    while True:
        if STOP_FLAG.exists():
            log("STOP_FLAG_DETECTED")
            break

        if process is None:
            child_stream = CHILD_LOG.open("a", encoding="utf-8", buffering=1)
            process = subprocess.Popen(
                [str(PYTHON), str(RECORDER)],
                cwd=str(ROOT),
                stdout=child_stream,
                stderr=subprocess.STDOUT,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            log("RECORDER_PROCESS_START PID=%s" % process.pid)

        exit_code = process.poll()
        if exit_code is not None:
            log("RECORDER_EXIT code=%s" % exit_code)
            if child_stream is not None:
                try:
                    child_stream.close()
                except Exception:
                    pass
                child_stream = None

            if STOP_FLAG.exists():
                break

            log("RECORDER_RESTART in %ss" % RESTART_DELAY)
            for _ in range(RESTART_DELAY):
                if STOP_FLAG.exists():
                    break
                time.sleep(1)
            process = None
            continue

        time.sleep(1)

finally:
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    if child_stream is not None:
        try:
            child_stream.close()
        except Exception:
            pass

    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass

    log("RECORDER_WATCHDOG_STOP")
