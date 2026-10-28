"""Polygon activity starts 2–5 minute clips with a silent minimum period."""
import csv
import subprocess
import sys
import ctypes
import json
import math
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue

import cv2
import mss
import numpy as np
import psutil
import torch
import win32gui
import win32process
from ultralytics import YOLO
from winotify import Notification

from record_protocol import (atomic_json, read_json, recorder_ready, recording_active,
                             valid_end_request, MIN_RECORD_SECONDS, MAX_RECORD_SECONDS)
from person_activity import PersonActivityTracker, InactivityTimer
from region_policy import people_in_region, objects_in_region, object_counts, ACTIVITY_CLASSES, validate_roi, rebase_roi
from preview_ui import (PreviewController, PreviewLayout, render_preview,
                        latest_vehicle_footer_text)
from capture_region import find_emulator_window, capture_frame, CaptureRegionResolver, CaptureRegionUnavailable
from app_branding import set_app_identity, apply_window_icon
from adb_capture import AdbStream, settings as capture_settings
from capture_region import CaptureInfo
from plate_recognition import PlateService, load_allowlist
from video_location import reveal_video, VideoLocationError
from latest_vehicle import LatestVehicleTracker
from plate_review import launch_review

ROOT = Path.home() / "Downloads" / "SuperLive_AI"
MODEL_PATH = ROOT / "models" / "yolo11n.pt"
EVENT_ROOT = ROOT / "events"
POLICY_FILE = ROOT / "config" / "notification_policy.json"
TELEGRAM_ENV = ROOT / "config" / "telegram.env"
RUNTIME = ROOT / "runtime"
STOP_FLAG = RUNTIME / "stop.flag"
REQUEST_FILE = RUNTIME / "record_request.json"
END_REQUEST_FILE = RUNTIME / "record_end_request.json"
STATE_FILE = RUNTIME / "recording_state.json"
INACTIVITY_SECONDS = 120.0
WINDOW_NAME = "SuperLive AI - Person / Region"
DEFAULT_POLICY = {
    "target_fps": 10.0, "confidence": 0.25, "imgsz": 640,
    "watch_classes": list(ACTIVITY_CLASSES), "roi": None,
    "capture_max_failures": 5, "capture_retry_seconds": 1.0,
}


