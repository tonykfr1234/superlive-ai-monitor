"""Local Windows first-run settings. No credentials are written to the repository."""
import argparse
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests

from adb_discovery import discover
from plate_corrections import normalized
from record_protocol import atomic_json, read_json


def verify_telegram_token(token):
    """Read-only getMe with the same verified HTTPS transport as notifications.

    Never expose exception text: requests URLs contain the bot credential.
    """
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        return 'Bot Token 格式不正確。'
    try:
        response = requests.get('https://api.telegram.org/bot'+token+'/getMe', timeout=(5, 15))
        if response.status_code in (401, 404):
            return 'Telegram 拒絕此 Token，請確認是否已撤銷或輸入錯誤。'
        if response.status_code == 429:
            return 'Telegram 暫時限制驗證次數，請稍後重試；這不代表 Token 無效。'
        if response.status_code != 200:
            return 'Telegram 服務暫時無法完成驗證，請稍後重試。'
        result = response.json()
        if isinstance(result, dict) and result.get('ok') is True:
            return 'Bot Token 驗證成功。此檢查不傳送訊息，也不驗證 Chat ID。'
        return 'Telegram 回應未確認成功，請稍後重試。'
    except requests.exceptions.SSLError:
        return 'HTTPS 憑證驗證異常，尚無法判定 Token；請檢查系統時間與網路憑證。'
    except requests.exceptions.Timeout:
        return '連線逾時，尚無法判定 Token；請稍後重試。'
    except ValueError:
        return 'Telegram 回應格式異常，尚無法判定 Token；請稍後重試。'
    except requests.exceptions.RequestException:
        return '網路連線失敗，尚無法判定 Token；請檢查網路後重試。'


def read_telegram(root):
    try:
        lines = (Path(root)/'config/telegram.env').read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return {}
    return dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))


def parse_plates(text):
    values = re.split(r'[,，;；\s]+', text.strip())
    result = []
    for value in values:
        if not value:
            continue
        if not re.fullmatch(r'[A-Za-z0-9-]{4,12}', value) or not 4 <= len(normalized(value)) <= 10:
            raise ValueError('車牌請填寫 4～10 碼英文或數字，可包含連字號。')
        result.append(normalized(value))
    return list(dict.fromkeys(result))


def parse_aliases(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        pair = line.split('=')
        if len(pair) != 2:
            raise ValueError('校正格式：原辨識值=正確車牌，每行一筆。')
        left, right = (parse_plates(p) for p in pair)
        if len(left) != 1 or len(right) != 1 or left[0] == right[0]:
            raise ValueError('每筆校正需要一個原辨識值和不同的正確車牌。')
        if left[0] in result and result[left[0]] != right[0]:
            raise ValueError('同一原辨識值不能設定兩個正確車牌。')
        result[left[0]] = right[0]
    if any(target in result for target in result.values()):
        raise ValueError('請直接填寫最終正確車牌，不要建立連鎖校正。')
    return result


def recovery_settings(previous, enabled, device_name, channel, stale_seconds, retry_seconds):
    """Validate the exact recovery target before any settings are written."""
    result = dict(previous or {}, enabled=bool(enabled))
    if not enabled:
        return result
    name = str(device_name).strip()
    if not name:
        raise ValueError('啟用自動復原時，請填寫 SuperLive 裝置列表中的完整裝置名稱。')
    values = []
    for value, low, high, label in ((channel, 1, 64, '頻道'),
                                   (stale_seconds, 15, 3600, '無畫面等待秒數'),
                                   (retry_seconds, 10, 3600, '復原重試間隔')):
        if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)) or not low <= int(value) <= high:
            raise ValueError(f'{label}請填寫 {low}～{high} 的整數。')
        values.append(int(value))
    result.update(device_name=name, channel=values[0], stale_seconds=values[1], retry_seconds=values[2])
    from stream_health import FrameStallDetector
    FrameStallDetector(result['stale_seconds'], roi=result.get('frame_roi'))
    return result


