"""One preview canvas with a bottom control bar and in-window ROI selection.

Geometry and interaction can be tested without OpenCV, a camera, or a desktop.
Text measurement and rendering import Pillow only when needed.
"""
from dataclasses import dataclass
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timedelta, timezone
import math
import time

from region_policy import roi_vertices, validate_roi
from latest_vehicle import latest_vehicle_text


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    @property
    def bounds(self):
        return (self.x, self.y, self.x + self.width, self.y + self.height)


@dataclass
class PreviewLayout:
    width: int
    height: int
    video: Rect
    footer: Rect
    buttons: dict
    font_size: int
    line_height: int
    line_count: int
    plates: object = None
    plate_status_lines: tuple = ()
    plate_sidebar: bool = False
    latest_vehicle_lines: tuple = ()
    latest_vehicle_top: int = 0
    panels: object = None
    headers: object = None

    @classmethod
    def create(cls, frame_width, frame_height, width=960, height=720, show_plates=False,
               plates_open=False, plate_status_text="", plate_sidebar=False, latest_vehicle_status="",
               dashboard=False, panel_order=None):
        if dashboard:
            return cls.four_panel(frame_width, frame_height, width, height, show_plates, panel_order)
        width = max(320, int(width))
        plate_sidebar = bool(show_plates and plate_sidebar)
        side_panel = plate_sidebar and width >= 900
        font_size = max(12, min(18, width // 52))
        line_height = font_size + 6
        button_height = font_size + 20
        line_count = 3 if width >= 640 else 4
        if show_plates:
            rows = (("select", "full", "stop", "plates", "confirm", "cancel"),) if width >= 900 else (
                ("select", "full", "stop"), ("plates", "confirm", "cancel"))
        else:
            rows = (("select", "full", "stop", "confirm", "cancel"),) if width >= 640 else (
                ("select", "full", "stop"), ("confirm", "cancel"))
        plate_lines = tuple(plate_footer_lines(plate_status_text, width, font_size)) if show_plates else ()
        latest_lines = tuple(plate_footer_lines(latest_vehicle_status, width, font_size))
        footer_height = max(130, 24 + line_height * (line_count+len(plate_lines))
                            + (button_height+6)*len(rows))
        if latest_lines:
            footer_height += 6 + line_height * len(latest_lines)
        bottom_panel = show_plates and (plate_sidebar or plates_open) and not side_panel
        minimum_height = max(600, footer_height+320) if bottom_panel else max(300, footer_height+120)
        height = max(minimum_height, int(height))
        image_height = height - footer_height
        image_width = width
        plates = None
        if side_panel:
            panel_width = min(390, max(300, round(width*.3)))
            image_width -= panel_width
            plates = Rect(image_width, 0, panel_width, image_height)
        elif bottom_panel:
            # The opt-in history drawer takes its own space below the complete
            # letterboxed camera image. It never masks camera pixels.
            panel_height = min(max(200, height//3), 320, image_height-120)
            plates = Rect(0, image_height-panel_height, width, panel_height)
            image_height -= panel_height
        scale = min(image_width / max(1, frame_width), image_height / max(1, frame_height))
        video_width = max(1, int(frame_width * scale))
        video_height = max(1, int(frame_height * scale))
        video = Rect((image_width-video_width)//2, (image_height-video_height)//2, video_width, video_height)
        footer = Rect(0, height-footer_height, width, footer_height)
        margin, gap = 12, 8
        top = footer.y + 12 + line_height * (line_count+len(plate_lines))
        buttons = {}
        for row, names in enumerate(rows):
            button_width = (width-margin*2-gap*(len(names)-1))//len(names)
            for index, name in enumerate(names):
                buttons[name] = Rect(margin+index*(button_width+gap), top+row*(button_height+6),
                                     button_width, button_height)
        latest_top = max(rect.y+rect.height for rect in buttons.values()) + 8
        return cls(width, height, video, footer, buttons, font_size, line_height, line_count,
                   plates, plate_lines, plate_sidebar, latest_lines, latest_top)

    @classmethod
    def four_panel(cls, frame_width, frame_height, width, height, show_plates, order):
        width, height = max(640, int(width)), max(720, int(height))
        gap, margin, header = 12, 12, 38
        available = width-margin*2-gap
        left = round(available*(.62 if width >= 1000 else .5))
        right = available-left
        # Match the camera aspect where possible, while reserving useful lower cards.
        top = min(height-margin*2-gap-330, max(330, round(left*frame_height/max(1, frame_width))+header))
        bottom = height-margin*2-gap-top
        slots = [Rect(margin, margin, left, top), Rect(margin+left+gap, margin, right, top),
                 Rect(margin, margin+top+gap, left, bottom),
                 Rect(margin+left+gap, margin+top+gap, right, bottom)]
        names = ("camera", "plates", "latest", "controls")
        order = (list(order) if isinstance(order, (list, tuple)) and len(order) == 4
                 and all(isinstance(name, str) for name in order) and set(order) == set(names) else list(names))
        panels = dict(zip(order, slots))
        headers = {name: Rect(rect.x, rect.y, rect.width, header) for name, rect in panels.items()}
        camera = panels["camera"]
        content = Rect(camera.x+2, camera.y+header, camera.width-4, camera.height-header-2)
        scale = min(content.width/max(1, frame_width), content.height/max(1, frame_height))
        vw, vh = max(1, round(frame_width*scale)), max(1, round(frame_height*scale))
        video = Rect(content.x+(content.width-vw)//2, content.y+(content.height-vh)//2, vw, vh)
        control = panels["controls"]
        size = max(12, min(18, control.width//24))
        line_height = size+6
        footer = Rect(control.x+12, control.y+header+4, control.width-24, control.height-header-16)
        rows = (("select", "full"), ("confirm", "cancel"), ("plates", "stop"), ("reset_layout",))
        button_height = max(32, size+20)
        button_top = control.y+control.height-12-len(rows)*(button_height+8)
        buttons = {name: Rect(control.x+12+col*((control.width-24-8*(len(names_in_row)-1))//len(names_in_row)+8),
                              button_top+row*(button_height+8),
                              (control.width-24-8*(len(names_in_row)-1))//len(names_in_row), button_height)
                   for row, names_in_row in enumerate(rows) for col, name in enumerate(names_in_row)}
        p = panels["plates"]
        plates = Rect(p.x+2, p.y+header, p.width-4, p.height-header-2) if show_plates else None
        return cls(width, height, video, footer, buttons, size, line_height, 4,
                   plates=plates, plate_sidebar=True, panels=panels, headers=headers)

    def button_at(self, x, y):
        return next((name for name, rect in self.buttons.items() if rect.contains(x, y)), None)

    def image_point(self, x, y, clamp=False):
        if not clamp and not self.video.contains(x, y):
            return None
        return (min(1.0, max(0.0, (x-self.video.x)/self.video.width)),
                min(1.0, max(0.0, (y-self.video.y)/self.video.height)))

    def canvas_point(self, point):
        """Map normalized camera coordinates into the current, resized preview."""
        return (min(self.video.x + self.video.width - 1,
                    max(self.video.x, round(self.video.x + point[0] * self.video.width))),
                min(self.video.y + self.video.height - 1,
                    max(self.video.y, round(self.video.y + point[1] * self.video.height))))

    def roi_points(self, roi):
        return [self.canvas_point(point) for point in (roi_vertices(roi) or [])]


@dataclass(frozen=True)
class UIAction:
    kind: str
    roi: object = None
    event: object = None


class PreviewController:
    def __init__(self, roi=None):
        self.roi = deepcopy(roi)
        self.layout = None
        self.selecting = False
        self.draft = None
        self.selection_error = None
        self.pressed_button = None
        self.model_label = "YOLO11n"
        self.plate_events = []
        self.plate_enabled = False
        self.plate_panel_open = False
        self.plate_filter_targets = []
        self.plate_other_expanded = False
        self.plate_status = ""
        self.plate_error = ""
        self.plate_scan_status = ""
        self.object_counts = None
        self.latest_vehicle = {}
        self.latest_vehicle_image = None
        self.latest_plate_image = None
        self.panel_order = ["camera", "plates", "latest", "controls"]
        self._panel_drag = None
        self.hover_point = None
        self.animation_time = None
        self.plate_scroll = 0
        self.plate_scroll_max = 0
        self.plate_page_lines = 1
        self.plate_scrollbar = None
        self.plate_scroll_thumb = None
        self._plate_scroll_drag = False
        self._plate_row_cache = None
        self.plate_links = []
        self.plate_group_hits = []
        self._plate_hit_state = None
        self._pressed_plate = None
        self._plate_pending_scroll = None

    @property
    def can_confirm(self):
        if not self.selecting or not self.draft or len(self.draft) < 3:
            return False
        try:
            return validate_roi(self.draft) is not None
        except (TypeError, ValueError):
            return False

    def end_selection(self):
        self.selecting, self.draft, self.selection_error = False, None, None
        self.pressed_button = None

    def command(self, name):
        if name == "reset_layout":
            self.panel_order = ["camera", "plates", "latest", "controls"]
            self._panel_drag = None
            return UIAction("layout_changed", event=list(self.panel_order))
        if name == "plates" and self.layout is not None and "plates" in self.layout.buttons:
            if self.layout.plate_sidebar:
                self.plate_other_expanded = not self.plate_other_expanded
                self._plate_pending_scroll = "other" if self.plate_other_expanded else "top"
            else:
                self.plate_panel_open = not self.plate_panel_open
            self.plate_links, self.plate_group_hits = [], []
            self.plate_scrollbar = self.plate_scroll_thumb = None
            self._plate_hit_state = self._pressed_plate = None
            self._plate_scroll_drag = False
            return None
        if name == "select":
            self.selecting, self.draft, self.selection_error = True, [], None
            self.pressed_button = None
            return UIAction("selection_started")
        if name == "stop":
            return UIAction("stop")
        if name == "full":
            self.end_selection()
            return UIAction("save", None)
        if name == "cancel" and self.selecting:
            self.end_selection()
            return UIAction("selection_cancelled")
        if name == "undo" and self.selecting:
            if self.draft:
                self.draft.pop()
            self.selection_error = None
        if name == "confirm" and self.selecting:
            if not self.can_confirm:
                self.selection_error = "vertices" if len(self.draft) < 3 else "shape"
                return None
            selection = deepcopy(validate_roi(self.draft))
            self.end_selection()
            return UIAction("save", selection)
        return None

    def key(self, key):
        if key == 27 and self._panel_drag:
            self._panel_drag = None
            return None
        # waitKeyEx keeps Win32 extended keys; X11 values support other backends.
        if key in ("page_up", 0x210000, 65365):
            self.scroll_plates(-self.plate_page_lines)
            return None
        if key in ("page_down", 0x220000, 65366):
            self.scroll_plates(self.plate_page_lines)
            return None
        if key in ("home", 0x240000, 65360):
            self.scroll_plates(-self.plate_scroll_max)
            return None
        if key in ("end", 0x230000, 65367):
            self.scroll_plates(self.plate_scroll_max)
            return None
        if key in (ord("r"), ord("R")):
            return self.command("select")
        if key in (ord("c"), ord("C")):
            return self.command("full")
        if key in (ord("q"), ord("Q")):
            return self.command("stop")
        if key in (ord("l"), ord("L")):
            return self.command("reset_layout")
        if key == 27:
            return self.command("cancel" if self.selecting else "stop")
        if key in (8, 127):
            return self.command("undo")
        if key in (10, 13, 32):
            return self.command("confirm")
        return None

    def scroll_plates(self, lines):
        self.plate_scroll = max(0, min(self.plate_scroll_max, self.plate_scroll+int(lines)))

    def _scrollbar_at(self, y):
        bar = self.plate_scrollbar
        if bar is not None:
            fraction = max(0.0, min(1.0, (y-bar.y)/max(1, bar.height-1)))
            self.plate_scroll = round(fraction*self.plate_scroll_max)

    def _plate_view_state(self):
        panel = self.layout.plates if self.layout else None
        return (panel.bounds if panel else None, self.plate_scroll, id(self.plate_events),
                tuple(plate_filter_targets(self.plate_filter_targets)), self.plate_other_expanded,
                bool(self.layout and self.layout.plate_sidebar))

    def _plate_hit(self, x, y):
        # A scroll, resize or history update invalidates old click locations
        # until the next render publishes the visible rows again.
        if self._plate_hit_state != self._plate_view_state():
            return None
        if any(rect.contains(x, y) for rect in self.plate_group_hits):
            return ("toggle_other", None)
        return next((("reveal_video", event) for rect, event in self.plate_links if rect.contains(x, y)), None)

    def mouse(self, event, x, y):
        if self.layout is None:
            return None
        self.hover_point = (x, y)
        if self.layout.panels:
            if self._panel_drag:
                if event == "up":
                    source, self._panel_drag = self._panel_drag, None
                    target = next((name for name, rect in self.layout.panels.items() if rect.contains(x, y)), None)
                    if target and target != source:
                        a, b = self.panel_order.index(source), self.panel_order.index(target)
                        self.panel_order[a], self.panel_order[b] = self.panel_order[b], self.panel_order[a]
                        self.plate_links, self.plate_group_hits = [], []
                        self._plate_hit_state = None
                        return UIAction("layout_changed", event=list(self.panel_order))
                return None
            if event == "down":
                header = next((name for name, rect in self.layout.headers.items() if rect.contains(x, y)), None)
                if header:
                    self._panel_drag = header
                    self.pressed_button = None
                    self._pressed_plate = None
                    return None
        if event in ("wheel_up", "wheel_down"):
            if self.layout.plates and self.layout.plates.contains(x, y):
                self.scroll_plates(-3 if event == "wheel_up" else 3)
            return None
        if (event == "down" and self.plate_scrollbar and self.plate_scrollbar.contains(x, y)
                and self._plate_hit_state == self._plate_view_state()):
            self.pressed_button = None
            self._pressed_plate = None
            self._plate_scroll_drag = True
            self._scrollbar_at(y)
            return None
        if self._plate_scroll_drag:
            if event in ("move", "up"):
                self._scrollbar_at(y)
            if event == "up":
                self._plate_scroll_drag = False
            return None
        panel = self.layout.plates
        if event == "up" and self._pressed_plate is not None:
            pressed, self._pressed_plate = self._pressed_plate, None
            if self._plate_hit(x, y) == pressed:
                if pressed[0] == "toggle_other":
                    self.plate_other_expanded = not self.plate_other_expanded
                    if self.layout.plate_sidebar and not self.plate_other_expanded:
                        self._plate_pending_scroll = "top"
                else:
                    return UIAction("reveal_video", event=deepcopy(pressed[1]))
            return None
        if panel is not None and panel.contains(x, y):
            if event == "down":
                self.pressed_button = None
                self._pressed_plate = self._plate_hit(x, y)
            # Panel interactions must never add/remove polygon vertices.
            return None
        if event == "down":
            self._pressed_plate = None
        if event == "right_down":
            return self.command("undo")
        if event == "down":
            self.pressed_button = self.layout.button_at(x, y)
            if self.pressed_button == "select":
                # Pause triggering immediately when selection is pressed.
                self.pressed_button = None
                return self.command("select")
            if self.pressed_button or not self.selecting:
                return None
            point = self.layout.image_point(x, y)
            if point is not None:
                self.draft.append(list(point))
                self.selection_error = None
        elif event == "up":
            if self.pressed_button:
                pressed, self.pressed_button = self.pressed_button, None
                if self.layout.button_at(x, y) == pressed:
                    return self.command(pressed)
                return None
        return None


@lru_cache(maxsize=8)
def ui_font(size):
    from PIL import ImageFont
    for path in ("C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/msjhbd.ttc",
                 "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size), True
            except OSError:
                pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size), False
    except OSError:
        return ImageFont.load_default(), False


def status_lines(controller, status, count, infer_ms, elapsed=None, message=None):
    """Keep every status and hint in the footer, including live recording counts."""
    chinese = ui_font(controller.layout.font_size)[1]
    names = {"IDLE": "偵測中", "RECORDING": "錄影中", "STARTING": "準備錄影",
             "PAUSED": "錄影暫停", "STOPPED": "已停止", "WAITING_FOR_RECORDER": "等待錄影程式",
             "WAITING_FOR_MUMU": "等待 MuMu／逍遙畫面", "CAPTURE_RETRY": "重新擷取畫面",
             "FINDING_CAMERA_AREA": "正在辨識模擬器影像範圍",
             "ADB_CONNECTING": "正在連接 ADB", "ADB_QUALITY_CHECK": "設定最高畫質並收起選單",
             "ADB_QUALITY_UNCONFIRMED": "畫質待確認，將自動重試；請保持監視器即時畫面開啟",
             "ADB_UNAVAILABLE": "ADB 尚未連上，將自動重試", "ADB_RECONNECTING": "ADB 串流重新連接中",
             "ADB_STREAMING": "等待 ADB 新影格",
             "ADB_VERIFYING_STALL": "畫面疑似停滯，正在確認攝影機更新",
             "ADB_RECOVERING": "重新開啟 SuperLive 並恢復指定頻道",
             "ADB_RECOVERY_WAIT": "頻道尚未恢復，稍後自動重試",
             "ADB_VERIFYING_CAMERA": "頻道已選定，等待影像恢復更新"}
    title = names.get(status, status) if chinese else status.replace("_", " ")
    if status == "RECORDING" and elapsed is not None:
        seconds = max(0, min(300, int(elapsed)))
        title += " %02d:%02d / 05:00" % divmod(seconds, 60)
    people = str(count) if count is not None else "--"
    inference = "%.1f ms" % infer_ms if infer_ms is not None else "--"
    if controller.roi is None:
        region = "全畫面（未設定區域）" if chinese else "Full frame (no ROI set)"
    else:
        vertices = roi_vertices(controller.roi)
        region = ("指定多邊形：%d 個頂點" if chinese else "Polygon ROI: %d vertices") % len(vertices)
    heading = ("狀態：" if chinese else "Status: ")+title
    model = str(getattr(controller, "model_label", "YOLO11n"))
    counts = ("區域人數：%s   %s：%s" if chinese else "People in ROI: %s   %s: %s") % (people, model, inference)
    object_counts = getattr(controller, "object_counts", None)
    if isinstance(object_counts, dict):
        person_count = str(object_counts.get("person", 0)) if count is not None else "--"
        vehicle_count = str(sum(object_counts.get(name, 0) for name in
                               ("car", "motorcycle", "bus", "truck"))) if count is not None else "--"
        region_counts = (("區域：人物 %s／車輛 %s" if chinese else "ROI: people %s / vehicles %s") %
                         (person_count, vehicle_count))
        counts = "%s   %s：%s" % (region_counts, model, inference)
    if controller.selecting:
        points = len(controller.draft)
        instruction = ("點擊頂點（至少 3 點），已選 %d 點" if chinese else
                       "Click vertices (at least 3); %d selected") % points
        error = controller.selection_error
        if points >= 3 and not controller.can_confirm:
            error = "shape"
        if error == "vertices":
            instruction = "請至少點選 3 個不同頂點" if chinese else "Choose at least 3 distinct vertices"
        elif error == "shape":
            instruction = "區域無效：邊不可交叉，需有面積" if chinese else "Invalid: edges must not cross; area must be positive"
        instruction = message or instruction
        if controller.layout.line_count == 3:
            shortcuts = ("Enter 儲存・右鍵 / Backspace 撤銷・Esc 取消" if chinese else
                         "Enter: save | Right-click / Backspace: undo | Esc: cancel")
            return [heading + "   |   " + counts, instruction, shortcuts]
        compact_status = (("人數：%s  %s：%s" if chinese else "People: %s  %s: %s") %
                          (people, model, inference))
        if isinstance(object_counts, dict):
            compact_status = "%s  %s：%s" % (region_counts, model, inference)
        return [heading + " | " + compact_status, instruction,
                "Enter 儲存・Esc 取消" if chinese else "Enter: save | Esc: cancel",
                "右鍵 / Backspace 撤銷" if chinese else "Right-click / Backspace: undo"]
    hint = "按鈕可點擊｜R 設定區域・C 全畫面・Q 停止" if chinese else "Click buttons | R: region  C: full frame  Q: stop"
    if status == "RECORDING":
        if elapsed is None or elapsed < 120:
            hint = ("前 2 分鐘不切段・影片傳送靜默｜滿 2 分鐘後移動即切新段" if chinese else
                    "First 2 min: no split, video quiet | Then movement starts a new clip")
        else:
            hint = ("移動即切新段｜連續 2 分鐘無移動結束｜單段最多 5 分鐘" if chinese else
                    "Movement: new clip | End after 2 min inactivity | 5 min maximum")
    if controller.layout.line_count == 3:
        return [heading + "   |   " + counts, region, message or hint]
    return [heading, counts, region, message or hint]


def plate_filter_targets(values):
    """Normalize local targets without embedding a user's private plate in code."""
    def normalize(value):
        return "".join(char for char in str(value or "").upper() if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    return sorted({normalize(value) for value in (values or []) if normalize(value)})


def _plate_timestamp(item):
    try:
        value = float(item.get("timestamp", 0))
        return value if math.isfinite(value) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def plate_event_groups(events, targets=()):
    """Use confirmed matcher evidence, never infer a match from observed text."""
    valid = [event for event in events if isinstance(event, dict) and
             (event.get("confirmed") or event.get("outcome") in ("no_plate", "error", "insufficient"))]
    targets = set(plate_filter_targets(targets))
    featured, other = [], []
    for event in sorted(valid, key=_plate_timestamp, reverse=True):
        selected = event.get("confirmed") is True
        if selected and targets:
            matched = plate_filter_targets([event.get("matched")])
            distance = event.get("distance")
            selected = (bool(matched) and matched[0] in targets and isinstance(distance, (int, float))
                        and not isinstance(distance, bool) and distance in (0, 1, 2))
        (featured if selected else other).append(event)
    return featured, other


def plate_daily_groups(events):
    """Group confirmed matches by Taiwan calendar date, newest date first."""
    groups = {}
    for event in sorted(events, key=_plate_timestamp, reverse=True):
        try:
            moment = datetime.fromtimestamp(float(event["timestamp"]), timezone(timedelta(hours=8)))
            day = moment.strftime("%Y/%m/%d")
        except (KeyError, ValueError, TypeError, OSError, OverflowError):
            day = None
        groups.setdefault(day, []).append(event)
    return list(groups.items())


def plate_record_parts(event, chinese=True):
    """Separate metadata from the clickable source filename."""
    try:
        moment = datetime.fromtimestamp(float(event["timestamp"]), timezone(timedelta(hours=8)))
        stamp = moment.strftime("%m/%d %H:%M:%S")
    except (KeyError, ValueError, TypeError, OSError, OverflowError):
        stamp = "--:--:--"
    confirmed = event.get("confirmed")
    if confirmed:
        observed = str(event.get("text") or event.get("raw_text") or "?")
    else:
        names = ({"no_plate": "未辨識到車牌", "error": "辨識失敗", "insufficient": "有候選但未確認"}
                 if chinese else {"no_plate": "No plate recognized", "error": "Recognition failed",
                                  "insufficient": "Candidate not confirmed"})
        observed = names[event["outcome"]]
    parts = [("observed", "%s  %s" % (stamp, observed))]
    if confirmed and event.get("matched"):
        parts.append(("match", (("名單：%s · 差 %s 碼" if chinese else "Match: %s · distance %s") %
                               (event["matched"], event.get("distance", "?")))))
    elif confirmed:
        parts.append(("match", "未符合指定名單" if chinese else "No allowlist match"))
    video = str(event.get("source_video") or "").replace("\\", "/").rsplit("/", 1)[-1]
    if event.get("source") == "recording_replay" or video:
        source = (("原片補辨識" if chinese else "Recording replay") if event.get("source") == "recording_replay"
                  else ("錄影片段" if chinese else "Recording"))
        parts.append(("detail", source))
    if video:
        parts.append(("video", video))
    if not confirmed and event.get("error"):
        parts.append(("error", ("原因：" if chinese else "Reason: ") + str(event["error"])))
    return parts


def plate_rows(events, chinese=True):
    """Plain-text representation of all retained rows, newest first."""
    featured, other = plate_event_groups(events)
    combined = featured+other
    # Keep this legacy text helper independent of the view's collapsed groups.
    combined.sort(key=_plate_timestamp, reverse=True)
    rows = []
    for event in combined:
        parts = plate_record_parts(event, chinese)
        details = []
        for kind, text in parts[1:]:
            if kind == "video" and details:
                details[-1] += "："+text
            else:
                details.append(text)
        rows.append((parts[0][1], "\n".join(details)))
    return rows


def wrap_panel_text(draw, text, font, width):
    """Wrap Chinese or unbroken identifiers using the rendered font width."""
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        if not paragraph:
            lines.append("")
        while paragraph:
            # Binary-search a fitting prefix rather than measuring each added
            # character, so a week's history remains inexpensive to wrap.
            low, high = 1, len(paragraph)
            while low < high:
                middle = (low+high+1)//2
                box = draw.textbbox((0, 0), paragraph[:middle], font=font)
                if box[2]-box[0] <= width:
                    low = middle
                else:
                    high = middle-1
            lines.append(paragraph[:low].rstrip())
            paragraph = paragraph[low:].lstrip()
    return lines


def plate_status_parts(controller, chinese=True):
    """Separate OCR health, source and quality instead of hiding the last part."""
    parts = [part.strip() for part in str(controller.plate_status).split("|") if part.strip()]
    if not parts:
        parts = ["等待車牌辨識" if chinese else "Waiting for plate detection"]
    names = {"READY": "車牌辨識中", "STARTING": "車牌模型載入中", "PAUSED": "車牌辨識等待畫面",
             "ERROR": "車牌辨識錯誤", "UNAVAILABLE": "車牌辨識無法使用",
             "RESTARTING": "車牌辨識重新啟動中", "RETRYING": "車牌辨識重試中",
             "STOPPED": "車牌辨識已停止"}
    if chinese:
        parts[0] = names.get(parts[0].upper(), parts[0])
    scan_status = str(getattr(controller, "plate_scan_status", "") or "").strip()
    if scan_status:
        parts.append(scan_status)
    error = str(getattr(controller, "plate_error", "") or "").strip()
    return parts, (("原因：" if chinese else "Reason: ") + error if error else "")


def plate_footer_text(controller, chinese=True):
    """Compact live health/source/progress text; history headings stay hidden."""
    parts, error = plate_status_parts(controller, chinese)
    text = " · ".join(part.strip() for value in parts for part in value.splitlines() if part.strip())
    return text + ("\n"+error if error else "")


def latest_vehicle_footer_text(controller):
    return latest_vehicle_text(controller.latest_vehicle,
                               ui_font(controller.layout.font_size if controller.layout is not None else 18)[1])


@lru_cache(maxsize=128)
def plate_footer_lines(text, width, font_size):
    """Measure before layout so errors gain rows without covering controls."""
    if not str(text).strip():
        return ()
    font = ui_font(font_size)[0]
    class Measure:
        def textbbox(self, position, value, font):
            return font.getbbox(value)
    return tuple(wrap_panel_text(Measure(), text, font, width-24))


def render_plate_panel(draw, controller, font, chinese):
    panel = controller.layout.plates
    if panel is None:
        controller.plate_links, controller.plate_group_hits = [], []
        controller.plate_scrollbar = controller.plate_scroll_thumb = None
        controller._plate_hit_state = controller._pressed_plate = None
        controller._plate_scroll_drag = False
        return
    draw.rectangle(panel.bounds, fill=(15, 24, 35))
    draw.line((panel.x, panel.y, panel.x+panel.width, panel.y), fill=(52, 93, 123), width=2)
    if panel.x:
        draw.line((panel.x, panel.y, panel.x, panel.y+panel.height), fill=(52, 93, 123), width=2)
    size = controller.layout.font_size
    line = size+6
    text_width = panel.width-42
    colors = dict(title=(106, 224, 251), detail=(160, 179, 198), error=(255, 172, 141),
                  observed=(239, 247, 252), match=(155, 202, 176), video=(105, 184, 255))
    # The history store replaces the list when data changes. Cache its wrapped
    # rows independently of rapidly changing capture/status text.
    events = controller.plate_events
    cache = controller._plate_row_cache
    targets = plate_filter_targets(controller.plate_filter_targets)
    sidebar = controller.layout.plate_sidebar
    cache_key = (len(events), text_width, size, chinese, tuple(targets), controller.plate_other_expanded, sidebar)
    if cache is None or cache[0] is not events or cache[1] != cache_key:
        featured, other = plate_event_groups(events, targets)
        row_lines = []
        def paragraph(value, color, action=None):
            row_lines.extend((text, color, action) for text in wrap_panel_text(draw, value, font, text_width))
        def append_records(records):
            for record in records:
                for kind, value in plate_record_parts(record, chinese):
                    paragraph(value, colors[kind], ("reveal_video", record) if kind == "video" else None)
                row_lines.append(("", colors["detail"], None))
        if sidebar and targets:
            paragraph(("指定車牌：" if chinese else "Target plates: ")+" / ".join(targets), colors["title"])
        if featured:
            label = (("符合指定車牌（%d 筆）" if targets else "已確認車牌（%d 筆）") if chinese else
                     ("Matching plates · %d records" if targets else "Confirmed plates · %d records"))
            paragraph(label % len(featured), colors["title"])
            if sidebar:
                for day, records in plate_daily_groups(featured):
                    day = day or ("日期不明" if chinese else "Unknown date")
                    paragraph(("%s · %d 筆" if chinese else "%s · %d records") % (day, len(records)),
                              colors["match"])
                    append_records(records)
            else:
                append_records(featured)
        elif events:
            paragraph(("尚無符合指定車牌的確認紀錄" if targets else "尚無已確認的車牌") if chinese else
                      ("No confirmed target matches" if targets else "No confirmed plates"), colors["detail"])
        other_row = len(row_lines)
        if other:
            label = ("其他辨識紀錄（%d 筆）" if chinese else "Other recognition records · %d") % len(other)
            paragraph(("[-] " if controller.plate_other_expanded else "[+] ")+label,
                      colors["video"], ("toggle_other", None))
            if controller.plate_other_expanded:
                append_records(other)
        elif sidebar:
            paragraph("其他辨識紀錄（0 筆）" if chinese else "Other recognition records · 0", colors["detail"])
        controller._plate_row_cache = (events, cache_key, row_lines, len(featured)+len(other), other_row)
    _, _, row_lines, record_count, other_row = controller._plate_row_cache
    heading = (("最近 7 天辨識紀錄（%d 筆）" if chinese else "Last 7 days · %d records") % record_count)
    help_text = "滾輪 / PgUp / PgDn · 點擊捲軸" if chinese else "Wheel / PgUp / PgDn · scrollbar"
    title_lines = wrap_panel_text(draw, heading, font, text_width)
    title_lines += wrap_panel_text(draw, help_text, font, text_width)
    y = panel.y+8
    for text in title_lines:
        draw.text((panel.x+12, y), text, font=font, fill=colors["title"])
        y += line
    top = y+8
    controller.plate_page_lines = max(1, (panel.y+panel.height-8-top)//line)

    entries = list(row_lines)
    if not record_count:
        empty = "尚無辨識紀錄" if chinese else "No recognition records yet"
        entries.extend((text, colors["detail"], None) for text in wrap_panel_text(draw, empty, font, text_width))
    controller.plate_scroll_max = max(0, len(entries)-controller.plate_page_lines)
    if controller._plate_pending_scroll is not None:
        controller.plate_scroll = other_row if controller._plate_pending_scroll == "other" else 0
        controller._plate_pending_scroll = None
    controller.scroll_plates(0)
    start = controller.plate_scroll
    controller.plate_links, controller.plate_group_hits = [], []
    for index, (text, color, action) in enumerate(entries[start:start+controller.plate_page_lines]):
        box = draw.textbbox((0, 0), text, font=font)
        position = top+index*line
        if position+box[3] <= panel.y+panel.height-4:
            draw.text((panel.x+12, position), text, font=font, fill=color)
            if action:
                hit = Rect(panel.x+12, position, max(1, box[2]-box[0]), line)
                if action[0] == "reveal_video":
                    draw.line((hit.x, position+box[3], hit.x+hit.width, position+box[3]), fill=color)
                    controller.plate_links.append((hit, action[1]))
                else:
                    controller.plate_group_hits.append(hit)

    track = Rect(panel.x+panel.width-19, top, 11, controller.plate_page_lines*line)
    thumb_height = max(20, round(track.height*min(1, controller.plate_page_lines/max(1, len(entries)))))
    thumb_y = track.y + round((track.height-thumb_height)*start/max(1, controller.plate_scroll_max))
    thumb = Rect(track.x, thumb_y, track.width, thumb_height)
    controller.plate_scrollbar, controller.plate_scroll_thumb = track, thumb
    controller._plate_hit_state = controller._plate_view_state()
    draw.rounded_rectangle(track.bounds, radius=4, fill=(27, 44, 60))
    draw.rounded_rectangle(thumb.bounds, radius=4, fill=(85, 169, 193) if controller.plate_scroll_max else (63, 92, 111))


def _dashboard_photo(image, source, rect, contain=True):
    """Display the full source on a soft backdrop without cropping evidence."""
    from PIL import Image, ImageOps, ImageFilter
    backdrop = source.resize((48, 32)).filter(ImageFilter.GaussianBlur(5))
    backdrop = Image.blend(backdrop, Image.new("RGB", backdrop.size, (18, 32, 49)), .65)
    image.paste(backdrop.resize((rect.width, rect.height), Image.Resampling.BILINEAR), (rect.x, rect.y))
    if contain:
        fitted = ImageOps.contain(source, (rect.width, rect.height), Image.Resampling.BILINEAR)
        image.paste(fitted, (rect.x+(rect.width-fitted.width)//2, rect.y+(rect.height-fitted.height)//2))


def render_dashboard(frame, controller, status, count=None, infer_ms=None, elapsed=None, message=None):
    import numpy as np
    from PIL import Image, ImageDraw
    layout = controller.layout
    now = controller.animation_time if controller.animation_time is not None else time.monotonic()
    pulse = (math.sin(now*2.5)+1)/2
    image = Image.new("RGB", (layout.width, layout.height), (10, 17, 29))
    draw = ImageDraw.Draw(image)
    font, chinese = ui_font(layout.font_size)
    title_font, _ = ui_font(17 if layout.width >= 1000 else 14)
    small, _ = ui_font(max(12, layout.font_size-2))
    titles = ({"camera": "01  即時監視", "plates": "02  車牌紀錄", "latest": "03  最近車輛", "controls": "04  操作中心"}
              if chinese else {"camera": "01  LIVE CAMERA", "plates": "02  PLATE HISTORY", "latest": "03  LATEST VEHICLE", "controls": "04  CONTROLS"})
    for name, panel in layout.panels.items():
        draw.rounded_rectangle((panel.x+2, panel.y+4, panel.x+panel.width+2, panel.y+panel.height+4), 14, fill=(5, 10, 18))
        edge = (45, 76, 102)
        if name == "camera" and status == "RECORDING":
            edge = (int(140+90*pulse), 72, 83)
        if controller._panel_drag and controller.hover_point and panel.contains(*controller.hover_point):
            edge = (89, 225, 235)
        draw.rounded_rectangle(panel.bounds, 14, fill=(17, 29, 45), outline=edge, width=2)
        draw.text((panel.x+14, panel.y+9), titles[name], font=title_font, fill=(200, 229, 244))
        for dx in (0, 5):
            for dy in (0, 5, 10):
                x, y = panel.x+panel.width-27+dx, panel.y+13+dy
                draw.ellipse((x, y, x+2, y+2), fill=(100, 155, 180))
    camera = layout.panels["camera"]
    content = Rect(camera.x+2, camera.y+38, camera.width-4, camera.height-40)
    source = Image.fromarray(frame[:, :, ::-1])
    _dashboard_photo(image, source, content, contain=False)
    image.paste(source.resize((layout.video.width, layout.video.height), Image.Resampling.BILINEAR),
                (layout.video.x, layout.video.y))
    draw = ImageDraw.Draw(image)
    if controller.roi is not None:
        points = layout.roi_points(controller.roi)
        draw.line(points+points[:1], fill=(66, 219, 224), width=2)
    if controller.selecting and controller.draft:
        points = [layout.canvas_point(point) for point in controller.draft]
        color = (255, 192, 80) if controller.can_confirm or len(points) < 3 else (255, 117, 112)
        if len(points) > 1:
            draw.line(points+(points[:1] if len(points) >= 3 else []), fill=color, width=3)
        for x, y in points:
            draw.ellipse((x-4, y-4, x+4, y+4), fill=color)
    if status == "RECORDING":
        x, y = camera.x+camera.width-75, camera.y+19
        draw.ellipse((x-3, y-3, x+3, y+3), fill=(int(160+90*pulse), 89, 104))
        draw.text((x+8, y-9), "REC", font=small, fill=(255, 162, 170))
    render_plate_panel(draw, controller, font, chinese)
    if layout.plates is None:
        p = layout.panels["plates"]
        draw.text((p.x+16, p.y+54), "車牌辨識未啟用" if chinese else "Plate recognition disabled", font=font, fill=(142, 164, 185))
    # The saved crop and its own timestamp are separate from current camera pixels.
    p = layout.panels["latest"]
    area = Rect(p.x+12, p.y+48, p.width-24, p.height-60)
    photo = controller.latest_vehicle_image
    record = controller.latest_vehicle
    meta_height = 78 if area.width < 450 else 90
    picture = Rect(area.x, area.y, int((area.width-10)*.57), max(28, area.height-meta_height))
    plate_picture = Rect(picture.x+picture.width+10, picture.y,
                         area.width-picture.width-10, picture.height)
    if photo is not None and getattr(photo, "size", 0):
        _dashboard_photo(image, Image.fromarray(photo[:, :, ::-1]), picture)
        draw = ImageDraw.Draw(image)
    else:
        draw.rounded_rectangle(picture.bounds, 9, fill=(24, 42, 60), outline=(43, 69, 89))
        label = "等待車輛影像" if chinese else "Waiting for vehicle"
        for i, line in enumerate(wrap_panel_text(draw, label, small, picture.width-12)):
            draw.text((picture.x+6, picture.y+12+i*22), line, font=small, fill=(139, 177, 195))
    plate_photo = controller.latest_plate_image
    draw.rounded_rectangle(plate_picture.bounds, 9, fill=(24, 42, 60), outline=(43, 69, 89))
    label = "車牌放大" if chinese else "Plate close-up"
    draw.text((plate_picture.x+6, plate_picture.y+6), label, font=small, fill=(139, 207, 220))
    plate_pixels = Rect(plate_picture.x+5, plate_picture.y+32, plate_picture.width-10,
                        max(20, plate_picture.height-72))
    if plate_photo is not None and getattr(plate_photo, 'size', 0):
        _dashboard_photo(image, Image.fromarray(plate_photo[:, :, ::-1]), plate_pixels)
        draw = ImageDraw.Draw(image)
    else:
        label = "尚未取得車牌影像" if chinese else "No plate image yet"
        for i, line in enumerate(wrap_panel_text(draw, label, small, plate_pixels.width-4)):
            draw.text((plate_pixels.x+2, plate_pixels.y+8+i*20), line, font=small, fill=(139, 177, 195))
    plate_label = str(record.get('plate_display') or record.get('plate') or '--')
    plate_font = ui_font(26)[0]
    for size in range(26, 10, -1):
        plate_font = ui_font(size)[0]
        if draw.textbbox((0, 0), plate_label, font=plate_font)[2] <= plate_picture.width-12:
            break
    draw.text((plate_picture.x+6, plate_picture.y+plate_picture.height-34), plate_label,
              font=plate_font, fill=(155, 237, 199))
    stamp = record.get("timestamp")
    date = (datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime("%Y/%m/%d %H:%M:%S")
            if isinstance(stamp, (int, float)) and math.isfinite(stamp) and stamp > 0 else "--")
    lines = [("最近偵測  " if chinese else "Last seen  ")+date]
    if record.get("plate"):
        lines.append(("人工校正  " if chinese and record.get('user_corrected') else
                      'Reviewed  ' if record.get('user_corrected') else '車牌  ' if chinese else 'Plate  ')+plate_label)
    photo_stamp = record.get("image_timestamp")
    if photo is not None and isinstance(photo_stamp, (int, float)):
        lines.append(("影像時間  " if chinese else "Image captured  ")+
                     datetime.fromtimestamp(photo_stamp, timezone(timedelta(hours=8))).strftime("%H:%M:%S"))
    for index, text in enumerate(lines):
        draw.text((area.x+2, picture.y+picture.height+8+index*22), text,
                  font=font if index < 2 else small, fill=(155, 227, 199) if index == 1 else (182, 207, 223))
    # All actions stay in the controls card, including layout reset.
    control = layout.panels["controls"]
    status_bottom = min(rect.y for rect in layout.buttons.values())-6
    lines = status_lines(controller, status, count, infer_ms, elapsed, message)
    if controller.selecting:
        lines = [lines[1], lines[0]]  # Selection errors must remain visible in short cards.
    elif message:
        lines = [message, lines[0]]
    text_y = control.y+44
    for line in lines:
        for text in wrap_panel_text(draw, line, small, control.width-28):
            if text_y+layout.line_height > status_bottom:
                break
            draw.text((control.x+14, text_y), text, font=small, fill=(157, 206, 226))
            text_y += layout.line_height
        if text_y+layout.line_height > status_bottom:
            break
    labels = ({"select": "R  設定區域", "full": "C  全畫面", "stop": "Q  停止",
               "confirm": "Enter  儲存", "cancel": "Esc  取消", "plates": "收起其他" if controller.plate_other_expanded else "其他紀錄",
               "reset_layout": "L  還原四區排列"} if chinese else
              {"select": "R  Set region", "full": "C  Full frame", "stop": "Q  Stop",
               "confirm": "Enter  Save", "cancel": "Esc  Cancel", "plates": "Hide others" if controller.plate_other_expanded else "Other records",
               "reset_layout": "L  Reset layout"})
    for name, rect in layout.buttons.items():
        enabled = name not in ("confirm", "cancel") or (controller.can_confirm if name == "confirm" else controller.selecting)
        if name == "plates" and not controller.plate_enabled:
            enabled = False
        hover = controller.hover_point and rect.contains(*controller.hover_point)
        color = (34, 66, 86) if enabled else (25, 36, 50)
        if name == "stop":
            color = (106, 45, 58)
        elif name == "select" and controller.selecting:
            color = (21, 100, 114)
        if hover and enabled:
            color = tuple(min(255, value+18) for value in color)
        draw.rounded_rectangle(rect.bounds, 8, fill=color,
                               outline=(92, 190, 211) if hover and enabled else (48, 79, 101))
        text = labels[name]
        button_font = font
        for size in range(layout.font_size, 10, -1):
            button_font = ui_font(size)[0]
            if draw.textbbox((0, 0), text, font=button_font)[2] <= rect.width-12:
                break
        box = draw.textbbox((0, 0), text, font=button_font)
        draw.text((rect.x+(rect.width-(box[2]-box[0]))/2, rect.y+(rect.height-(box[3]-box[1]))/2-box[1]),
                  text, font=button_font, fill=(230, 242, 250) if enabled else (99, 120, 140))
    if controller._panel_drag:
        p = layout.panels[controller._panel_drag]
        draw.rounded_rectangle(p.bounds, 14, outline=(95, 232, 237), width=3)
    return np.array(image)[:, :, ::-1].copy()


def render_preview(frame, controller, status, count=None, infer_ms=None, elapsed=None, message=None):
    """Return a BGR canvas; never change the source/evidence frame."""
    import numpy as np
    from PIL import Image, ImageDraw
    layout = controller.layout
    if layout.panels:
        return render_dashboard(frame, controller, status, count, infer_ms, elapsed, message)
    image = Image.new("RGB", (layout.width, layout.height), (9, 14, 21))
    source = Image.fromarray(frame[:, :, ::-1]).resize((layout.video.width, layout.video.height), Image.Resampling.BILINEAR)
    image.paste(source, (layout.video.x, layout.video.y))
    draw = ImageDraw.Draw(image)
    if controller.roi is not None:
        points = layout.roi_points(controller.roi)
        draw.line(points + points[:1], fill=(35, 206, 236), width=2)
    if controller.selecting and controller.draft:
        points = [layout.canvas_point(point) for point in controller.draft]
        color = ((255, 117, 112) if len(points) >= 3 and not controller.can_confirm else (255, 208, 80))
        if len(points) > 1:
            draw.line(points + (points[:1] if len(points) >= 3 else []), fill=color, width=3)
        for x, y in points:
            draw.ellipse((x-4, y-4, x+4, y+4), fill=color, outline=(20, 30, 44), width=1)
    draw.rectangle(layout.footer.bounds, fill=(20, 30, 44))
    draw.line((0, layout.footer.y, layout.width, layout.footer.y), fill=(52, 93, 123), width=2)
    font, chinese = ui_font(layout.font_size)
    render_plate_panel(draw, controller, font, chinese)
    for index, text in enumerate(status_lines(controller, status, count, infer_ms, elapsed, message)):
        # Fit narrow windows without pushing text outside the single footer.
        while len(text) > 1 and draw.textbbox((0, 0), text, font=font)[2] > layout.width-24:
            text = text[:-2].rstrip("…") + "…"
        draw.text((12, layout.footer.y+8+index*layout.line_height), text, font=font,
                  fill=(106, 224, 251) if index == 0 else (224, 233, 242))
    for index, text in enumerate(layout.plate_status_lines, start=layout.line_count):
        draw.text((12, layout.footer.y+8+index*layout.line_height), text, font=font,
                  fill=(255, 172, 141) if text.startswith(("原因：", "Reason:")) else (160, 179, 198))
    labels = ({"select": "R 設定區域", "full": "C 全畫面", "stop": "Q 停止",
               "confirm": "Enter 儲存", "cancel": "Esc 取消",
               "plates": "收起紀錄" if controller.plate_panel_open else "車牌紀錄"} if chinese else
              {"select": "R: Region", "full": "C: Full frame", "stop": "Q: Stop",
               "confirm": "Enter: Save", "cancel": "Esc: Cancel",
               "plates": "Hide plates" if controller.plate_panel_open else "Plate history"})
    if layout.plate_sidebar:
        labels["plates"] = (("收起其他" if controller.plate_other_expanded else "其他紀錄") if chinese else
                            ("Hide others" if controller.plate_other_expanded else "Other records"))
    for name, rect in layout.buttons.items():
        enabled = (name not in ("confirm", "cancel") or
                   (controller.selecting if name == "cancel" else controller.can_confirm))
        color = (36, 65, 88) if enabled else (30, 39, 51)
        if name == "select" and controller.selecting:
            color = (25, 103, 119)
        if name == "plates" and (controller.plate_other_expanded if layout.plate_sidebar else controller.plate_panel_open):
            color = (25, 103, 119)
        if name == "stop":
            color = (115, 49, 58)
        draw.rounded_rectangle(rect.bounds, radius=5, fill=color, outline=(75, 118, 146) if enabled else (43, 54, 67))
        text = labels[name]
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((rect.x+(rect.width-(box[2]-box[0]))/2, rect.y+(rect.height-(box[3]-box[1]))/2-box[1]),
                  text, font=font, fill=(239, 247, 252) if enabled else (106, 122, 139))
    for index, text in enumerate(layout.latest_vehicle_lines):
        draw.text((12, layout.latest_vehicle_top+index*layout.line_height), text, font=font,
                  fill=(155, 220, 183))
    return np.array(image)[:, :, ::-1].copy()
