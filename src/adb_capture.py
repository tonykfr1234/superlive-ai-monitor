"""Full-resolution Android capture shared by detector and recorder.

One ADB H.264 stream is decoded in the background. A seqlock protects a bounded
shared-memory frame; consumers reject stale frames and never capture the desktop
when ADB was explicitly selected.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import threading
import time
from multiprocessing import shared_memory
import xml.etree.ElementTree as ET

from record_protocol import atomic_json, read_json

HEADER = struct.Struct('<QdIII')
OFFSET = 64
CAPACITY = 3840 * 2160 * 3


def memory_name(root):
    return 'SuperLiveADB_' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def settings(root):
    return read_json(Path(root) / 'config' / 'capture_config.json', {})


class AdbClient:
    def __init__(self, config):
        self.executable = config.get('adb_path') or 'C:/Program Files/Microvirt/MEmu/adb.exe'
        self.serial = config.get('serial', '')

    def run(self, *args, timeout=15):
        command = [self.executable] + (['-s', self.serial] if self.serial else []) + list(args)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('ADB command failed')
        return result.stdout

    def select(self):
        devices = [line.split()[0] for line in self.run('devices').decode().splitlines()[1:]
                   if len(line.split()) >= 2 and line.split()[1] == 'device']
        if self.serial:
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

    def ui(self):
        self.run('shell', 'uiautomator', 'dump', '/sdcard/superlive_ai_ui.xml')
        return ET.fromstring(self.run('shell', 'cat', '/sdcard/superlive_ai_ui.xml'))

    def tap(self, node):
        values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
        if len(values) != 4:
            raise ValueError('UI control has no bounds')
        x1, y1, x2, y2 = values
        self.run('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))

    def highest_quality(self):
        """Select a verified SuperLive control before monitoring; no blind taps."""
        def control(tree):
            return next((n for n in tree.iter('node')
                         if n.get('resource-id', '').endswith(('iv_bottom_definition_land', 'iv_bottom_definition'))), None)
        def open_menu():
            root = self.ui()
            if not any(n.get('package') == 'com.tvt.superliveplus' for n in root.iter('node')):
                raise RuntimeError('Open SuperLive live view first')
            if any(n.get('text') in ('最高', 'Highest') for n in root.iter('node')):
                return root
            button = control(root)
            if button is None:
                live = next((n for n in root.iter('node') if n.get('resource-id', '').endswith('/live_video')), None)
                if live is None:
                    raise RuntimeError('SuperLive live-view control unavailable')
                self.tap(live)
                root = self.ui()
                button = control(root)
            if button is None:
                raise RuntimeError('Quality control unavailable')
            self.tap(button)
            return self.ui()
        verified = False
        clean = False
        try:
            root = open_menu()
            highest = next((n for n in root.iter('node') if n.get('text') in ('最高', 'Highest')), None)
            if highest is None:
                raise RuntimeError('Highest quality option unavailable')
            self.tap(highest)
            root = open_menu()
            verified = quality_selected(root)
            if not verified:
                highest = next((n for n in root.iter('node') if n.get('text') in ('最高', 'Highest')), None)
                if highest is not None:
                    verified = selected_cyan_text(self.screenshot(), list(map(int, re.findall(r'\d+', highest.get('bounds', '')))))
        finally:
            root = self.ui()
            if any(n.get('text') in ('最高', 'Highest', '低', 'Low') for n in root.iter('node')):
                self.run('shell', 'input', 'keyevent', '4')
            for _ in range(4):
                time.sleep(1)
                root = self.ui()
                if not any(n.get('resource-id', '').endswith('video_menu_layout_land')
                           or n.get('text') in ('最高', 'Highest', '低', 'Low') for n in root.iter('node')):
                    clean = True
                    break
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
        if any(n.get('text') in ('最高', 'Highest') for n in node.iter('node')):
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
        self.thread = None
        self.processes = []
        self.status = 'ADB_CONNECTING'
        self.quality = '未確認'
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

    def run(self):
        import imageio_ffmpeg
        import numpy as np
        sequence = 0
        try:
            self.client.select()
            self.status = 'ADB_QUALITY_CHECK'
            if self.config.get('ensure_highest', True):
                try:
                    self.quality = '最高（已確認）' if self.client.highest_quality() else '已選最高，待確認'
                except Exception:
                    self.quality = '未確認，請手動選最高'
            if self.config.get('ensure_highest', True) and self.quality != '最高（已確認）':
                self.status = 'ADB_QUALITY_UNCONFIRMED'
                return
            while not self.stop_event.is_set():
                try:
                    frame = self.client.screenshot()
                    height, width = frame.shape[:2]
                    if width*height*3 > CAPACITY:
                        raise RuntimeError('Capture exceeds 4K capacity')
                    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    adb = subprocess.Popen([self.client.executable, '-s', self.client.serial, 'exec-out',
                        'screenrecord', '--output-format=h264', '--bit-rate', '16000000',
                        '--size', '%dx%d' % (width, height), '--time-limit', '180', '-'],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
                    self.processes = [adb]
                    decoder = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                        '-probesize', '32768', '-analyzeduration', '0', '-f', 'h264', '-i', 'pipe:0',
                        '-an', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
                        stdin=adb.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
                    adb.stdout.close()
                    self.processes.append(decoder)
                    size = width*height*3
                    while not self.stop_event.is_set():
                        data = decoder.stdout.read(size)
                        if len(data) != size:
                            break
                        self.publish(np.frombuffer(data, np.uint8).reshape(height, width, 3), sequence)
                        sequence += 2
                        self.status = 'ADB_STREAMING'
                except Exception:
                    self.status = 'ADB_RECONNECTING'
                finally:
                    self.kill_children()
                self.stop_event.wait(0.5)
        except Exception:
            self.status = 'ADB_UNAVAILABLE'

    def kill_children(self):
        for process in self.processes:
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
        self.processes = []

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
