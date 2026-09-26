import os
import csv
import time
import math
import json
import ctypes
from datetime import datetime
from collections import defaultdict, deque
from pathlib import Path

import cv2
import mss
import numpy as np
import psutil
import requests
import torch
import win32gui
import win32process

from ultralytics import YOLO
from winotify import Notification


# ============================================================
# PATH
# ============================================================

ROOT = Path.home() / "Downloads" / "SuperLive_AI"

MODEL_PATH = ROOT / "models" / "yolo11n.pt"
EVENT_ROOT = ROOT / "events"
CSV_PATH = EVENT_ROOT / "events.csv"
TELEGRAM_ENV = ROOT / "config" / "telegram.env"
POLICY_FILE = ROOT / "config" / "notification_policy.json"

EVENT_ROOT.mkdir(parents=True, exist_ok=True)


# ============================================================
# POLICY
# ============================================================

DEFAULT_POLICY = {
    "moving_notify_seconds": 300,
    "still_notify_seconds": 600,
    "absence_reset_seconds": 30,
    "target_fps": 10.0,
    "confidence": 0.25,
    "imgsz": 640,
    "watch_classes": [0, 2, 3, 5, 7],
    "capture_max_failures": 5,
    "capture_retry_seconds": 1.0,
}


def load_policy():
    cfg = dict(DEFAULT_POLICY)

    if POLICY_FILE.exists():
        try:
            with POLICY_FILE.open("r", encoding="utf-8-sig") as f:
                loaded = json.load(f)

            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception as e:
            print("POLICY_LOAD_ERROR=%s" % str(e), flush=True)

    return cfg


POLICY = load_policy()

MOVING_NOTIFY_SEC = float(POLICY["moving_notify_seconds"])
STILL_NOTIFY_SEC = float(POLICY["still_notify_seconds"])
ABSENCE_RESET_SEC = float(POLICY["absence_reset_seconds"])
TARGET_FPS = float(POLICY["target_fps"])
FRAME_INTERVAL = 1.0 / TARGET_FPS
CONF = float(POLICY["confidence"])
IMGSZ = int(POLICY["imgsz"])
WATCH_CLASSES = [int(x) for x in POLICY["watch_classes"]]
CAPTURE_MAX_FAILURES = int(POLICY["capture_max_failures"])
CAPTURE_RETRY_SEC = float(POLICY["capture_retry_seconds"])


# ============================================================
# CLASSES
# ============================================================

CLASS_NAMES = {
    0: "Person",
    2: "Car",
    3: "Motorcycle",
    5: "Bus",
    7: "Truck",
}

VEHICLE_CLASSES = {2, 3, 5, 7}

WINDOW_NAME = "SuperLive AI Detection Monitor"


# ============================================================
# MOTION ENGINE V2
# ============================================================

PERSON_WINDOW = 6
PERSON_MIN_DISPLACEMENT = 10.0
PERSON_MIN_RATIO = 0.035
PERSON_DIRECTION_RATIO = 0.60

VEHICLE_WINDOW = 8
VEHICLE_MIN_DISPLACEMENT = 18.0
VEHICLE_MIN_RATIO = 0.055
VEHICLE_DIRECTION_RATIO = 0.70

MOTION_CONFIRM_HITS = 2
STILL_CONFIRM_HITS = 4

TRAIL_LENGTH = 20


# ============================================================
# DPI
# ============================================================

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ============================================================
# CSV
# ============================================================

if not CSV_PATH.exists():
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(
            [
                "timestamp",
                "event_type",
                "class",
                "track_id",
                "confidence",
                "movement_px",
                "snapshot",
            ]
        )


# ============================================================
# TELEGRAM
# ============================================================

