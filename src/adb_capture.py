"""Full-resolution Android capture shared by detector and recorder.

One ADB H.264 stream is decoded in the background. A seqlock protects a bounded
shared-memory frame; consumers reject stale frames and never capture the desktop
when ADB was explicitly selected.
"""
import hashlib
import math
import os
from pathlib import Path
import queue
import re
import struct
import subprocess
import threading
import time
import uuid
from multiprocessing import shared_memory
import xml.etree.ElementTree as ET

from record_protocol import atomic_json, read_json
from stream_health import FrameStallDetector
from superlive_recovery import SuperLiveRecovery

HEADER = struct.Struct('<QdIII')
OFFSET = 64
CAPACITY = 3840 * 2160 * 3


class CameraAppStopped(RuntimeError):
    """The configured SuperLive process is no longer running."""


class CameraFrameStalled(RuntimeError):
    """The transport is alive but camera pixels have stopped changing."""


def memory_name(root):
    return 'SuperLiveADB_' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def settings(root):
    return read_json(Path(root) / 'config' / 'capture_config.json', {})


class AdbClient:
    def __init__(self, config):
        self.config = dict(config)
        self.executable = config.get('adb_path') or 'C:/Program Files/Microvirt/MEmu/adb.exe'
        self.serial = config.get('serial', '')

    def run(self, *args, timeout=15):
        if getattr(self, 'stop_event', None) is not None and self.stop_event.is_set():
            raise InterruptedError('ADB capture stopped')
        deadline = getattr(self, 'operation_deadline', None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Camera recovery timed out')
            timeout = min(timeout, remaining)
        command = [self.executable] + (['-s', self.serial] if self.serial else []) + list(args)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('ADB command failed')
        if getattr(self, 'stop_event', None) is not None and self.stop_event.is_set():
            raise InterruptedError('ADB capture stopped')
        return result.stdout

    def select(self):
        if self.config.get('auto_detect'):
            from adb_discovery import discover
            executable, devices = discover(dict(self.config, serial=self.serial))
            self.executable = executable
            if self.serial and self.serial not in devices:
                raise RuntimeError('Configured emulator is offline; retrying connection')
            if not self.serial:
                if len(devices) != 1:
                    raise RuntimeError('Open Settings to select one connected emulator')
                self.serial = devices[0]
            return
        def online():
            return [line.split()[0] for line in self.run('devices').decode().splitlines()[1:]
                    if len(line.split()) >= 2 and line.split()[1] == 'device']
        devices = online()
        if self.serial:
            # Connect only the configured TCP endpoint, never scan other devices.
            if self.serial not in devices and re.fullmatch(r'(?:[\w.-]+|\[[0-9a-fA-F:]+\]):\d{1,5}', self.serial):
                self.run('connect', self.serial, timeout=8)
                devices = online()  # adb connect can report failure with exit code 0.
            if self.serial not in devices:
                raise RuntimeError('Configured ADB device is not connected')
        elif len(devices) == 1:
            self.serial = devices[0]
        else:
            raise RuntimeError('Select exactly one ADB serial in capture_config.json')

    def screenshot(self):
        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(self.run('exec-out', 'screencap', '-p'), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError('ADB screenshot cannot be decoded')
        return frame

    def quality_screenshot(self):
        """Read the compositor output used by the recording pipeline.

        Some MEmu builds omit SuperLive popup layers from screencap even when
        uiautomator and the emulator show them. A short screenrecord includes
        those layers and can prove the highlighted quality selection.
        """
        provider = getattr(self, "quality_frame_provider", None)
        if callable(provider):
            return provider()
        import cv2
        import imageio_ffmpeg
        import numpy as np
        video = self.run('exec-out', 'screenrecord', '--output-format=h264',
                         '--time-limit', '1', '-', timeout=8)
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
            '-f', 'h264', '-i', 'pipe:0', '-frames:v', '1', '-threads', '1',
            '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'], input=video,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not result.stdout:
            raise RuntimeError('ADB quality frame cannot be decoded')
        frame = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError('ADB quality frame cannot be decoded')
        return frame

    def ui(self):
        # Some Android builds exit successfully without writing a hierarchy
        # (for example when the UI never becomes idle). Never reuse a prior dump.
        began = time.monotonic()
        try:
            return self._ui()
        finally:
            timings = getattr(self, 'ui_seconds', None)
            if isinstance(timings, list):
                timings.append(time.monotonic() - began)

    def _ui(self):
        for attempt in range(3):
            path = '/sdcard/superlive_ai_ui_' + uuid.uuid4().hex + '.xml'
            try:
                output = self.run('shell', 'uiautomator', 'dump', path)
                message = output.decode('utf-8', errors='replace')
                if not re.search(r'UI (?:hierchary|hierarchy) dumped to:\s*' + re.escape(path) + r'(?:\s|$)', message):
                    if attempt < 2:
                        time.sleep(0.25)
                        continue
                    raise RuntimeError('ADB UI hierarchy dump was not confirmed')
                tree = ET.fromstring(self.run('shell', 'cat', path))
                observer = getattr(self, 'ui_observer', None)
                if callable(observer):
                    observer(tree)
                return tree
            finally:
                try:
                    self.run('shell', 'rm', '-f', path)
                except Exception:
                    pass

    def tap(self, node):
        values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
        if len(values) != 4:
            raise ValueError('UI control has no bounds')
        x1, y1, x2, y2 = values
        self.run('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))

    def highest_quality(self):
        return self.set_quality("high")

    def set_quality(self, level):
        """Confirm the selected quality and dismiss its menu before capture.

        SuperLive can leave the quality popup open after selecting a row, and
        its controls appear asynchronously. Only observed controls in this app
        receive input; each attempt is bounded so the caller can reconnect.
        """
        if level not in ("high", "low"):
            raise ValueError("Unknown camera quality")
        labels = ("最高", "Highest") if level == "high" else ("低", "Low")

        def in_app(tree):
            return any(n.get('package') == 'com.tvt.superliveplus' for n in tree.iter('node'))

        def option(tree):
            return next((n for n in tree.iter('node')
                         if n.get('text') in labels), None)

        def popup(tree):
            return in_app(tree) and any(n.get('text') in ('最高', 'Highest', '低', 'Low')
                                       for n in tree.iter('node'))

        def control(tree):
            return next((n for n in tree.iter('node')
                         if n.get('resource-id', '').endswith(
                             ('iv_bottom_definition_land', 'iv_bottom_definition'))), None)

        def wait_ui(predicate):
            root = self.ui()
            for _ in range(3):
                if not in_app(root) or predicate(root):
                    return root
                time.sleep(0.4)
                root = self.ui()
            return root

        def surface(tree):
            # With the toolbar shown its overlay replaces live_video in the tree.
            return next((n for n in tree.iter('node')
                         if n.get('resource-id', '').endswith(('/live_video', '/video_menu_layout_land'))), None)

        def center(node):
            values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
            if len(values) != 4:
                raise ValueError('UI control has no bounds')
            return (values[0]+values[2])//2, (values[1]+values[3])//2

        def press(button, reveal):
            # A uiautomator dump takes about 1.5 s, longer than the toolbar
            # stays visible. Reveal and press in one shell call so both taps
            # land inside the same visible window.
            if reveal is None:
                self.tap(button)
                return
            (rx, ry), (bx, by) = center(reveal), center(button)
            self.run('shell', 'sh', '-c', "'input tap %d %d; sleep 0.3; input tap %d %d'" % (rx, ry, bx, by))

        def open_menu(root=None):
            root = self.ui() if root is None else root
            if not in_app(root):
                raise RuntimeError('Open SuperLive live view first')
            if popup(root):
                return root
            video = surface(root)
            if video is not None:
                self.quality_live_bounds = video.get('bounds')
            button = control(root)
            if button is None:
                button = getattr(self, 'quality_button', None)
            if button is None:
                # First use: reveal the toolbar once to learn the button position.
                if video is None:
                    raise RuntimeError('SuperLive live-view control unavailable')
                for _ in range(3):
                    # Under load a dump can outlast the toolbar; reveal again.
                    self.tap(video)
                    root = self.ui()
                    if not in_app(root) or popup(root):
                        return root
                    button = control(root)
                    if button is not None:
                        break
                    time.sleep(0.4)
                if button is None:
                    raise RuntimeError('Quality control unavailable')
            self.quality_button = button
            bounds = getattr(self, 'quality_live_bounds', None)
            reveal = video if video is not None else (ET.Element('node', bounds=bounds) if bounds else None)
            # A tap on the video toggles the toolbar, so alternate between
            # reveal+press and a direct press: whichever state the toolbar is
            # in, one of two consecutive attempts reaches the button.
            for attempt in range(4):
                press(button, reveal if attempt % 2 == 0 else None)
                opened = self.ui()
                if not in_app(opened) or popup(opened):
                    return opened
                current = control(opened)
                if current is not None:
                    self.quality_button = button = current
                time.sleep(0.2)
            raise RuntimeError('Quality menu did not open')

        def selected(root):
            if not in_app(root):
                return False
            if quality_selected(root, level):
                return True
            highest = option(root)
            if highest is None:
                return False
            bounds = list(map(int, re.findall(r'\d+', highest.get('bounds', ''))))
            return selected_cyan_text(self.quality_screenshot(), bounds)

        verified = False
        clean = False
        try:
            root = open_menu()
            highest = option(root)
            if highest is None or not in_app(root):
                raise RuntimeError('Highest quality option unavailable')
            # Avoid toggling a setting that is already selected. The popup's
            # cyan label is the selection indicator on current SuperLive builds.
            verified = selected(root)
            if not verified:
                self.tap(highest)
                time.sleep(0.4)
                root = self.ui()
                if not popup(root):
                    root = open_menu(root)
                for attempt in range(3):
                    verified = selected(root)
                    if verified or not in_app(root):
                        break
                    if attempt < 2:
                        time.sleep(0.4)
                        root = self.ui()
        finally:
            # Failure to confirm is retryable, but a quality popup must never
            # become part of a published detection/recording frame.
            backs = 0
            for attempt in range(5):
                try:
                    root = self.ui()
                except Exception:
                    break
                if not in_app(root):
                    break
                has_popup = popup(root)
                toolbar = any(n.get('resource-id', '').endswith('video_menu_layout_land')
                              for n in root.iter('node'))
                if not has_popup:
                    clean = True
                    break
                if has_popup and backs < 2 and attempt in (0, 2):
                    self.run('shell', 'input', 'keyevent', '4')
                    backs += 1
                time.sleep(1)
        if not (verified and clean):
            print('ADB_QUALITY_CHECK selected=%s menu_closed=%s' % (verified, clean), flush=True)
        return verified and clean


def selected_cyan_text(frame, bounds):
    if len(bounds) != 4:
        return False
    x1, y1, x2, y2 = bounds
    crop = frame[max(0,y1):min(frame.shape[0],y2),max(0,x1):min(frame.shape[1],x2)].astype('int16')
    if crop.size == 0:
        return False
    blue, green, red = crop[:,:,0], crop[:,:,1], crop[:,:,2]
    return int(((blue > red+35) & (green > red+35) & (green > 120)).sum()) >= 5


def quality_selected(root, level="high"):
    wanted = ("最高", "Highest") if level == "high" else ("低", "Low")
    other = ("低", "Low") if level == "high" else ("最高", "Highest")
    for node in root.iter('node'):
        texts = {n.get('text') for n in node.iter('node')}
        if texts.intersection(wanted) and not texts.intersection(other):
            if node.get('selected') == 'true' or node.get('checked') == 'true':
                return True
    return False


class SharedAdbReader:
    def __init__(self, root):
        self.name = memory_name(root)
        self.memory = None

    def capture(self):
        import numpy as np
        if self.memory is None:
            self.memory = shared_memory.SharedMemory(name=self.name)
        for _ in range(5):
            head = bytes(self.memory.buf[:HEADER.size])
            seq, stamp, width, height, size = HEADER.unpack(head)
            if seq % 2:
                continue
            if not math.isfinite(stamp) or not stamp or time.time() - stamp > 3 or stamp > time.time()+1:
                raise RuntimeError('ADB frame is stale; waiting for stream')
            if size != width * height * 3 or not 0 < size <= CAPACITY:
                raise RuntimeError('Invalid ADB frame dimensions')
            data = bytes(self.memory.buf[OFFSET:OFFSET+size])
            if head == bytes(self.memory.buf[:HEADER.size]):
                return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3).copy()
        raise RuntimeError('ADB frame changed while reading')

    def close(self):
        if self.memory:
            self.memory.close()
            self.memory = None


class AdbStream(SharedAdbReader):
    def __init__(self, root):
        super().__init__(root)
        self.root = Path(root)
        self.config = settings(root)
        self.client = AdbClient(self.config)
        self.stop_event = threading.Event()
        self.client.stop_event = self.stop_event
        self.thread = None
        self.processes = []
        self.process_lock = threading.Lock()
        self.sequence = 0
        self.retry_count = 0
        self.status = 'ADB_CONNECTING'
        self.quality = '未確認'
        self.adaptive_quality = self.config.get('quality_mode') == 'activity'
        self._quality_applied = None
        self._quality_retry_at = 0.0
        self._quality_ready_at = None
        self._quality_last_seen = 0.0
        # High quality is for reading plates: switch once per person/vehicle
        # event and give up after three tries instead of clicking repeatedly.
        # Idle CPU inference can pause 3-12 s, so hold high for a minute.
        self.quality_max_attempts = 3
        self.quality_hold_seconds = 60.0
        self._quality_target = None
        self._quality_attempts = 0
        self._quality_event = 0
        self._quality_thread = None
        self._quality_cancel = threading.Event()
        self._quality_busy = threading.Event()
        self._quality_started = self._busy_last_frame = 0.0
        self._busy_max_gap = 0.0
        self._quality_masks = []
        self._quality_mask_until = 0.0
        self._raw_frame = None
        self._raw_frame_at = 0.0
        self._activity_read_at = None
        self._activity_state = {}
        # The toolbar hides before a hierarchy dump completes, so keep the
        # learned button position across restarts instead of relearning it.
        self._button_file = self.root / 'runtime/superlive_quality_button.json'
        saved = read_json(self._button_file, {}) or {}
        if isinstance(saved, dict) and re.fullmatch(r'\[\d+,\d+\]\[\d+,\d+\]', str(saved.get('button', ''))):
            self._quality_button = ET.Element('node', {'bounds': saved['button']})
            self._quality_live_bounds = saved.get('surface')
        recovery = self.config.get('recovery') or {}
        if not isinstance(recovery, dict):
            raise ValueError('Invalid recovery configuration')
        self.recovery_enabled = recovery.get('enabled') is True
        self.health = None
        self.recovery = None
        self.recovery_retry_seconds = 30.0
        if self.recovery_enabled:
            self.health = FrameStallDetector(recovery.get('stale_seconds', 60),
                                             roi=recovery.get('frame_roi'))
            self.recovery_retry_seconds = float(recovery.get('retry_seconds', 30))
            if (not math.isfinite(self.recovery_retry_seconds)
                    or not 10 <= self.recovery_retry_seconds <= 3600):
                raise ValueError('Recovery retry interval must be 10 to 3600 seconds')
            restart_only = recovery.get('mode') == 'restart_only'
            self.recovery = SuperLiveRecovery(self.client, None if restart_only else recovery.get('device_name', ''),
                                             None if restart_only else recovery.get('channel'), self.stop_event)
        self._recovery_pending = False
        self._last_recovery_at = None
        self._capture_failure_since = None
        self._awaiting_fresh_camera = False
        self._fresh_reference = None
        self._app_checked_at = None
        try:
            self.memory = shared_memory.SharedMemory(name=self.name, create=True, size=OFFSET+CAPACITY)
        except FileExistsError:
            self.memory = shared_memory.SharedMemory(name=self.name)
        self.memory.buf[:HEADER.size] = bytes(HEADER.size)

    def capture(self):
        # Only the in-process AI reader excludes quality controls; recordings
        # and Telegram videos read the shared frame directly and stay full.
        return self.camera_without_controls(super().capture())

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='SuperLiveADB')
        self.thread.start()

    def publish(self, frame, sequence):
        height, width = frame.shape[:2]
        data = frame.tobytes()
        if len(data) > CAPACITY:
            raise RuntimeError('ADB source exceeds supported 4K frame capacity')
        self.memory.buf[:HEADER.size] = HEADER.pack(sequence+1, 0, width, height, len(data))
        self.memory.buf[OFFSET:OFFSET+len(data)] = data
        self.memory.buf[:HEADER.size] = HEADER.pack(sequence+2, time.time(), width, height, len(data))

    def confirm_camera_stalled(self):
        """Compare independent PNG captures, never PNG against H.264 noise."""
        self.status = 'ADB_VERIFYING_STALL'
        first = self.client.screenshot()
        for _ in range(2):
            if self.stop_event.wait(1.2):
                raise InterruptedError('ADB capture stopped')
            current = self.client.screenshot()
            if self.health.frames_changed(first, current):
                return False
        return True

    def restore_camera_if_needed(self):
        if not self.recovery_enabled:
            return False
        now = time.monotonic()
        timed_out = (self._capture_failure_since is not None
                     and now - self._capture_failure_since >= self.health.timeout_seconds)
        if not (self._recovery_pending or timed_out):
            return False
        self._recovery_pending = True
        self.memory.buf[:HEADER.size] = bytes(HEADER.size)
        if self._last_recovery_at is not None:
            delay = self.recovery_retry_seconds - (now - self._last_recovery_at)
            if delay > 0:
                self.status = 'ADB_RECOVERY_WAIT'
                if self.stop_event.wait(delay):
                    raise InterruptedError('ADB capture stopped')
        if self.stop_event.is_set():
            raise InterruptedError('ADB capture stopped')
        self._quality_cancel.set()
        if self._quality_thread:
            self._quality_thread.join(timeout=16)
            if self._quality_thread.is_alive():
                raise RuntimeError('Waiting for camera controls to stop before recovery')
        self._last_recovery_at = time.monotonic()
        self.status = 'ADB_RECOVERING'
        # The navigator checks the configured device and channel before success.
        # Never log private target names or hierarchy text.
        if self.recovery.restore() is not True:
            raise RuntimeError('Configured camera recovery was not verified')
        camera_roi = getattr(self.recovery, "camera_roi", None)
        if isinstance(camera_roi, tuple) and len(camera_roi) == 4:
            self.health.roi = camera_roi
        self.health.reset()
        self._fresh_reference = None
        self._awaiting_fresh_camera = True
        self._recovery_pending = False
        self._capture_failure_since = None
        return True

    def camera_frame_ready(self, frame):
        """Gate recovered images until real camera updates have been observed."""
        if not self.recovery_enabled:
            return True
        now = time.monotonic()
        # The quality worker's uiautomator dumps can delay other ADB shells by
        # seconds; it already fails if SuperLive leaves the foreground.
        if not self._quality_busy.is_set() and (self._app_checked_at is None or now - self._app_checked_at >= 5):
            self._app_checked_at = now
            try:
                process = self.client.run("shell", "sh", "-c", "'pidof com.tvt.superliveplus || true'", timeout=3)
            except subprocess.TimeoutExpired:
                # A slow reply is not a closed app; frames still arrive. Only a
                # persistently unresponsive device forces a reconnect.
                self._app_check_timeouts = getattr(self, '_app_check_timeouts', 0) + 1
                if self._app_check_timeouts >= 3:
                    raise
                process = None
            else:
                self._app_check_timeouts = 0
            if isinstance(process, (bytes, str)) and not process.strip():
                raise CameraAppStopped("SuperLive process stopped")
        if not self._quality_busy.is_set() and self.health.observe(frame):
            raise CameraFrameStalled('Camera content stopped updating')
        if self._awaiting_fresh_camera:
            self.status = 'ADB_VERIFYING_CAMERA'
            if self._fresh_reference is None:
                self._fresh_reference = frame.copy()
                return False
            if not self.health.frames_changed(self._fresh_reference, frame):
                return False
            self._awaiting_fresh_camera = False
            self._fresh_reference = None
            print('ADB_CAMERA_RECOVERED frames_updating=yes', flush=True)
        self._capture_failure_since = None
        return True

    def desired_quality(self):
        # Reading on every frame contends with the detector's atomic replace
        # of this file on Windows; twice a second is ample for a 15 s hold.
        if self._activity_read_at is None or time.monotonic() - self._activity_read_at >= 0.5:
            self._activity_read_at = time.monotonic()
            self._activity_state = read_json(self.root / 'runtime/camera_activity.json', {}) or {}
        state = self._activity_state if isinstance(self._activity_state, dict) else {}
        now = time.time()
        # A brief hold prevents switching menus on every missed detection.
        active = (state.get('active') is True and 0 <= now - state.get('timestamp', 0) < 15)
        if active:
            self._quality_last_seen = time.monotonic()
        return ('high' if self._quality_last_seen
                and time.monotonic() - self._quality_last_seen < self.quality_hold_seconds else 'low')

    def quality_change_due(self):
        return (self.adaptive_quality and not self._quality_busy.is_set() and self._quality_ready_at is not None
                and time.monotonic() >= max(self._quality_ready_at, self._quality_retry_at)
                and self.desired_quality() != self._quality_applied)

    def quality_frame(self):
        # screenrecord only emits on screen changes and briefly pauses around
        # menu animations and stream rollovers; wait for the next frame.
        deadline = time.monotonic() + 4
        while True:
            frame = self._raw_frame
            if frame is not None and time.monotonic() - self._raw_frame_at <= 2:
                return frame.copy()
            if time.monotonic() >= deadline or self._quality_cancel.is_set():
                raise RuntimeError('No fresh compositor frame for quality verification')
            time.sleep(0.1)

    def quality_ui(self, tree):
        boxes = []
        for node in tree.iter('node'):
            if node.get('package') != 'com.tvt.superliveplus':
                continue
            name = node.get('resource-id', '')
            if name.endswith('/live_video'):
                self._quality_live_bounds = node.get('bounds')
            if name.endswith(('iv_bottom_definition_land', 'iv_bottom_definition')):
                known = getattr(self, '_quality_button', None)
                self._quality_button = ET.fromstring(ET.tostring(node))
                if known is None or known.get('bounds') != node.get('bounds'):
                    try:
                        atomic_json(self._button_file, {'button': node.get('bounds'),
                                                        'surface': getattr(self, '_quality_live_bounds', None)})
                    except OSError:
                        pass
            if (node.get('text') in ('最高', 'Highest', '低', 'Low')
                    or name.endswith(('video_menu_operate_land', 'video_menu_title_land'))):
                values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
                if len(values) == 4:
                    boxes.append(values)
        self._quality_masks = boxes

    def camera_without_controls(self, frame):
        if not self._quality_busy.is_set() and time.monotonic() >= self._quality_mask_until:
            return frame
        # Exclude changing controls from inference, not from raw verification.
        clean = frame.copy()
        h, w = clean.shape[:2]
        clean[:int(h*.12)] = 51
        clean[int(h*.82):] = 51
        for left, top, right, bottom in self._quality_masks:
            clean[max(0, top-12):min(h, bottom+12), max(0, left-12):min(w, right+12)] = 51
        return clean

    def apply_activity_quality(self):
        if not self.adaptive_quality:
            return
        level = self.desired_quality()
        if level != self._quality_target:
            self._quality_target, self._quality_attempts = level, 0
            if level == 'high':
                self._quality_event += 1
        if not self.quality_change_due() or self._quality_attempts >= self.quality_max_attempts:
            return
        self._quality_attempts += 1
        self._quality_started = self._busy_last_frame = time.monotonic()
        self._busy_max_gap = 0.0
        self._quality_busy.set()
        self._quality_cancel.clear()
        self._quality_thread = threading.Thread(target=self._set_activity_quality,
            args=(level,), daemon=True, name='SuperLiveQuality')
        self._quality_thread.start()

    def _set_activity_quality(self, level):
        client = AdbClient(dict(self.config, adb_path=self.client.executable, serial=self.client.serial))
        client.stop_event = self._quality_cancel
        client.quality_frame_provider = self.quality_frame
        client.ui_observer = self.quality_ui
        client.quality_button = getattr(self, '_quality_button', None)
        client.quality_live_bounds = getattr(self, '_quality_live_bounds', None)
        client.operation_deadline = time.monotonic() + 60
        client.ui_seconds = []

        def timing():
            # Frame gaps show whether live detection kept receiving images.
            now = time.monotonic()
            gap = max(self._busy_max_gap, now - self._busy_last_frame)
            return ' event=%d attempt=%d/%d seconds=%.1f max_frame_gap=%.1f dumps=%d dump_seconds=%.1f' % (
                self._quality_event, self._quality_attempts, self.quality_max_attempts,
                now - self._quality_started, gap, len(client.ui_seconds), sum(client.ui_seconds))
        try:
            if not client.set_quality(level):
                raise RuntimeError('Quality selection not confirmed')
            self._quality_applied = level
            self.quality = '高（人車偵測）' if level == 'high' else '低（待機）'
            print('ADB_ACTIVITY_QUALITY=' + level + timing(), flush=True)
            self._quality_retry_at = time.monotonic() + 2
        except Exception as error:
            reason = type(error).__name__ + (' REASON=' + str(error) if isinstance(error, RuntimeError) else '')
            if self._quality_attempts >= self.quality_max_attempts:
                self.quality = '畫質切換失敗（本次事件不再重試）'
                print('ADB_ACTIVITY_QUALITY_GAVE_UP level=' + level + ' TYPE=' + reason + timing(), flush=True)
            else:
                self.quality = '自動畫質待重試'
                print('ADB_ACTIVITY_QUALITY_RETRY level=' + level + ' TYPE=' + reason + timing(), flush=True)
            self._quality_retry_at = time.monotonic() + 5
        finally:
            # The toolbar auto-hides within about a second of the last tap; a
            # longer mask would hide plates right after switching to high.
            self._quality_mask_until = time.monotonic() + (3 if self._quality_masks else 1)
            self._quality_busy.clear()

    def run(self):
        selected_source = False
        quality_checked = self.adaptive_quality or not self.config.get('ensure_highest', True)
        if quality_checked:
            self.quality = '自動（待機低／人車高）' if self.adaptive_quality else '由使用者設定'
        while not self.stop_event.is_set():
            try:
                self.status = 'ADB_CONNECTING' if not self.retry_count else 'ADB_RECONNECTING'
                try:
                    if not selected_source:
                        self.client.select()
                except Exception:
                    quality_checked = self.adaptive_quality or not self.config.get('ensure_highest', True)
                    self.status = 'ADB_UNAVAILABLE'
                    raise
                if self.recovery_enabled:
                    process = self.client.run("shell", "sh", "-c", "'pidof com.tvt.superliveplus || true'", timeout=3)
                    if isinstance(process, (bytes, str)) and not process.strip():
                        self._recovery_pending = True
                if self.restore_camera_if_needed():
                    self._quality_ready_at = None
                    quality_checked = self.adaptive_quality or not self.config.get('ensure_highest', True)
                if not quality_checked:
                    self.status = 'ADB_QUALITY_CHECK'
                    try:
                        quality_checked = self.client.highest_quality()
                        if not quality_checked:
                            raise RuntimeError('Highest quality not confirmed')
                        self.quality = '最高（已確認）'
                    except Exception:
                        self.quality = '未確認，將自動重試'
                        self.status = 'ADB_QUALITY_UNCONFIRMED'
                        raise
                try:
                    self.stream_once()
                except CameraAppStopped:
                    self._recovery_pending = True
                    quality_checked = self.adaptive_quality or not self.config.get("ensure_highest", True)
                    raise
                except CameraFrameStalled:
                    quality_checked = self.adaptive_quality or not self.config.get('ensure_highest', True)
                    self._recovery_pending = self.confirm_camera_stalled()
                    if not self._recovery_pending:
                        # Camera is live: replace just the stalled ADB pipeline.
                        self.health.reset()
                    raise
                except Exception:
                    # A quickly restarted emulator may already be online but
                    # have reset the camera quality. Only normal rollovers
                    # retain the previous confirmation.
                    quality_checked = self.adaptive_quality or not self.config.get('ensure_highest', True)
                    if not quality_checked:
                        self.quality = '連線中斷，將重新確認'
                    raise
            except Exception as error:
                selected_source = False
                if self.stop_event.is_set():
                    break
                if self.recovery_enabled and self._capture_failure_since is None:
                    self._capture_failure_since = time.monotonic()
                if self.status not in ('ADB_UNAVAILABLE', 'ADB_QUALITY_UNCONFIRMED',
                                        'ADB_RECOVERING', 'ADB_RECOVERY_WAIT', 'ADB_VERIFYING_STALL'):
                    self.status = 'ADB_RECONNECTING'
                self.retry_count += 1
                delay = min(30, 5 * 2 ** min(self.retry_count - 1, 3))
                detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                print('ADB_RETRY status=%s attempt=%d delay=%ds reason=%s' %
                      (self.status, self.retry_count, delay, detail), flush=True)
                self.stop_event.wait(delay)
            else:
                # screenrecord ends normally at 180 seconds. Keep the confirmed
                # quality and restart promptly without opening any overlay.
                selected_source = True
                self.status = 'ADB_RECONNECTING'
                self.stop_event.wait(0.05)

    def start_process(self, *args, **kwargs):
        with self.process_lock:
            if self.stop_event.is_set():
                raise InterruptedError('ADB capture stopped')
            process = subprocess.Popen(*args, **kwargs)
            self.processes.append(process)
            return process

    def stream_once(self):
        import imageio_ffmpeg
        import numpy as np
        reader = None
        finished = threading.Event()
        frames = queue.Queue(maxsize=1)
        started = time.monotonic()
        normal_rollover = False
        try:
            frame = self.client.screenshot()
            height, width = frame.shape[:2]
            size = width * height * 3
            if not 0 < size <= CAPACITY:
                raise RuntimeError('Capture exceeds 4K capacity')
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            adb = self.start_process([self.client.executable, '-s', self.client.serial, 'exec-out',
                'screenrecord', '--output-format=h264', '--bit-rate', '16000000',
                '--size', '%dx%d' % (width, height), '--time-limit', '180', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
            decoder = self.start_process([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                '-threads', '2', '-probesize', '32768', '-analyzeduration', '0', '-f', 'h264', '-i', 'pipe:0',
                '-an', '-vf', 'fps=10', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
                stdin=adb.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
            adb.stdout.close()

            def read_frames():
                try:
                    while not finished.is_set() and not self.stop_event.is_set():
                        data = bytearray()
                        while len(data) < size:
                            part = decoder.stdout.read(size - len(data))
                            if not part:
                                return
                            data.extend(part)
                        item = (time.monotonic(), data)
                        try:
                            frames.put_nowait(item)
                        except queue.Full:
                            try:
                                frames.get_nowait()
                            except queue.Empty:
                                pass
                            frames.put_nowait(item)
                except (OSError, ValueError):
                    pass
                finally:
                    finished.set()

            reader = threading.Thread(target=read_frames, daemon=True, name='SuperLiveADBReader')
            reader.start()
            deadline = time.monotonic() + 15
            received = False
            while not self.stop_event.is_set():
                try:
                    stamp, data = frames.get(timeout=0.25)
                except queue.Empty:
                    if finished.is_set():
                        if received and time.monotonic() - started >= 170:
                            # EOF may reach the reader just before the process
                            # exit becomes observable via poll().
                            if decoder.wait(timeout=2) == 0:
                                normal_rollover = True
                                return
                        raise RuntimeError('ADB stream ended unexpectedly')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('ADB stream stalled; reconnecting')
                    continue
                if time.monotonic() - stamp > 3:
                    continue
                frame = np.frombuffer(data, np.uint8).reshape(height, width, 3)
                deadline = time.monotonic() + 8
                self._raw_frame = frame
                self._raw_frame_at = time.monotonic()
                if self._quality_busy.is_set():
                    self._busy_max_gap = max(self._busy_max_gap, self._raw_frame_at - self._busy_last_frame)
                    self._busy_last_frame = self._raw_frame_at
                if not self.camera_frame_ready(frame):
                    continue
                # The recorder reads this shared frame: keep it full and unmasked.
                self.publish(frame, self.sequence)
                if self._quality_ready_at is None:
                    self._quality_ready_at = time.monotonic() + 10
                self.sequence += 2
                self.retry_count = 0
                if not received:
                    print('ADB_STREAM_READY %dx%d quality=%s' % (width, height, self.quality), flush=True)
                received = True
                self.status = 'ADB_STREAMING'
                self.apply_activity_quality()
        finally:
            finished.set()
            self.kill_children()
            if reader:
                reader.join(timeout=3)
                if not reader.is_alive():
                    decoder.stdout.close()
            # A disconnected source must never leave a frame looking current.
            if self.memory and not normal_rollover:
                self.memory.buf[:HEADER.size] = bytes(HEADER.size)

    def kill_children(self):
        with self.process_lock:
            processes, self.processes = self.processes, []
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass

    def stop(self):
        self.stop_event.set()
        self._quality_cancel.set()
        if self._quality_thread:
            self._quality_thread.join(timeout=16)
        self.kill_children()
        if self.thread:
            self.thread.join(timeout=20)
        if self.memory and not (self.thread and self.thread.is_alive()):
            self.memory.buf[:HEADER.size] = bytes(HEADER.size)
            self.memory.close()
            self.memory.unlink()
            self.memory = None