def save_settings(root, capture, token, chat, plates, verified, aliases):
    root = Path(root)
    token, chat = token.strip(), chat.strip()
    if token or chat:
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'-?\d+', chat):
            raise ValueError('請完整填寫 Bot Token 與數字 Chat ID；不用 Telegram 時兩欄都留空。')
    allowlist, spellings, mapping = parse_plates(plates), parse_plates(verified), parse_aliases(aliases)
    if 'recovery' in capture:
        recovery = capture['recovery']
        if not isinstance(recovery, dict):
            raise ValueError('自動復原設定格式不正確。')
        capture = dict(capture, recovery=recovery_settings(recovery, recovery.get('enabled') is True,
            recovery.get('device_name', ''), recovery.get('channel', 1),
            recovery.get('stale_seconds', 60), recovery.get('retry_seconds', 30)))
    cfg = root/'config'
    cfg.mkdir(parents=True, exist_ok=True)
    # Validate all inputs before writing anything. Each file is replaced atomically.
    atomic_json(cfg/'capture_config.json', capture)
    atomic_json(cfg/'plate_allowlist.json', allowlist)
    atomic_json(cfg/'plate_corrections.json', {'verified': spellings, 'aliases': mapping})
    temporary = cfg/'telegram.env.tmp'
    temporary.write_text('BOT_TOKEN='+token+'\nCHAT_ID='+chat+'\n', encoding='utf-8')
    temporary.replace(cfg/'telegram.env')


