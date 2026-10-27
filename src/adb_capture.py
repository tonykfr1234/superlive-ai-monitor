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
                return ET.fromstring(self.run('shell', 'cat', path))
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
        """Confirm the selected quality and dismiss its menu before capture.

        SuperLive can leave the quality popup open after selecting a row, and
        its controls appear asynchronously. Only observed controls in this app
        receive input; each attempt is bounded so the caller can reconnect.
        """
        def in_app(tree):
            return any(n.get('package') == 'com.tvt.superliveplus' for n in tree.iter('node'))

        def option(tree):
            return next((n for n in tree.iter('node')
                         if n.get('text') in ('最高', 'Highest')), None)

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

        def open_menu(root=None):
            root = self.ui() if root is None else root
            if not in_app(root):
                raise RuntimeError('Open SuperLive live view first')
            if popup(root):
                return root
            button = control(root)
            if button is None:
                live = next((n for n in root.iter('node')
                             if n.get('resource-id', '').endswith('/live_video')), None)
                if live is None:
                    raise RuntimeError('SuperLive live-view control unavailable')
                self.tap(live)
                root = wait_ui(lambda tree: control(tree) is not None)
                button = control(root) if in_app(root) else None
            if button is None:
                raise RuntimeError('Quality control unavailable')
            self.tap(button)
            return wait_ui(popup)

        def selected(root):
            if not in_app(root):
                return False
            if quality_selected(root):
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
                if not has_popup and not toolbar:
                    clean = True
                    break
                if has_popup and backs < 2 and attempt in (0, 2):
                    self.run('shell', 'input', 'keyevent', '4')
                    backs += 1
                time.sleep(1)
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


def quality_selected(root):
    for node in root.iter('node'):
        texts = {n.get('text') for n in node.iter('node')}
        if texts.intersection(('最高', 'Highest')) and not texts.intersection(('低', 'Low')):
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
            self.recovery = SuperLiveRecovery(self.client, recovery.get('device_name', ''),
                                             recovery.get('channel'), self.stop_event)
        self._recovery_pending = False
        self._last_recovery_at = None
        self._capture_failure_since = None
        self._awaiting_fresh_camera = False
        self._fresh_reference = None
        try:
            self.memory = shared_memory.SharedMemory(name=self.name, create=True, size=OFFSET+CAPACITY)
        except FileExistsError:
            self.memory = shared_memory.SharedMemory(name=self.name)
        self.memory.buf[:HEADER.size] = bytes(HEADER.size)

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
        self._last_recovery_at = time.monotonic()
        self.status = 'ADB_RECOVERING'
        # The navigator checks the configured device and channel before success.
        # Never log private target names or hierarchy text.
        if self.recovery.restore() is not True:
            raise RuntimeError('Configured camera recovery was not verified')
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
        if self.health.observe(frame):
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
            print('ADB_CAMERA_RECOVERED target_verified=yes frames_updating=yes', flush=True)
        self._capture_failure_since = None
        return True

    def run(self):
        quality_checked = not self.config.get('ensure_highest', True)
        if quality_checked:
            self.quality = '由使用者設定'
        while not self.stop_event.is_set():
            try:
                self.status = 'ADB_CONNECTING' if not self.retry_count else 'ADB_RECONNECTING'
                try:
                    self.client.select()
                except Exception:
                    quality_checked = not self.config.get('ensure_highest', True)
                    self.status = 'ADB_UNAVAILABLE'
                    raise
                if self.restore_camera_if_needed():
                    quality_checked = not self.config.get('ensure_highest', True)
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
                except CameraFrameStalled:
                    quality_checked = not self.config.get('ensure_highest', True)
                    self._recovery_pending = self.confirm_camera_stalled()
                    if not self._recovery_pending:
                        # Camera is live: replace just the stalled ADB pipeline.
                        self.health.reset()
                    raise
                except Exception:
                    # A quickly restarted emulator may already be online but
                    # have reset the camera quality. Only normal rollovers
                    # retain the previous confirmation.
                    quality_checked = not self.config.get('ensure_highest', True)
                    if not quality_checked:
                        self.quality = '連線中斷，將重新確認'
                    raise
            except Exception as error:
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
                self.status = 'ADB_RECONNECTING'
                self.stop_event.wait(0.5)

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
                '-probesize', '32768', '-analyzeduration', '0', '-f', 'h264', '-i', 'pipe:0',
                '-an', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
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
                                return
                        raise RuntimeError('ADB stream ended unexpectedly')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('ADB stream stalled; reconnecting')
                    continue
                if time.monotonic() - stamp > 3:
                    continue
                frame = np.frombuffer(data, np.uint8).reshape(height, width, 3)
                deadline = time.monotonic() + 8
                if not self.camera_frame_ready(frame):
                    continue
                self.publish(frame, self.sequence)
                self.sequence += 2
                self.retry_count = 0
                if not received:
                    print('ADB_STREAM_READY %dx%d quality=%s' % (width, height, self.quality), flush=True)
                received = True
                self.status = 'ADB_STREAMING'
        finally:
            finished.set()
            self.kill_children()
            if reader:
                reader.join(timeout=3)
                if not reader.is_alive():
                    decoder.stdout.close()
            # A disconnected source must never leave a frame looking current.
            if self.memory:
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
        self.kill_children()
        if self.thread:
            self.thread.join(timeout=20)
        if self.memory and not (self.thread and self.thread.is_alive()):
            self.memory.buf[:HEADER.size] = bytes(HEADER.size)
            self.memory.close()
            self.memory.unlink()
            self.memory = None