def load_policy():
    cfg = dict(DEFAULT_POLICY)
    if POLICY_FILE.exists():
        with POLICY_FILE.open(encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("notification_policy.json must contain an object")
        cfg.update(loaded)
    cfg["roi"] = validate_roi(cfg.get("roi"))
    cfg["watch_classes"] = list(ACTIVITY_CLASSES)
    if not 0 < float(cfg["target_fps"]) <= 60:
        raise ValueError("target_fps must be > 0 and <= 60")
    if not 0 < float(cfg["confidence"]) <= 1:
        raise ValueError("confidence must be > 0 and <= 1")
    return cfg


def save_roi(cfg, roi):
    updated = dict(cfg)
    updated["roi"] = validate_roi(roi)
    updated["roi_coordinate_space"] = "mumu_content_v1"
    updated.pop("roi_reselect_required", None)
    for old_key in ("moving_notify_seconds", "still_notify_seconds", "absence_reset_seconds"):
        updated.pop(old_key, None)
    atomic_json(POLICY_FILE, updated)
    cfg.clear()
    cfg.update(updated)
    print("ROI_SAVED=%s" % ("FULL_FRAME" if roi is None else roi), flush=True)


def ensure_content_roi(cfg, info):
    """Keep legacy selections on the same camera pixels after toolbar removal."""
    if cfg.get("roi_coordinate_space") == "mumu_content_v1":
        return not cfg.get("roi_reselect_required", False)
    try:
        migrated = rebase_roi(cfg["roi"], info.parent_client_rect, info.content_rect)
    except ValueError:
        # A selection entirely inside the removed toolbar must not become full frame.
        updated = dict(cfg, roi_coordinate_space="mumu_content_v1", roi_reselect_required=True)
        atomic_json(POLICY_FILE, updated)
        cfg.update(updated)
        return False
    save_roi(cfg, migrated)
    return True


def load_telegram():
    config = {}
    if TELEGRAM_ENV.exists():
        for raw in TELEGRAM_ENV.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config.get("BOT_TOKEN", ""), config.get("CHAT_ID", "")


def notify_detection(image, people, roi, request_id, credentials):
    """Save local evidence and a Windows toast; never send a Telegram start alert.

    The credentials argument remains for local-call compatibility. Completed
    videos and daily CSV reports are delivered by recorder background workers.
    """
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    day = EVENT_ROOT / now.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    counts = object_counts(people)
    types = [kind for kind, count in counts.items() if count]
    event_type = "PERSON_VEHICLE_ACTIVITY" if counts["person"] and len(types) > 1 else "PERSON_ACTIVITY" if counts["person"] else "VEHICLE_ACTIVITY"
    jpg = day / ("%s_ACTIVITY_%s.jpg" % (now.strftime("%Y%m%d_%H%M%S"), request_id))
    confidence = max(item["confidence"] for item in people)
    area = "全畫面" if roi is None else "指定區域"
    written = cv2.imwrite(str(jpg), image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    csv_path = EVENT_ROOT / "person_events.csv"
    new_csv = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        if new_csv:
            writer.writerow(["timestamp", "event_type", "count", "confidence", "request_id", "roi", "snapshot"])
        writer.writerow([timestamp, event_type, len(people), "%.3f" % confidence, request_id, json.dumps(roi), str(jpg) if written else ""])
    try:
        Notification(app_id="SuperLive AI", title="SuperLive AI - 偵測到人物／車輛移動",
                     msg="%s：人物 %d、車輛 %d，已開始錄影；前 2 分鐘 Telegram 靜默\n%s" % (area, counts["person"], len(people)-counts["person"], timestamp),
                     duration="short").show()
    except Exception as error:
        print("WINDOWS_NOTIFICATION_ERROR=%s" % type(error).__name__, flush=True)
    print("ACTIVITY_DETECTED persons=%d vehicles=%d request=%s" %
          (counts["person"], len(people)-counts["person"], request_id), flush=True)


def find_mumu():
    # Compatibility name retained for existing callers.
    return find_emulator_window(win32gui, win32process, psutil)


def new_mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def can_trigger():
    return not STOP_FLAG.exists() and recorder_ready(
        read_json(STATE_FILE), pending=REQUEST_FILE.exists(), pid_alive=psutil.pid_exists,
    )


def recording_request(people, cfg):
    return {"request_id": uuid.uuid4().hex, "created_at": time.time(),
            "count": len(people), "confidence": max(item["confidence"] for item in people),
            "roi": deepcopy(cfg["roi"]), "object_counts": object_counts(people)}


def trigger_recording(image, people, cfg, credentials):
    """Dispatch caller-confirmed activity; DetectionSession applies motion gating."""
    # A pending request closes the idle window until the recorder consumes it.
    if not people or not can_trigger():
        return False
    request = recording_request(people, cfg)
    atomic_json(REQUEST_FILE, request)
    # Only local evidence/Windows notifications happen at the start. The queue
    # enforces Telegram silence independently of this asynchronous worker.
    try:
        dispatch_detection_notification(image, people, cfg["roi"], request["request_id"], credentials)
    except Exception as error:
        print("DETECTION_NOTIFICATION_ERROR=%s" % type(error).__name__, flush=True)
    return True


def can_observe_activity(state):
    """Inference continues while recording, without sending another start alert."""
    return not STOP_FLAG.exists() and (
        recorder_ready(state, pending=REQUEST_FILE.exists(), pid_alive=psutil.pid_exists)
        or recording_active(state, pid_alive=psutil.pid_exists)
    )


def recording_elapsed(state):
    try:
        value = state["elapsed_seconds"]
        if isinstance(value, bool):
            return None
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def request_recording_end(observed_state, reason="NO_MOVEMENT", next_request=None):
    """End only the event observed by this detector, never a later recording."""
    current = read_json(STATE_FILE)
    if (STOP_FLAG.exists() or not recording_active(observed_state, pid_alive=psutil.pid_exists)
            or not recording_active(current, pid_alive=psutil.pid_exists)
            or observed_state["request_id"] != current["request_id"]):
        return False
    request_id = current["request_id"]
    observed_elapsed, current_elapsed = recording_elapsed(observed_state), recording_elapsed(current)
    if (observed_elapsed is None or current_elapsed is None
            or min(observed_elapsed, current_elapsed) < MIN_RECORD_SECONDS):
        return False
    if reason == "MOVEMENT" and max(observed_elapsed, current_elapsed) >= MAX_RECORD_SECONDS:
        return False
    if valid_end_request(read_json(END_REQUEST_FILE), request_id):
        return False
    request = {"request_id": request_id, "created_at": time.time(), "reason": reason,
               "created_monotonic": time.monotonic()}
    if next_request is not None:
        request["next_request"] = next_request
    if not valid_end_request(request, request_id):
        return False
    atomic_json(END_REQUEST_FILE, request)
    print("EVENT_END_REQUESTED=%s REQUEST=%s" % (reason, request_id), flush=True)
    return True


class DetectionSession:
    """Keep motion anchors across clips so a stationary person cannot retrigger."""
    def __init__(self):
        self.motion = PersonActivityTracker()
        self.inactivity = InactivityTimer(INACTIVITY_SECONDS)

    def suspend(self, reset_region=False):
        self.inactivity.suspend()
        if reset_region:
            self.motion.reset()

    def observe(self, image, people, cfg, credentials, state, width, height):
        current = read_json(STATE_FILE)
        active = recording_active(state, pid_alive=psutil.pid_exists)
        same_recording = (active and recording_active(current, pid_alive=psutil.pid_exists)
                          and state["request_id"] == current["request_id"])
        pending = REQUEST_FILE.exists()
        still_idle = (recorder_ready(state, pending=pending, pid_alive=psutil.pid_exists)
                      and recorder_ready(current, pending=pending, pid_alive=psutil.pid_exists))
        if STOP_FLAG.exists() or not (same_recording or still_idle):
            self.suspend()
            return None
        now = time.monotonic()
        moving = self.motion.update(people, width, height, now)
        if same_recording:
            elapsed = recording_elapsed(state)
            if moving and people and elapsed is not None and MIN_RECORD_SECONDS <= elapsed < MAX_RECORD_SECONDS:
                successor = recording_request(people, cfg)
                if request_recording_end(state, "MOVEMENT", successor):
                    # This worker is local-only; it cannot bypass Telegram silence.
                    try:
                        dispatch_detection_notification(image, people, cfg["roi"], successor["request_id"], credentials)
                    except Exception as error:
                        print("DETECTION_NOTIFICATION_ERROR=%s" % type(error).__name__, flush=True)
                    return "recording_rollover_requested"
            if self.inactivity.observe(state["request_id"], moving, now):
                if request_recording_end(state):
                    return "recording_end_requested"
        else:
            self.inactivity.reset()
            if moving and people and trigger_recording(image, people, cfg, credentials):
                return "recording_started"
        return None


def dispatch_detection_notification(image, people, roi, request_id, credentials):
    snapshot = image.copy()
    region = deepcopy(roi)
    def deliver():
        try:
            notify_detection(snapshot, people, region, request_id, credentials)
        except Exception as error:
            print("DETECTION_NOTIFICATION_ERROR=%s" % type(error).__name__, flush=True)
    threading.Thread(target=deliver, name="IdleDetectionAlert", daemon=True).start()


def preview_status(state, pending, capture_status=None):
    status = state.get("status", "WAITING_FOR_RECORDER") if isinstance(state, dict) else "WAITING_FOR_RECORDER"
    if pending:
        return "STARTING"
    if status == "IDLE" and not recorder_ready(state, pid_alive=psutil.pid_exists):
        return "WAITING_FOR_RECORDER"
    # Recording continues independently even while the monitor is selecting/recovering.
    return capture_status if capture_status and status != "RECORDING" else status


def main():
    set_dpi_awareness()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    EVENT_ROOT.mkdir(parents=True, exist_ok=True)
    cfg = load_policy()
    credentials = ("", "")  # Telegram credentials belong to delivery workers.
    interval = 1.0 / float(cfg["target_fps"])
    device = 0 if torch.cuda.is_available() else "cpu"
    model = YOLO(str(MODEL_PATH) if MODEL_PATH.exists() else "yolo11n.pt")
    print("POLICY=PERSON_VEHICLE_ACTIVITY_REGION_MIN_120S_ROLLOVER_MAX_300S_QUIET_120S", flush=True)
    print("AI_DEVICE=%s" % device, flush=True)
    print("ROI=%s" % (cfg["roi"] if cfg["roi"] is not None else "FULL_FRAME"), flush=True)
    adb_stream = AdbStream(ROOT) if capture_settings(ROOT).get("source") == "adb" else None
    plate_service = PlateService(ROOT) if cfg.get("plate_enabled", False) else None
    sct = new_mss()
    capture_resolver = CaptureRegionResolver()
    last_capture_area = None
    hwnd = None
    failures = 0
    next_frame = time.monotonic()
    next_capture = 0.0
    capture_status = "WAITING_FOR_MUMU"
    last_frame = np.zeros((540, 960, 3), dtype=np.uint8)
    canvas_size = (1280, 820)
    controller = PreviewController(cfg["roi"])
    controller.plate_enabled = bool(plate_service)
    controller.plate_filter_targets = load_allowlist(ROOT)
    next_plate_filter_read = time.monotonic() + 2
    controller.model_label = MODEL_PATH.stem.upper().replace("YOLO11N", "YOLO11n")
    latest_vehicle = LatestVehicleTracker(ROOT)
    controller.latest_vehicle = latest_vehicle.snapshot()
    view_settings = read_json(ROOT / "runtime/preview_layout.json", {})
    saved_order = view_settings.get("panel_order") if isinstance(view_settings, dict) else None
    if (isinstance(saved_order, list) and len(saved_order) == 4
            and all(isinstance(name, str) for name in saved_order) and set(saved_order) == set(controller.panel_order)):
        controller.panel_order = saved_order
    detection_session = DetectionSession()
    ui_actions = []
    message = None
    message_until = 0.0
    reveal_results = SimpleQueue()
    reveal_thread = None
    review_process = None
    settings_process = None
    next_review_read = 0.0

    def reveal_in_background(event):
        try:
            path = reveal_video(ROOT, event)
            reveal_results.put("已在檔案總管選取：" + path.name)
        except VideoLocationError as error:
            reveal_results.put(str(error))
        except Exception:
            reveal_results.put("無法開啟影片位置，請稍後再試")
    set_app_identity()
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    apply_window_icon(WINDOW_NAME)
    cv2.resizeWindow(WINDOW_NAME, *canvas_size)

    def mouse_callback(event, x, y, flags, userdata):
        events = {cv2.EVENT_LBUTTONDOWN: "down", cv2.EVENT_MOUSEMOVE: "move",
                  cv2.EVENT_LBUTTONUP: "up", cv2.EVENT_RBUTTONDOWN: "right_down"}
        if event == cv2.EVENT_MOUSEWHEEL:
            delta = (flags >> 16) & 0xFFFF
            if delta >= 0x8000:
                delta -= 0x10000
            controller.mouse("wheel_up" if delta > 0 else "wheel_down", x, y)
            return
        if event in events:
            # HighGUI already reports coordinates in the displayed canvas image.
            action = controller.mouse(events[event], x, y)
            if action is not None:
                ui_actions.append(action)

    cv2.setMouseCallback(WINDOW_NAME, mouse_callback)
    try:
        if adb_stream:
            adb_stream.start()
        if plate_service:
            plate_service.start()
        while not STOP_FLAG.exists():
            if time.monotonic() < next_frame:
                time.sleep(max(0, min(next_frame - time.monotonic(), 0.01)))
                continue
            capture_ok = False
            if adb_stream:
                try:
                    last_frame = adb_stream.capture()
                    h, w = last_frame.shape[:2]
                    capture_info = CaptureInfo((0, 0, w, h), (0, 0, w, h), "adb_stream")
                    if ensure_content_roi(cfg, capture_info):
                        controller.roi = deepcopy(cfg["roi"])
                    elif not controller.selecting:
                        controller.command("select")
                    capture_ok, capture_status = True, None
                except Exception:
                    capture_status = adb_stream.status
            else:
                if time.monotonic() >= next_capture:
                    if hwnd is None or not win32gui.IsWindow(hwnd):
                        hwnd = find_mumu()
                        if hwnd is None:
                            capture_status = "WAITING_FOR_MUMU"
                            next_capture = time.monotonic() + 1.0
                        elif win32gui.IsIconic(hwnd):
                            win32gui.ShowWindow(hwnd, 9)
                    if hwnd is not None:
                        try:
                            last_frame, capture_info = capture_frame(
                                sct, hwnd, win32gui, cv2, np, resolver=capture_resolver)
                            area = (capture_info.parent_client_rect, capture_info.content_rect)
                            if area != last_capture_area:
                                print("CAPTURE_AREA=%s METHOD=%s" % (capture_info.content_rect, capture_info.method), flush=True)
                                last_capture_area = area
                            if ensure_content_roi(cfg, capture_info):
                                controller.roi = deepcopy(cfg["roi"])
                            elif not controller.selecting:
                                controller.roi = None
                                controller.command("select")
                            failures, capture_ok, capture_status = 0, True, None
                        except CaptureRegionUnavailable:
                            # Keep controls responsive without recording an uncertain menu area.
                            capture_status = "FINDING_CAMERA_AREA"
                            next_capture = time.monotonic() + 1.0
                        except Exception as error:
                            failures += 1
                            print("CAPTURE_ERROR=%s count=%d" % (type(error).__name__, failures), flush=True)
                            sct.close()
                            sct = new_mss()
                            hwnd = None
                            capture_status = "CAPTURE_RETRY"
                            if failures >= int(cfg["capture_max_failures"]):
                                raise RuntimeError("Capture failed repeatedly; watchdog will retry")
                            next_capture = time.monotonic() + float(cfg["capture_retry_seconds"])
            frame = last_frame
            frame_timestamp = time.time()
            height, width = frame.shape[:2]
            evidence = frame.copy()
            people = []
            infer_ms = None
            count = None
            # Continue inference while recording, but only IDLE can start/notify.
            # Unavailable frames and region edits never count as observed inactivity.
            observation_state = read_json(STATE_FILE)
            if (capture_ok and not controller.selecting and not cfg.get("roi_reselect_required")
                    and can_observe_activity(observation_state)):
                started = time.monotonic()
                result = model.predict(frame, classes=cfg["watch_classes"], conf=float(cfg["confidence"]),
                                       imgsz=int(cfg["imgsz"]), device=device, verbose=False)[0]
                infer_ms = (time.monotonic() - started) * 1000
                if result.boxes is not None:
                    people = objects_in_region(
                        result.boxes.xyxy.detach().cpu().numpy(),
                        result.boxes.cls.detach().cpu().numpy(),
                        result.boxes.conf.detach().cpu().numpy(),
                        cfg["roi"], width, height, float(cfg["confidence"]),
                    )
                for person in people:
                    x1, y1, x2, y2 = person["box"]
                    color = (0, 255, 0) if person["class_id"] == 0 else (255, 190, 0)
                    cv2.rectangle(evidence, (x1, y1), (x2, y2), color, 2)
                count = len(people)
            controller.object_counts = object_counts(people) if count is not None else None
            latest_vehicle.observe(people, frame_timestamp, (width, height), available=count is not None, frame=frame)

            if plate_service:
                if time.monotonic() >= next_plate_filter_read:
                    controller.plate_filter_targets = load_allowlist(ROOT)
                    next_plate_filter_read = time.monotonic() + 2
                if capture_ok:
                    plate_service.submit(frame, frame_timestamp, None, vehicles=people)
                else:
                    plate_service.pause()
                plate_state = plate_service.snapshot()
                latest_vehicle.update_candidate(plate_state.get('candidate'))
                history = plate_state.get("recent", [])
                if history != controller.plate_events:
                    controller.plate_events = history
                    latest_vehicle.update_plates(history)
                controller.plate_status = plate_state.get("status", "")
                controller.plate_error = plate_state.get("error", "")
                if adb_stream:
                    controller.plate_status += " | ADB %dx%d | %s" % (width, height, adb_stream.quality)
            try:
                _, _, view_width, view_height = cv2.getWindowImageRect(WINDOW_NAME)
                if view_width >= 320 and view_height >= 300:
                    canvas_size = (view_width, view_height)
            except (cv2.error, AttributeError):
                pass
            if time.monotonic() >= next_review_read:
                latest_vehicle.apply_manual_confirmation(read_json(ROOT/'runtime/manual_plate_confirmation.json', {}))
                next_review_read = time.monotonic()+.5
            controller.latest_vehicle = latest_vehicle.snapshot()
            controller.latest_vehicle_image = latest_vehicle.image
            controller.latest_plate_image = latest_vehicle.plate_image
            controller.layout = PreviewLayout.create(width, height, *canvas_size,
                show_plates=controller.plate_enabled,
                plate_sidebar=controller.plate_enabled,
                plates_open=controller.plate_panel_open if plate_service else False,
                plate_status_text="",
                latest_vehicle_status=latest_vehicle_footer_text(controller),
                dashboard=True, panel_order=controller.panel_order)
            state = read_json(STATE_FILE)
            status = preview_status(state, REQUEST_FILE.exists(), capture_status)
            elapsed = state.get("elapsed_seconds") if isinstance(state, dict) else None
            try:
                message = reveal_results.get_nowait()
                message_until = time.monotonic() + 6
            except Empty:
                pass
            current_message = message if time.monotonic() < message_until else None
            if cfg.get("roi_reselect_required"):
                current_message = "原區域無法轉換至攝影機影像，請重新點選多邊形並儲存"
            preview = render_preview(evidence, controller, status, count, infer_ms, elapsed, current_message)
            cv2.imshow(WINDOW_NAME, preview)
            key = cv2.waitKeyEx(1)
            action = controller.key(key)
            if action is not None:
                ui_actions.append(action)
            changed = any(item.kind in ("save", "selection_started", "selection_cancelled", "stop")
                          for item in ui_actions)
            while ui_actions:
                action = ui_actions.pop(0)
                if action.kind == "stop":
                    STOP_FLAG.write_text("STOP", encoding="utf-8")
                elif action.kind in ('settings', 'alert_settings'):
                    if settings_process is None or settings_process.poll() is not None:
                        try:
                            settings_process = subprocess.Popen([sys.executable, str(ROOT/'scripts/setup_wizard.py'), '--root', str(ROOT), '--tab', 'telegram' if action.kind == 'settings' else 'plates'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                            message, message_until = '已開啟設定；警示名單儲存後自動套用，Telegram 變更需重啟', time.monotonic()+6
                        except OSError:
                            message, message_until = '無法開啟設定，請使用 Settings.bat', time.monotonic()+6
                elif action.kind == "layout_changed":
                    try:
                        atomic_json(ROOT / "runtime/preview_layout.json", dict(panel_order=controller.panel_order))
                        message, message_until = "排列已儲存", time.monotonic()+6
                    except OSError:
                        message, message_until = "排列已更新，本次無法儲存設定", time.monotonic()+5
                elif action.kind == 'review_plate':
                    if review_process is None or review_process.poll() is not None:
                        try:
                            review_process = launch_review(ROOT, latest_vehicle)
                            message, message_until = '已開啟車牌確認視窗，監控繼續運作', time.monotonic()+6
                        except ValueError as error:
                            message, message_until = str(error), time.monotonic()+6
                        except Exception:
                            message, message_until = '車牌確認視窗無法開啟，請稍後重試', time.monotonic()+6
                elif action.kind == "reveal_video":
                    if reveal_thread is None or not reveal_thread.is_alive():
                        reveal_thread = threading.Thread(target=reveal_in_background,
                            args=(deepcopy(action.event),), name="RevealRecording", daemon=True)
                        reveal_thread.start()
                elif action.kind == "save":
                    try:
                        save_roi(cfg, action.roi)
                        controller.roi = deepcopy(cfg["roi"])
                        detection_session.suspend(reset_region=True)
                        message = None
                    except Exception as error:
                        print("ROI_SAVE_ERROR=%s" % type(error).__name__, flush=True)
                        message, message_until = "ROI SAVE FAILED - previous region preserved", time.monotonic()+5
            if STOP_FLAG.exists():
                break
            try:
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    STOP_FLAG.write_text("STOP", encoding="utf-8")
                    break
            except cv2.error:
                STOP_FLAG.write_text("STOP", encoding="utf-8")
                break
            # Any selection/control change discards detections from the old ROI.
            # The footer is composed separately and can never enter evidence or ROI.
            if count is not None and not controller.selecting and not changed and not cfg.get("roi_reselect_required"):
                detection_session.observe(evidence, people, cfg, credentials, observation_state, width, height)
            else:
                detection_session.suspend()
                latest_vehicle.suspend()
            next_frame = time.monotonic() + interval
    finally:
        latest_vehicle.flush(force=True)
        sct.close()
        if plate_service:
            plate_service.stop()
        if adb_stream:
            adb_stream.stop()
        cv2.destroyAllWindows()
        print("MONITOR_STOPPED", flush=True)


if __name__ == "__main__":
    main()