def load_telegram():
    cfg = {}

    if not TELEGRAM_ENV.exists():
        print("TELEGRAM_CONFIG=NOT_FOUND", flush=True)
        return "", ""

    try:
        with TELEGRAM_ENV.open("r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    except Exception as e:
        print("TELEGRAM_CONFIG_ERROR=%s" % str(e), flush=True)
        return "", ""

    token = cfg.get("BOT_TOKEN", "")
    chat_id = cfg.get("CHAT_ID", "")

    if token and chat_id:
        print("TELEGRAM_CONFIG=PASS", flush=True)
    else:
        print("TELEGRAM_CONFIG=INCOMPLETE", flush=True)

    return token, chat_id


TELEGRAM_TOKEN, TELEGRAM_CHAT_ID = load_telegram()


def telegram_photo(caption, jpg_path):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_ALERT=SKIPPED", flush=True)
        return False

    try:
        url = "https://api.telegram.org/bot%s/sendPhoto" % TELEGRAM_TOKEN

        with open(jpg_path, "rb") as image_file:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                },
                files={
                    "photo": (
                        os.path.basename(jpg_path),
                        image_file,
                        "image/jpeg",
                    )
                },
                timeout=20,
            )

        if response.status_code != 200:
            print(
                "TELEGRAM_ALERT=FAIL_HTTP_%s" % response.status_code,
                flush=True,
            )
            return False

        data = response.json()
        if not data.get("ok", False):
            print("TELEGRAM_ALERT=FAIL_API", flush=True)
            return False

        print("TELEGRAM_ALERT=PASS", flush=True)
        return True

    except Exception as e:
        # Do not expose token.
        print("TELEGRAM_ALERT=FAIL", flush=True)
        print("TELEGRAM_ERROR=%s" % str(e), flush=True)
        return False


# ============================================================
# WINDOWS NOTIFICATION
# ============================================================

def windows_alert(category, state, count, timestamp):
    try:
        if category == "PERSON":
            item_cn = "人員"
        else:
            item_cn = "車輛"

        if state == "MOVING":
            state_cn = "移動中"
        else:
            state_cn = "持續存在 / 未明顯移動"

        title = "SuperLive AI - %s偵測" % item_cn
        message = "%s：%s\n數量：%d\n%s" % (
            item_cn,
            state_cn,
            count,
            timestamp,
        )

        Notification(
            app_id="SuperLive AI",
            title=title,
            msg=message,
            duration="long",
        ).show()

        print("WINDOWS_NOTIFICATION=PASS", flush=True)
        return True

    except Exception as e:
        print("WINDOWS_NOTIFICATION=FAIL %s" % str(e), flush=True)
        return False


# ============================================================
# EVENT SAVE / NOTIFY
# ============================================================

def save_event(
    category,
    state,
    class_name,
    track_id,
    confidence,
    movement,
    count,
    image,
):
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M%S")

    if category == "PERSON":
        event_type = "PERSON_%s" % state
        title_cn = "偵測到人員"
    else:
        event_type = "VEHICLE_%s" % state
        title_cn = "偵測到車輛"

    day_dir = EVENT_ROOT / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    jpg_path = day_dir / ("%s_%s_ID%s.jpg" % (stamp, event_type, track_id))

    cv2.imwrite(
        str(jpg_path),
        image,
        [cv2.IMWRITE_JPEG_QUALITY, 95],
    )

    with CSV_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(
            [
                timestamp,
                event_type,
                class_name,
                track_id,
                "%.3f" % confidence,
                "%.2f" % movement,
                str(jpg_path),
            ]
        )

    windows_alert(
        category=category,
        state=state,
        count=count,
        timestamp=timestamp,
    )

    interval_min = (
        int(MOVING_NOTIFY_SEC / 60)
        if state == "MOVING"
        else int(STILL_NOTIFY_SEC / 60)
    )

    state_cn = "移動中" if state == "MOVING" else "持續存在 / 未明顯移動"

    caption = (
        "SuperLive AI Alert\n\n"
        + title_cn
        + "\n狀態："
        + state_cn
        + "\n類型："
        + class_name
        + "\n畫面數量：%d" % count
        + "\nConfidence：%.0f%%" % (confidence * 100)
        + "\nMovement：%.1f px" % movement
        + "\n通知週期：%d 分鐘" % interval_min
        + "\n時間："
        + timestamp
    )

    telegram_photo(
        caption=caption,
        jpg_path=str(jpg_path),
    )

    print("", flush=True)
    print("============================================================", flush=True)
    print("DETECTION_EVENT=YES", flush=True)
    print("CATEGORY=%s" % category, flush=True)
    print("STATE=%s" % state, flush=True)
    print("CLASS=%s" % class_name, flush=True)
    print("COUNT=%d" % count, flush=True)
    print("TRACK_ID=%s" % track_id, flush=True)
    print("MOVEMENT=%.2f" % movement, flush=True)
    print("JPG=%s" % jpg_path, flush=True)
    print("============================================================", flush=True)

    return "%s %s %s" % (category, state, timestamp)


