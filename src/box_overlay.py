"""Detection boxes for the Telegram copy of a clip; the original recording stays untouched.

The monitor publishes each inference's boxes; the recorder logs them per clip; at
delivery time the clip is decoded, the boxes valid at each frame's time are drawn,
and the copy is encoded with one thread so live detection keeps most of the CPU.
"""
import bisect
import json
import os
import subprocess
import time
from pathlib import Path

from record_protocol import atomic_json, read_json

LABELS = {0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 16: "dog"}
COLORS = {0: (0, 255, 0), 16: (255, 0, 255)}  # BGR; vehicles use the default below
VEHICLE_COLOR = (255, 190, 0)
MAX_BOX_AGE = 3.0  # seconds a detection stays drawn; live inference can pause ~3 s under load
POLL_SECONDS = 0.25


def box_color(class_id):
    return COLORS.get(int(class_id), VEHICLE_COLOR)


def publish_detections(path, timestamp, width, height, objects):
    atomic_json(path, {"timestamp": float(timestamp), "size": [int(width), int(height)],
                       "objects": [{"box": [int(v) for v in item["box"]], "class_id": int(item["class_id"]),
                                    "confidence": round(float(item["confidence"]), 3)} for item in objects]})


class BoxLog:
    """Append the monitor's detections to one clip's log while it records."""

    def __init__(self, path, source, started_at):
        self.path, self.source = Path(path), Path(source)
        self.started_at = float(started_at)
        self.last_timestamp = None
        self.next_poll = 0.0

    def poll(self):
        now = time.monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + POLL_SECONDS
        entry = read_json(self.source, None)
        try:
            stamp = float(entry["timestamp"])
        except (TypeError, KeyError, ValueError):
            return
        if stamp == self.last_timestamp or stamp < self.started_at - MAX_BOX_AGE:
            return
        self.last_timestamp = stamp
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except OSError:
            pass  # boxes are a convenience; never interrupt recording for them


def prune_logs(folder, keep_seconds=7*24*3600, now=None):
    now = time.time() if now is None else now
    for path in Path(folder).glob("*.jsonl"):
        try:
            if now - path.stat().st_mtime > keep_seconds:
                path.unlink()
        except OSError:
            pass


def load_log(path):
    entries = []
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                    entries.append((float(entry["timestamp"]), entry))
                except (ValueError, KeyError, TypeError):
                    continue  # a line cut short by a crash
    except OSError:
        return []
    entries.sort(key=lambda item: item[0])
    return entries


def boxes_at(entries, moment, stamps=None):
    """The latest detection at or before `moment`, if it is recent enough."""
    stamps = stamps if stamps is not None else [stamp for stamp, _ in entries]
    index = bisect.bisect_right(stamps, moment) - 1
    if index < 0 or moment - stamps[index] > MAX_BOX_AGE:
        return None
    return entries[index][1]


def draw_boxes(frame, entry, cv2):
    """Draw one detection entry, scaled like fit_frame letterboxes the capture."""
    height, width = frame.shape[:2]
    try:
        source_width, source_height = map(float, entry["size"])
        scale = min(width / source_width, height / source_height)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return frame
    x_offset, y_offset = (width - source_width*scale) / 2, (height - source_height*scale) / 2
    thickness = max(2, round(width / 640))
    for item in entry.get("objects", []):
        try:
            x1, y1, x2, y2 = (round(value*scale + offset) for value, offset in
                              zip(item["box"], (x_offset, y_offset, x_offset, y_offset)))
            class_id = int(item["class_id"])
        except (KeyError, TypeError, ValueError):
            continue
        color = box_color(class_id)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        label = "%s %.2f" % (LABELS.get(class_id, "object"), float(item.get("confidence", 0)))
        scale_text = max(0.6, width / 1920)
        (text_width, text_height), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale_text, thickness)
        top = max(0, y1 - text_height - 8)
        cv2.rectangle(frame, (x1, top), (x1 + text_width + 8, top + text_height + 8), color, -1)
        cv2.putText(frame, label, (x1 + 4, top + text_height + 3), cv2.FONT_HERSHEY_SIMPLEX,
                    scale_text, (0, 0, 0), thickness)
    return frame


def render(source, destination, log_path, clip_started_at, fps, stop_event=None, timeout=900):
    """Write `destination`, a copy of `source` with the logged boxes drawn in."""
    import cv2
    import imageio_ffmpeg
    entries = load_log(log_path)
    stamps = [stamp for stamp, _ in entries]
    capture = cv2.VideoCapture(str(source))
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture.release()
    if width <= 0 or height <= 0:
        raise RuntimeError("Unreadable recording")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # Normal priority but one thread each: below-normal starves completely while
    # the emulator saturates the CPU, and more threads slow the live monitor.
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    destination = Path(destination)
    temporary = destination.with_name(destination.stem + ".encoding.mp4")
    decoder = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "1", "-i", str(source),
                                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=flags)
    encoder = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                                "-pix_fmt", "bgr24", "-s", "%dx%d" % (width, height), "-r", str(fps), "-i", "pipe:0",
                                "-an", "-c:v", "libx264", "-preset", "superfast", "-threads", "1", "-crf", "23",
                                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=flags)
    import numpy as np
    frame_bytes, index, deadline = width*height*3, 0, time.monotonic() + timeout
    try:
        while True:
            data = decoder.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                break
            if (stop_event is not None and stop_event.is_set()) or time.monotonic() >= deadline:
                raise InterruptedError("Box rendering cancelled")
            frame = np.frombuffer(data, np.uint8).reshape(height, width, 3).copy()
            entry = boxes_at(entries, float(clip_started_at) + index/float(fps), stamps)
            if entry is not None:
                draw_boxes(frame, entry, cv2)
            encoder.stdin.write(frame.tobytes())
            index += 1
        encoder.stdin.close()
        decoder.stdout.close()
        if encoder.wait(timeout=120) or decoder.wait(timeout=30) or index == 0:
            raise RuntimeError("Box rendering failed")
        os.replace(temporary, destination)
        return destination
    finally:
        for process in (decoder, encoder):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        temporary.unlink(missing_ok=True)
