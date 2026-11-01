"""Independent, durable Telegram silence reminder; never logs credentials."""
import threading
import time
from pathlib import Path

from record_protocol import atomic_json, read_json

INTERVAL = 3600
MESSAGE = ("SuperLive AI 提醒：已超過一小時沒有成功推播。\n"
           "請查看監視器是否正常、SuperLive 是否有即時影像，以及網路連線。\n"
           "也可能只是這段時間沒有觸發事件，並不代表已確認故障。")


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
        if 'started_at' not in self.state:
            self.state['started_at'] = clock()
            atomic_json(self.path, self.state)
        self.stop = threading.Event()

    def tick(self):
        now = self.clock()
        if self.stop.is_set() or (self.root / 'runtime/stop.flag').exists():
            return False
        config = read_json(self.root / 'config/telegram_idle_alert.json', {}) or {}
        if config.get('enabled', True) is not True:
            return False
        last = max(self.state['started_at'], self.state.get('last_alert_at', 0))
        for kind in ('plate', 'video', 'report', 'reminder'):
            receipt = read_json(self.root / 'runtime' / ('telegram_activity_' + kind + '.json'), {}) or {}
            last = max(last, float(receipt.get('sent_at', 0)))
        if now - last < INTERVAL or now < self.state.get('retry_at', 0):
            return False
        # Health reminders intentionally bypass event-video quiet periods.
        try:
            self.sender(self.root)
        except Exception as error:
            self.state['retry_at'] = now + 300
            atomic_json(self.path, self.state)
            self.log('TELEGRAM_SILENCE=RETRY TYPE=' + type(error).__name__)
            return False
        sent_at = self.clock()
        self.state.update(last_alert_at=sent_at, retry_at=0)
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