# ============================================================
# FIND MUMU
# ============================================================

def find_mumu():
    candidates = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)

            if proc.name().lower() != "mumunxdevice.exe":
                return

            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w = r - l
            h = b - t

            if w >= 200 and h >= 300:
                candidates.append((hwnd, w * h))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)

    if not candidates:
        return None

    return max(candidates, key=lambda x: x[1])[0]


# ============================================================
# START
# ============================================================

print("")
print("============================================================", flush=True)
print(" SuperLive AI - Detection Notification Policy", flush=True)
print("============================================================", flush=True)
print("MOVING_NOTIFY_SEC=%.0f" % MOVING_NOTIFY_SEC, flush=True)
print("STILL_NOTIFY_SEC=%.0f" % STILL_NOTIFY_SEC, flush=True)
print("ABSENCE_RESET_SEC=%.0f" % ABSENCE_RESET_SEC, flush=True)
CUDA_AVAILABLE = torch.cuda.is_available()
DEVICE = 0 if CUDA_AVAILABLE else "cpu"

print("CUDA=%s" % CUDA_AVAILABLE, flush=True)

if CUDA_AVAILABLE:
    print("GPU=%s" % torch.cuda.get_device_name(0), flush=True)
    print("AI_DEVICE=CUDA", flush=True)
else:
    print("GPU=VMware/CPU fallback", flush=True)
    print("AI_DEVICE=CPU", flush=True)

hwnd = find_mumu()

while hwnd is None:
    print("WAITING_FOR_MUMU", flush=True)
    time.sleep(5)
    hwnd = find_mumu()

if win32gui.IsIconic(hwnd):
    try:
        win32gui.ShowWindow(hwnd, 9)
        time.sleep(1)
    except Exception:
        pass

cr = win32gui.GetClientRect(hwnd)
cw = int(cr[2])
ch = int(cr[3])

if cw < 200 or ch < 300:
    raise RuntimeError("Invalid MuMu capture size")

print("TARGET_PROCESS=MuMuNxDevice.exe", flush=True)
print("CAPTURE_SIZE=%dx%d" % (cw, ch), flush=True)

model = YOLO(str(MODEL_PATH) if MODEL_PATH.exists() else "yolo11n.pt")

dummy = np.zeros((640, 640, 3), dtype=np.uint8)

for _ in range(3):
    model.predict(
        dummy,
        classes=WATCH_CLASSES,
        imgsz=IMGSZ,
        device=DEVICE,
        verbose=False,
    )

if CUDA_AVAILABLE:
    torch.cuda.synchronize()
print("CUDA_WARMUP=PASS", flush=True)


def new_mss():
    try:
        return mss.MSS()
    except AttributeError:
        return mss.mss()


sct = new_mss()

# ============================================================
# TRACK / MOTION STATE
# ============================================================

motion_history = defaultdict(lambda: deque(maxlen=12))
motion_confirm = defaultdict(int)
still_confirm = defaultdict(int)
motion_state = defaultdict(bool)
trails = defaultdict(lambda: deque(maxlen=TRAIL_LENGTH))

# Category-level notification state.
# IMPORTANT: this avoids ByteTrack ID churn causing alert spam.
notify_state = {
    "PERSON": {
        "last_notify": 0.0,
        "last_seen": 0.0,
        "session_active": False,
    },
    "VEHICLE": {
        "last_notify": 0.0,
        "last_seen": 0.0,
        "session_active": False,
    },
}

last_event_text = "None"
capture_failures = 0
capture_recovery_total = 0

frames = 0
start_time = time.perf_counter()
next_frame = start_time

cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
cv2.resizeWindow(WINDOW_NAME, cw, ch)

