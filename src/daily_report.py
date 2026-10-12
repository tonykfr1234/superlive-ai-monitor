"""Durable 23:00 Taiwan-time trigger CSV reports, independent of video uploads."""
import csv
import hashlib
import io
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from record_protocol import atomic_json, read_json, telegram_send_allowed
from telegram_video import load_credentials

TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
HEADER = ["觸發時間（台灣時間）", "事件類型", "人數", "信心值", "事件ID", "影片檔名"]


class DailyReports:
    def __init__(self, root, record_root, log=print, clock=time.time):
        self.root, self.record_root = Path(root), Path(record_root)
        self.log, self.clock = log, clock
        self.directory = self.root / "runtime" / "daily_reports"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal = self.directory / "triggers"
        self.journal.mkdir(exist_ok=True)
        self.jobs = self.directory / "jobs"
        self.jobs.mkdir(exist_ok=True)
        self.state_file = self.directory / "schedule.json"
        if not self.state_file.exists():
            atomic_json(self.state_file, {"first_day": self.now().date().isoformat()})
        self.stop_event = threading.Event()
        self.thread = None

    def now(self):
        return datetime.fromtimestamp(self.clock(), TAIPEI)

    def record_trigger(self, request, video=""):
        event_id = request["request_id"]
        timestamp = float(request.get("created_at", self.clock()))
        date = datetime.fromtimestamp(timestamp, TAIPEI).date().isoformat()
        folder = self.journal / date
        folder.mkdir(exist_ok=True)
        path = folder / (hashlib.sha256(event_id.encode()).hexdigest() + ".json")
        previous = read_json(path, {})
        atomic_json(path, dict(timestamp=timestamp, request_id=event_id,
            event_type=request.get("trigger_type", "PERSON_ACTIVITY"),
            count=request.get("count", 1), confidence=request.get("confidence", ""),
            video=str(video) or previous.get("video", "")))

    def rows(self, day, cutoff):
        entries = {}
        # Import existing local evidence for today's pre-upgrade triggers too.
        legacy = self.root / "events" / "person_events.csv"
        if legacy.exists():
            with legacy.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    try:
                        stamp = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
                        if stamp.date() == day and stamp < cutoff:
                            event_id = row["request_id"]
                            entries[event_id] = dict(timestamp=stamp.timestamp(), request_id=event_id,
                                event_type=row.get("event_type", "PERSON_ACTIVITY"),
                                count=row.get("count", ""), confidence=row.get("confidence", ""), video="")
                    except (KeyError, ValueError, TypeError):
                        continue  # An incomplete concurrently appended last row is not a trigger.
        for path in (self.journal / day.isoformat()).glob("*.json"):
            entry = read_json(path)
            if not isinstance(entry, dict):
                raise ValueError("Unreadable durable trigger entry")
            if entry["timestamp"] < cutoff.timestamp():
                entries[entry["request_id"]] = entry
        rows = []
        for event in sorted(entries.values(), key=lambda e: (e["timestamp"], e["request_id"])):
            rows.append([datetime.fromtimestamp(event["timestamp"], TAIPEI).isoformat(timespec="seconds"),
                event["event_type"], event["count"], event["confidence"], event["request_id"],
                Path(event["video"]).name if event["video"] else ""])
        return rows

    def generate(self, day, phase):
        key = day.isoformat() + "_" + phase
        manifest = self.jobs / (key + ".json")
        existing = read_json(manifest)
        # Reconcile late writes for the just-closed day. Older immutable reports
        # need no rescans; sent snapshots are never repeatedly dispatched.
        recent_full = phase == "full" and day >= self.now().date() - timedelta(days=1)
        if existing and not recent_full:
            return
        cutoff = datetime.combine(day, datetime.min.time(), TAIPEI) + (
            timedelta(hours=23) if phase == "2300" else timedelta(days=1))
        rows = self.rows(day, cutoff)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(HEADER)
        for row in rows:
            # CSV values must not become formulas when opened in Excel.
            writer.writerow(["'"+str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
        content = output.getvalue().encode("utf-8-sig")
        digest = hashlib.sha256(content).hexdigest()
        if existing and existing.get("digest") == digest:
            return
        folder = self.record_root / day.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("SuperLive_Triggers_" + key + ".csv")
        temporary = path.with_suffix(".csv.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        previous_path = self.jobs / (day.isoformat() + "_2300.json")
        previous = read_json(previous_path, {}) if phase == "full" else {}
        same_sent = previous.get("sent") and previous.get("digest") == digest
        atomic_json(manifest, dict(day=day.isoformat(), phase=phase, path=str(path),
            count=len(rows), digest=digest, sent=bool(same_sent), attempts=0, retry_at=0))
        if phase == "full" and previous and not previous.get("sent"):
            previous["superseded"] = True
            atomic_json(previous_path, previous)
        self.log("DAILY_CSV_SAVED DATE=%s PHASE=%s COUNT=%d" % (day, phase, len(rows)))

    def schedule(self, now):
        state = read_json(self.state_file)
        first = datetime.fromisoformat(state["first_day"]).date()
        day = first
        while day <= now.date():
            if day < now.date():
                self.generate(day, "full")
            elif now.hour >= 23:
                self.generate(day, "2300")
            day += timedelta(days=1)

    def send(self, job):
        import requests
        token, chat = load_credentials(self.root / "config" / "telegram.env")
        if not token or not chat:
            raise RuntimeError("Telegram configuration missing")
        with Path(job["path"]).open("rb") as stream:
            if self.stop_event.is_set() or not telegram_send_allowed(self.root):
                return False
            scope = "00:00–23:00" if job["phase"] == "2300" else "全天完整版"
            response = requests.post("https://api.telegram.org/bot%s/sendDocument" % token,
                data={"chat_id": chat, "caption": "SuperLive AI 每日觸發總結\n%s %s（台灣時間）\n共 %d 次觸發" % (job["day"], scope, job["count"])},
                files={"document": (Path(job["path"]).name, stream, "text/csv")}, timeout=(10, 45))
        if response.status_code != 200 or not response.json().get("ok"):
            raise RuntimeError("Telegram report delivery failed")
        return True

    def tick(self):
        now = self.now()
        self.schedule(now)  # Save CSV at 23:00 even while Telegram is quiet.
        for path in sorted(self.jobs.glob("*.json"), reverse=True):
            job = read_json(path)
            if not isinstance(job, dict):
                raise ValueError("Unreadable report delivery state")
            if job.get("sent") or job.get("superseded") or job.get("retry_at", 0) > self.clock():
                continue
            if self.stop_event.is_set() or not telegram_send_allowed(self.root, now=self.clock()):
                return
            try:
                if not self.send(job):
                    return
                job["sent"] = True
                job["sent_at"] = self.clock()
                atomic_json(path, job)
                self.log("DAILY_CSV_SENT DATE=%s PHASE=%s" % (job["day"], job["phase"]))
            except Exception as error:
                job["attempts"] = int(job.get("attempts", 0)) + 1
                job["retry_at"] = self.clock() + min(300, 10 * 2**min(job["attempts"], 5))
                atomic_json(path, job)
                self.log("DAILY_CSV_RETRY TYPE=%s" % type(error).__name__)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as error:
                self.log("DAILY_CSV_ERROR TYPE=%s" % type(error).__name__)
            self.stop_event.wait(15)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="DailyTriggerReports", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
