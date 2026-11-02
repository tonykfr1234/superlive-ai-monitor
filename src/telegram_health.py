"""Durable daytime person/vehicle checks; never logs Telegram credentials."""
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from record_protocol import atomic_json, read_json

INTERVAL = 7200
DAY_START_HOUR = 6
QUIET_START_HOUR = 23
RETRY_SECONDS = 300
STATE_POLICY = "daytime_activity_v2"
TAIPEI = timezone(timedelta(hours=8))
MESSAGE = ("SuperLive AI 提醒：過去兩小時未偵測到人員或車輛。\n"
           "請查看監視器是否正常、SuperLive 是否有即時影像，以及網路連線。\n"
           "也可能只是這段時間沒有觸發事件，並不代表已確認故障。")


def activity_slot(now):
    """Return the current daytime two-hour slot, or None during quiet hours."""
    local = datetime.fromtimestamp(now, TAIPEI)
    if local.hour < DAY_START_HOUR or local.hour >= QUIET_START_HOUR:
        return None
    hour = DAY_START_HOUR + ((local.hour - DAY_START_HOUR) // 2) * 2
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


def recently_observed(root, now):
    """Only a finite timestamp inside the completed two-hour window counts."""
    observed = read_json(Path(root) / "runtime/person_vehicle_activity.json", {})
    try:
        last_seen = float(observed["last_seen_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(last_seen) and 0 <= now - last_seen < INTERVAL


def record_success(root, kind, now=None):
    """Separate files avoid lost updates between independent sending workers."""
    if kind not in ('plate', 'video', 'report', 'reminder'):
        raise ValueError('Unknown Telegram activity kind')
    try:
        atomic_json(Path(root) / 'runtime' / ('telegram_activity_' + kind + '.json'),
                    {'sent_at': time.time() if now is None else now})
    except OSError:
        # Never retry an already successful network delivery for a local write.
        print('TELEGRAM_ACTIVITY_WRITE_FAILED', flush=True)


def send_reminder(root):
    import requests
    from telegram_video import load_credentials
    token, chat = load_credentials(Path(root) / 'config/telegram.env')
    if not token or not chat:
        raise RuntimeError('Telegram configuration missing')
    response = requests.post('https://api.telegram.org/bot%s/sendMessage' % token,
                             data={'chat_id': chat, 'text': MESSAGE}, timeout=(5, 15))
    if response.status_code != 200 or response.json().get('ok') is not True:
        raise RuntimeError('Telegram reminder delivery failed')


class TelegramSilenceReminder:
    def __init__(self, root, clock=time.time, sender=send_reminder, log=print):
        self.root = Path(root)
        self.clock, self.sender, self.log = clock, sender, log
        self.path = self.root / 'runtime/telegram_silence.json'
        self.state = read_json(self.path, {}) or {}
        if self.state.get('policy') != STATE_POLICY:
            try:
                started_at = float(self.state['started_at'])
                if not math.isfinite(started_at):
                    started_at = clock()
            except (KeyError, TypeError, ValueError, OverflowError):
                started_at = clock()
            self.state = {'policy': STATE_POLICY, 'started_at': started_at}
            atomic_json(self.path, self.state)
        self.stop = threading.Event()

    def tick(self):
        now = self.clock()
        if self.stop.is_set() or (self.root / 'runtime/stop.flag').exists():
            return False
        config = read_json(self.root / 'config/telegram_idle_alert.json', {}) or {}
        if config.get('enabled', True) is not True:
            return False
        try:
            slot = activity_slot(now)
            checked_slot = float(self.state.get('checked_slot', 0))
            retry_at = float(self.state.get('retry_at', 0))
            started_at = float(self.state['started_at'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if slot is None:
            return False
        if checked_slot >= slot or now - started_at < INTERVAL or now < retry_at:
            return False
        if recently_observed(self.root, now):
            self.state.update(checked_slot=slot, retry_at=0)
            atomic_json(self.path, self.state)
            return False
        # These checks intentionally bypass recording upload quiet periods.
        try:
            self.sender(self.root)
        except Exception as error:
            self.state['retry_at'] = now + RETRY_SECONDS
            atomic_json(self.path, self.state)
            self.log('TELEGRAM_SILENCE=RETRY TYPE=' + type(error).__name__)
            return False
        sent_at = self.clock()
        self.state.update(last_alert_at=sent_at, checked_slot=slot, retry_at=0)
        atomic_json(self.path, self.state)
        record_success(self.root, 'reminder', sent_at)
        self.log('TELEGRAM_SILENCE=SENT')
        return True

    def run(self):
        while not self.stop.is_set() and not (self.root / 'runtime/stop.flag').exists():
            try:
                self.tick()
            except Exception as error:
                self.log('TELEGRAM_SILENCE=ERROR TYPE=' + type(error).__name__)
            self.stop.wait(30)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name='TelegramSilenceReminder').start()