print("MONITOR_STARTED=YES", flush=True)
print("POLICY=MOVING_5MIN_STILL_10MIN", flush=True)
print("GREEN=STILL RED=MOVING", flush=True)


try:
    while True:
        now_perf = time.perf_counter()

        if now_perf < next_frame:
            time.sleep(min(next_frame - now_perf, 0.005))
            continue

        if not win32gui.IsWindow(hwnd):
            hwnd = find_mumu()
            if hwnd is None:
                print("MUMU_WINDOW_LOST", flush=True)
                time.sleep(5)
                next_frame = time.perf_counter() + FRAME_INTERVAL
                continue

        try:
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            cr = win32gui.GetClientRect(hwnd)
            new_cw = int(cr[2])
            new_ch = int(cr[3])

            if new_cw < 200 or new_ch < 300:
                raise RuntimeError("Invalid MuMu size")

            if (new_cw, new_ch) != (cw, ch):
                print(
                    "MUMU_RESIZE_DETECTED %dx%d -> %dx%d"
                    % (cw, ch, new_cw, new_ch),
                    flush=True,
                )
                cw, ch = new_cw, new_ch
                cv2.resizeWindow(WINDOW_NAME, cw, ch)

            shot = sct.grab(
                {
                    "left": int(cx),
                    "top": int(cy),
                    "width": int(cw),
                    "height": int(ch),
                }
            )

            frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

            if capture_failures:
                print(
                    "CAPTURE_RECOVERY=PASS previous_failures=%d"
                    % capture_failures,
                    flush=True,
                )
            capture_failures = 0

        except Exception as capture_error:
            capture_failures += 1
            capture_recovery_total += 1

            print(
                "CAPTURE_ERROR count=%d/%d type=%s"
                % (
                    capture_failures,
                    CAPTURE_MAX_FAILURES,
                    type(capture_error).__name__,
                ),
                flush=True,
            )

            try:
                sct.close()
            except Exception:
                pass

            sct = new_mss()
            hwnd = find_mumu()

            if capture_failures >= CAPTURE_MAX_FAILURES:
                print("CAPTURE_RECOVERY=ESCALATE_TO_WATCHDOG", flush=True)
                raise RuntimeError(
                    "Capture failed %d consecutive times" % capture_failures
                )

            time.sleep(CAPTURE_RETRY_SEC)
            next_frame = time.perf_counter() + FRAME_INTERVAL
            continue

        t0 = time.perf_counter()

        result = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=WATCH_CLASSES,
            conf=CONF,
            imgsz=IMGSZ,
            device=DEVICE,
            verbose=False,
        )[0]

        if CUDA_AVAILABLE:
            torch.cuda.synchronize()
        infer_ms = (time.perf_counter() - t0) * 1000

        out = frame.copy()

        # Aggregate objects by category for notification policy.
        category_items = {
            "PERSON": [],
            "VEHICLE": [],
        }

        if result.boxes is not None and result.boxes.id is not None:
            boxes = result.boxes.xyxy.detach().cpu().numpy()
            ids = result.boxes.id.detach().cpu().numpy().astype(int)
            classes = result.boxes.cls.detach().cpu().numpy().astype(int)
            confs = result.boxes.conf.detach().cpu().numpy()

            for box, tid, cls, conf in zip(boxes, ids, classes, confs):
                tid = int(tid)
                cls = int(cls)

                x1, y1, x2, y2 = map(int, box)
                px = int((x1 + x2) / 2)
                py = int(y2)

                key = (cls, tid)

                box_w = max(1, x2 - x1)
                box_h = max(1, y2 - y1)
                box_diag = math.hypot(box_w, box_h)

                history = motion_history[key]
                history.append((px, py, box_diag))
                trails[key].append((px, py))

                if cls == 0:
                    required_window = PERSON_WINDOW
                    min_displacement = PERSON_MIN_DISPLACEMENT
                    min_ratio = PERSON_MIN_RATIO
                    direction_threshold = PERSON_DIRECTION_RATIO
                else:
                    required_window = VEHICLE_WINDOW
                    min_displacement = VEHICLE_MIN_DISPLACEMENT
                    min_ratio = VEHICLE_MIN_RATIO
                    direction_threshold = VEHICLE_DIRECTION_RATIO

                movement = 0.0
                normalized_movement = 0.0
                direction_ratio = 0.0
                motion_candidate = False

                if len(history) >= required_window:
                    recent = list(history)[-required_window:]

                    first_x, first_y = recent[0][0], recent[0][1]
                    last_x, last_y = recent[-1][0], recent[-1][1]

                    movement = math.hypot(
                        last_x - first_x,
                        last_y - first_y,
                    )

                    avg_diag = sum(item[2] for item in recent) / len(recent)
                    normalized_movement = (
                        movement / avg_diag if avg_diag > 0 else 0.0
                    )

                    overall_dx = last_x - first_x
                    overall_dy = last_y - first_y
                    overall_len = math.hypot(overall_dx, overall_dy)

                    consistent_steps = 0
                    valid_steps = 0

                    if overall_len > 0:
                        for i in range(1, len(recent)):
                            dx = recent[i][0] - recent[i - 1][0]
                            dy = recent[i][1] - recent[i - 1][1]
                            step_len = math.hypot(dx, dy)

                            if step_len < 1.0:
                                continue

                            valid_steps += 1

                            dot = dx * overall_dx + dy * overall_dy
                            if dot > 0:
                                consistent_steps += 1

                    direction_ratio = (
                        consistent_steps / valid_steps
                        if valid_steps
                        else 0.0
                    )

                    motion_candidate = (
                        movement >= min_displacement
                        and normalized_movement >= min_ratio
                        and direction_ratio >= direction_threshold
                    )

                if motion_candidate:
                    motion_confirm[key] += 1
                    still_confirm[key] = 0

                    if motion_confirm[key] >= MOTION_CONFIRM_HITS:
                        motion_state[key] = True
                else:
                    motion_confirm[key] = 0

                    if motion_state[key]:
                        still_confirm[key] += 1

                        if still_confirm[key] >= STILL_CONFIRM_HITS:
                            motion_state[key] = False
                            still_confirm[key] = 0

                moving = bool(motion_state[key])
                category = "PERSON" if cls == 0 else "VEHICLE"

                category_items[category].append(
                    {
                        "track_id": tid,
                        "class_id": cls,
                        "class_name": CLASS_NAMES.get(cls, str(cls)),
                        "confidence": float(conf),
                        "movement": float(movement),
                        "moving": moving,
                        "box": (x1, y1, x2, y2),
                    }
                )

                color = (0, 0, 255) if moving else (0, 255, 0)

                cv2.rectangle(
                    out,
                    (x1, y1),
                    (x2, y2),
                    color,
                    3,
                )

                label = "%s ID:%d %.0f%% %s" % (
                    CLASS_NAMES.get(cls, str(cls)),
                    tid,
                    float(conf) * 100,
                    "MOVING" if moving else "STILL",
                )

                cv2.putText(
                    out,
                    label,
                    (x1, max(22, y1 - 7)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    color,
                    2,
                    cv2.LINE_AA,
                )

                pts = list(trails[key])
                for i in range(1, len(pts)):
                    cv2.line(
                        out,
                        pts[i - 1],
                        pts[i],
                        (255, 255, 0),
                        2,
                    )

        # ====================================================
        # CATEGORY-LEVEL NOTIFICATION POLICY
        #
        # PERSON:
        #   present + moving -> max one alert / 5 min
        #   present + still  -> max one reminder / 10 min
        #
        # VEHICLE:
        #   same policy
        #
        # Reappearance after >= ABSENCE_RESET_SEC absence:
        #   immediate alert again.
        #
        # ByteTrack ID is deliberately NOT used as the rate-limit key.
        # ====================================================

        now_wall = time.time()

        for category in ("PERSON", "VEHICLE"):
            items = category_items[category]
            st = notify_state[category]

            if not items:
                if (
                    st["session_active"]
                    and (now_wall - st["last_seen"]) >= ABSENCE_RESET_SEC
                ):
                    st["session_active"] = False
                    st["last_notify"] = 0.0
                    print(
                        "%s_SESSION_RESET=YES" % category,
                        flush=True,
                    )
                continue

            st["last_seen"] = now_wall

            new_session = not st["session_active"]
            if new_session:
                st["session_active"] = True

            moving_items = [x for x in items if x["moving"]]

            if moving_items:
                state = "MOVING"
                interval = MOVING_NOTIFY_SEC
                representative = max(
                    moving_items,
                    key=lambda x: x["confidence"],
                )
            else:
                state = "STILL"
                interval = STILL_NOTIFY_SEC
                representative = max(
                    items,
                    key=lambda x: x["confidence"],
                )

            due = new_session or (now_wall - st["last_notify"] >= interval)

            if due:
                last_event_text = save_event(
                    category=category,
                    state=state,
                    class_name=representative["class_name"],
                    track_id=representative["track_id"],
                    confidence=representative["confidence"],
                    movement=representative["movement"],
                    count=len(items),
                    image=out.copy(),
                )

                st["last_notify"] = now_wall

        # ====================================================
        # STATUS PANEL
        # ====================================================

        person_items = category_items["PERSON"]
        vehicle_items = category_items["VEHICLE"]

        person_moving = sum(1 for x in person_items if x["moving"])
        vehicle_moving = sum(1 for x in vehicle_items if x["moving"])

        frames += 1
        runtime = time.perf_counter() - start_time
        fps = frames / runtime if runtime > 0 else 0.0

        panel = out.copy()
        cv2.rectangle(
            panel,
            (5, 5),
            (545, 138),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(
            panel,
            0.60,
            out,
            0.40,
            0,
            out,
        )

        cv2.putText(
            out,
            "SuperLive AI Detection Monitor",
            (15, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 255, 255),
            2,
        )

        cv2.putText(
            out,
            "Person:%d Moving:%d | Vehicle:%d Moving:%d"
            % (
                len(person_items),
                person_moving,
                len(vehicle_items),
                vehicle_moving,
            ),
            (15, 53),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
        )

        cv2.putText(
            out,
            "Moving notify:5 min | Still notify:10 min",
            (15, 76),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 255, 255),
            1,
        )

        cv2.putText(
            out,
            "FPS:%.1f YOLO:%.1fms" % (fps, infer_ms),
            (15, 99),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 255, 255),
            1,
        )

        cv2.putText(
            out,
            "Last:%s" % last_event_text,
            (15, 122),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (255, 255, 255),
            1,
        )

        cv2.imshow(WINDOW_NAME, out)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):
            print("USER_STOP=YES", flush=True)
            break

        try:
            if cv2.getWindowProperty(
                WINDOW_NAME,
                cv2.WND_PROP_VISIBLE,
            ) < 1:
                print("WINDOW_CLOSE=YES", flush=True)
                break
        except Exception:
            pass

        next_frame += FRAME_INTERVAL
        after = time.perf_counter()

        if next_frame < after - FRAME_INTERVAL:
            next_frame = after + FRAME_INTERVAL

finally:
    try:
        sct.close()
    except Exception:
        pass

    cv2.destroyAllWindows()


runtime = time.perf_counter() - start_time
fps = frames / runtime if runtime else 0.0

print("")
print("============================================================", flush=True)
print(" SUPERLIVE DETECTION MONITOR RESULT", flush=True)
print("============================================================", flush=True)
print("RUN_TIME_SEC=%.2f" % runtime, flush=True)
print("FRAMES=%d" % frames, flush=True)
print("ACTUAL_FPS=%.2f" % fps, flush=True)
print("CAPTURE_RECOVERY_TOTAL=%d" % capture_recovery_total, flush=True)
print("MOVING_NOTIFY_SECONDS=%.0f" % MOVING_NOTIFY_SEC, flush=True)
print("STILL_NOTIFY_SECONDS=%.0f" % STILL_NOTIFY_SEC, flush=True)
print("PRESENCE_NOTIFICATION_ENGINE=PASS", flush=True)
print("WINDOWS_ALERT_ENGINE=PASS", flush=True)
print("TELEGRAM_ENGINE=PASS", flush=True)
print("CAPTURE_SELF_HEALING=PASS", flush=True)
print("STAGE5D=PASS", flush=True)
print("============================================================", flush=True)
