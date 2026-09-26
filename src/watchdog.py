import os, time, json, subprocess
from pathlib import Path
from datetime import datetime, timedelta
import psutil

ROOT = Path.home()/"Downloads"/"SuperLive_AI"
PYTHON = ROOT/".venv"/"Scripts"/"python.exe"
MONITOR = ROOT/"scripts"/"stage4b_motion_monitor.py"
RUNTIME = ROOT/"runtime"
LOG_DIR = ROOT/"logs"/"Stage5"
EVENT_DIR = ROOT/"events"
WATCHDOG_PID = RUNTIME/"watchdog.pid"
MONITOR_PID = RUNTIME/"monitor.pid"
HEARTBEAT = RUNTIME/"heartbeat.json"
STOP_FLAG = RUNTIME/"stop.flag"
WATCHDOG_LOG = LOG_DIR/"watchdog.log"
MONITOR_LOG = LOG_DIR/"monitor_process.log"
RESTART_DELAY=10; HEARTBEAT_SEC=60; EVENT_RETENTION_DAYS=60; LOG_RETENTION_DAYS=30
RUNTIME.mkdir(parents=True,exist_ok=True); LOG_DIR.mkdir(parents=True,exist_ok=True)

def log(msg):
    line="[%s] %s"%(datetime.now().strftime("%Y-%m-%d %H:%M:%S"),msg)
    print(line,flush=True)
    with WATCHDOG_LOG.open("a",encoding="utf-8") as f:f.write(line+"\n")

def alive(pid):
    try:return bool(pid) and psutil.pid_exists(int(pid))
    except:return False

def readpid(p):
    try:return int(p.read_text(encoding="utf-8").strip())
    except:return None

def rm(p):
    try:
        if p.exists():p.unlink()
    except:pass

old=readpid(WATCHDOG_PID)
if old and old!=os.getpid() and alive(old):
    log("WATCHDOG_ALREADY_RUNNING PID=%s"%old); raise SystemExit(2)
WATCHDOG_PID.write_text(str(os.getpid()),encoding="utf-8"); rm(STOP_FLAG)

def heartbeat(pid=None,status="OK"):
    HEARTBEAT.write_text(json.dumps({"timestamp":datetime.now().isoformat(timespec="seconds"),"watchdog_pid":os.getpid(),"monitor_pid":pid,"status":status},indent=2),encoding="utf-8")

def cleanup():
    now=datetime.now(); ec=now-timedelta(days=EVENT_RETENTION_DAYS); lc=now-timedelta(days=LOG_RETENTION_DAYS)
    ev=lg=0
    if EVENT_DIR.exists():
        for p in EVENT_DIR.rglob("*.jpg"):
            try:
                if datetime.fromtimestamp(p.stat().st_mtime)<ec:p.unlink();ev+=1
            except:pass
    logs=ROOT/"logs"
    if logs.exists():
        for p in logs.rglob("*.log"):
            try:
                if p.resolve() in (WATCHDOG_LOG.resolve(),MONITOR_LOG.resolve()):continue
                if datetime.fromtimestamp(p.stat().st_mtime)<lc:p.unlink();lg+=1
            except:pass
    log("CLEANUP events=%d logs=%d"%(ev,lg))

mon=None; stream=None; last_hb=last_cl=0.0
try:
    cleanup(); log("WATCHDOG_START PID=%s"%os.getpid())
    while True:
        if STOP_FLAG.exists():log("STOP_FLAG_DETECTED");break
        if mon is None:
            stream=MONITOR_LOG.open("a",encoding="utf-8",buffering=1)
            mon=subprocess.Popen([str(PYTHON),str(MONITOR)],cwd=str(ROOT),stdout=stream,stderr=subprocess.STDOUT,creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0))
            MONITOR_PID.write_text(str(mon.pid),encoding="utf-8");log("MONITOR_START PID=%s"%mon.pid)
        code=mon.poll()
        if code is not None:
            log("MONITOR_EXIT code=%s"%code);rm(MONITOR_PID)
            if stream:
                try:stream.close()
                except:pass
                stream=None
            if STOP_FLAG.exists():break
            heartbeat(None,"RESTART_WAIT");log("AUTO_RESTART in %ss"%RESTART_DELAY)
            for _ in range(RESTART_DELAY):
                if STOP_FLAG.exists():break
                time.sleep(1)
            mon=None;continue
        now=time.time()
        if now-last_hb>=HEARTBEAT_SEC:
            heartbeat(mon.pid,"OK");log("MONITOR_HEALTH=OK PID=%s"%mon.pid);last_hb=now
        if now-last_cl>=86400:cleanup();last_cl=now
        time.sleep(1)
finally:
    if mon is not None and mon.poll() is None:
        try:mon.terminate();mon.wait(timeout=10)
        except:
            try:mon.kill()
            except:pass
    if stream:
        try:stream.close()
        except:pass
    rm(MONITOR_PID);rm(WATCHDOG_PID);heartbeat(None,"STOPPED");log("WATCHDOG_STOP")