class SetupWizard:
    def __init__(self, root, window=None, tab=None):
        self.root = Path(root)
        self.window = window or tk.Tk()
        self.window.title('SuperLive AI — 安裝與本機設定')
        self.window.geometry('800x690')
        self.window.minsize(720, 640)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.saved = False
        self.closed = False
        self.running = False
        self.online = []
        self.results = queue.Queue()
        self.capture = read_json(self.root/'config/capture_config.json', {})
        self.capture = self.capture if isinstance(self.capture, dict) else {}
        self.capture.setdefault('source', 'adb')
        self.capture.setdefault('ensure_highest', True)
        self.capture['auto_detect'] = True
        self.token = tk.StringVar()
        self.chat = tk.StringVar()
        self.telegram_status = tk.StringVar()
        old = read_telegram(root)
        self.token.set(old.get('BOT_TOKEN', '') if ':' in old.get('BOT_TOKEN', '') else '')
        self.chat.set(old.get('CHAT_ID', '') if re.fullmatch(r'-?\d+', old.get('CHAT_ID', '')) else '')
        self.adb = tk.StringVar(value=self.capture.get('adb_path', ''))
        self.serial = tk.StringVar(value=self.capture.get('serial', ''))
        self.status = tk.StringVar(value='請開啟模擬器與 SuperLive 即時畫面；正在偵測 ADB…')
        wrapper = ttk.Frame(self.window, padding=20)
        wrapper.pack(fill='both', expand=True)
        ttk.Label(wrapper, text='SuperLive AI', font=('Segoe UI', 25, 'bold')).pack(anchor='w')
        ttk.Label(wrapper, text='一次設定：自動連線、通知及車牌。資料只儲存在這台電腦。').pack(anchor='w', pady=(2, 16))
        tabs = ttk.Notebook(wrapper)
        tabs.pack(fill='both', expand=True)
        connection = ttk.Frame(tabs, padding=18)
        telegram = ttk.Frame(tabs, padding=18)
        plate = ttk.Frame(tabs, padding=18)
        recovery_tab = ttk.Frame(tabs, padding=18)
        tabs.add(connection, text='  1  模擬器連線  ')
        tabs.add(telegram, text='  2  Telegram  ')
        tabs.add(plate, text='  3  車牌與校正  ')
        tabs.add(recovery_tab, text='  4  無畫面自動復原  ')
        ttk.Button(wrapper, text='Telegram Bot Token / Chat ID 設定', command=lambda: tabs.select(telegram)).pack(anchor='w', pady=4)
        if tab in ('telegram', 'plates', 'recovery'):
            tabs.select({'telegram': telegram, 'plates': plate, 'recovery': recovery_tab}[tab])
        for tab in (connection, telegram, plate, recovery_tab):
            tab.columnconfigure(0, weight=1)
        recovery = self.capture.get('recovery') or {}
        self.recovery_enabled = tk.BooleanVar(value=recovery.get('enabled') is True)
        self.recovery_device = tk.StringVar(value=recovery.get('device_name', ''))
        self.recovery_channel = tk.StringVar(value=str(recovery.get('channel', 1)))
        self.recovery_stale = tk.StringVar(value=str(recovery.get('stale_seconds', 60)))
        self.recovery_retry = tk.StringVar(value=str(recovery.get('retry_seconds', 30)))
        ttk.Checkbutton(recovery_tab, text='無畫面或畫面停止更新時，自動重啟 SuperLive 並選回指定頻道', variable=self.recovery_enabled).grid(sticky='w', pady=(0, 14))
        for label, variable in [('SuperLive 裝置名稱（須與 App 裝置列表完全相同）', self.recovery_device),
                                ('頻道編號（1～64）', self.recovery_channel),
                                ('無畫面／停止更新等待秒數（預設 60）', self.recovery_stale),
                                ('復原重試間隔秒數（預設 30）', self.recovery_retry)]:
            ttk.Label(recovery_tab, text=label).grid(sticky='w', pady=(8, 2))
            ttk.Entry(recovery_tab, textvariable=variable).grid(sticky='ew')
        ttk.Label(recovery_tab, text='只重啟 SuperLive；確認裝置與頻道後，等待影像恢復更新。\n模擬器離線時持續重連；遇到登入畫面或目標不明時不會選其他攝影機。\n儲存後重新啟動監控生效。裝置名稱與頻道只存在本機。', wraplength=650).grid(sticky='w', pady=16)
        ttk.Label(connection, text='安裝並開啟 MuMu／逍遙，登入 SuperLive 並開啟攝影機。', wraplength=650).grid(sticky='w', pady=(0, 18))
        ttk.Label(connection, text='ADB 程式（自動尋找，也可手動選取）').grid(sticky='w')
        ttk.Entry(connection, textvariable=self.adb).grid(sticky='ew', pady=6)
        ttk.Button(connection, text='選取 adb.exe', command=self.browse).grid(sticky='w')
        ttk.Label(connection, text='連線裝置：找到多台時請選擇要監控的模擬器').grid(sticky='w', pady=(22, 4))
        self.devices = ttk.Combobox(connection, textvariable=self.serial, state='readonly')
        self.devices.grid(sticky='ew')
        ttk.Button(connection, text='重新偵測／換一台裝置', command=self.rescan).grid(sticky='w', pady=14)
        ttk.Label(connection, text='未找到裝置時每 15 秒重試。已選定的裝置會固定使用，斷線後自動重連。\n若模擬器關閉 ADB，請先在模擬器設定啟用 ADB，再重新偵測。', wraplength=650).grid(sticky='w', pady=10)
        ttk.Label(telegram, text='可選設定；兩欄留空即不啟用 Telegram。').grid(sticky='w', pady=(0, 16))
        ttk.Label(telegram, text='Bot Token（從 Telegram 的 BotFather 取得）').grid(sticky='w')
        ttk.Entry(telegram, textvariable=self.token, show='●').grid(sticky='ew', pady=8)
        ttk.Label(telegram, text='Chat ID（接收通知的使用者或群組數字 ID）').grid(sticky='w', pady=(16, 0))
        ttk.Entry(telegram, textvariable=self.chat).grid(sticky='ew', pady=8)
        self.verify_button = ttk.Button(telegram, text='驗證 Bot Token（不傳送測試訊息）', command=self.verify_token)
        self.verify_button.grid(sticky='w', pady=16)
        ttk.Label(telegram, textvariable=self.telegram_status, wraplength=650).grid(sticky='w', pady=4)
        ttk.Label(telegram, text='請先在 Telegram 開啟自己的 Bot 並按 Start。\n驗證按鈕僅確認 Bot Token；Chat ID 請核對接收對象。\n憑證不會寫入 GitHub 專案或顯示於安裝紀錄。', wraplength=650).grid(sticky='w')
        data = read_json(self.root/'config/plate_allowlist.json', [])
        data = data.get('plates', []) if isinstance(data, dict) else data
        corrections = read_json(self.root/'config/plate_corrections.json', {})
        self.plates = self.textbox(plate, '置頂警示車牌（每行一筆或逗號分隔；清空可取消）', '\n'.join(data or []), 3)
        self.verified = self.textbox(plate, '已確認的正確車牌（用於優先選擇實際 OCR 候選，不直接猜字）', '\n'.join(corrections.get('verified', [])), 3)
        self.aliases = self.textbox(plate, '人工確認的誤讀校正：原辨識值=正確車牌（每行一筆）', '\n'.join(k+'='+v for k, v in corrections.get('aliases', {}).items()), 3)
        ttk.Label(plate, text='只校正你確認過的完整誤讀值；相近車牌不會自動替換。原始 OCR 證據仍會保留。', wraplength=650).grid(sticky='w', pady=8)
        ttk.Label(wrapper, textvariable=self.status, wraplength=740).pack(anchor='w', pady=15)
        actions = ttk.Frame(wrapper)
        actions.pack(fill='x')
        ttk.Button(actions, text='儲存並啟動監控', command=lambda: self.save(True)).pack(side='right')
        ttk.Button(actions, text='只儲存設定', command=lambda: self.save(False)).pack(side='right', padx=10)
        ttk.Button(actions, text='取消', command=self.close).pack(side='left')
        self.window.after(100, self.scan)
        self.window.after(150, self.poll)

    def textbox(self, parent, label, initial, height):
        ttk.Label(parent, text=label).grid(sticky='w', pady=(8, 3))
        box = tk.Text(parent, height=height, font=('Consolas', 12), wrap='word')
        box.insert('1.0', initial)
        box.grid(sticky='ew')
        return box

    def browse(self):
        filename = filedialog.askopenfilename(title='選取模擬器的 adb.exe', filetypes=[('ADB', 'adb.exe')])
        if filename:
            self.adb.set(filename)
            self.rescan()

    def rescan(self):
        if self.running:
            return
        self.serial.set('')
        self.online = []
        self.scan()

    def scan(self):
        if self.closed or self.running:
            return
        self.running = True
        config = dict(self.capture, adb_path=self.adb.get(), serial=self.serial.get())
        self.status.set('正在偵測本機模擬器 ADB…')
        def work():
            try:
                self.results.put(('adb', discover(config)))
            except Exception:
                self.results.put(('error', '找不到 ADB，請開啟模擬器或選取 adb.exe；稍後會自動重試。'))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            kind, value = self.results.get_nowait()
            if kind == 'adb':
                self.running = False
                path, devices = value
                self.adb.set(path)
                self.online = devices
                self.devices['values'] = devices
                if len(devices) == 1:
                    self.serial.set(devices[0])
                elif self.serial.get() not in devices:
                    self.serial.set('')
                self.status.set('ADB 已連線，可儲存並啟動。' if len(devices) == 1 else
                                ('找到多個裝置，請選取一台。' if devices else '尚未連線，15 秒後重試。'))
                if not devices:
                    self.window.after(15000, self.scan)
            elif kind == 'error':
                self.running = False
                self.status.set(value)
                self.window.after(15000, self.scan)
            elif kind == 'telegram':
                self.verify_button.configure(state='normal')
                token, message = value
                self.telegram_status.set(message if token == self.token.get().strip() else 'Token 已變更，請重新驗證。')
            else:
                self.status.set(value)
        except queue.Empty:
            pass
        self.window.after(150, self.poll)

    def verify_token(self):
        token = self.token.get().strip()
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            self.telegram_status.set('Bot Token 格式不正確。')
            return
        self.verify_button.configure(state='disabled')
        self.telegram_status.set('正在向 Telegram 驗證…')
        def work():
            self.results.put(('telegram', (token, verify_telegram_token(token))))
        threading.Thread(target=work, daemon=True).start()

    def save(self, start):
        if start and (not self.serial.get() or self.serial.get() not in self.online):
            messagebox.showerror('尚未連線', '請先連線並選取模擬器；也可先只儲存設定。')
            return
        if len(self.online) > 1 and not self.serial.get():
            messagebox.showerror('請選取裝置', '找到多個裝置，請先選取要監控的一台。')
            return
        try:
            capture = dict(self.capture, source='adb', auto_detect=True,
                           adb_path=self.adb.get(), serial=self.serial.get())
            capture['recovery'] = recovery_settings(self.capture.get('recovery'), self.recovery_enabled.get(),
                self.recovery_device.get(), self.recovery_channel.get(), self.recovery_stale.get(), self.recovery_retry.get())
            save_settings(self.root, capture, self.token.get(), self.chat.get(),
                          self.plates.get('1.0', 'end'), self.verified.get('1.0', 'end'), self.aliases.get('1.0', 'end'))
            self.saved = True
            if start:
                launcher = self.root/'Start_AI_Monitor.bat'
                if not launcher.is_file():
                    raise ValueError('找不到啟動檔，請先完成一鍵安裝。')
                subprocess.Popen(['cmd.exe', '/c', str(launcher)], cwd=self.root,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.close()
            else:
                self.status.set('已儲存在本機。設定變更於下次啟動監控生效。')
        except ValueError as error:
            messagebox.showerror('請檢查設定', str(error))
        except OSError:
            messagebox.showerror('儲存失敗', '無法寫入本機設定，請確認安裝資料夾可寫入。')

    def close(self):
        self.closed = True
        self.window.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--tab', choices=['telegram', 'plates', 'recovery'])
    args = parser.parse_args()
    wizard = SetupWizard(args.root, tab=args.tab)
    wizard.window.mainloop()
    return 0 if wizard.saved else 2


if __name__ == '__main__':
    raise SystemExit(main())
