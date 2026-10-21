"""One preview canvas with a bottom control bar and in-window ROI selection.

Geometry and interaction have no OpenCV/Pillow dependency, so they can be tested
without a camera or desktop. Rendering imports Pillow only when needed.
"""
from dataclasses import dataclass
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timedelta, timezone

from region_policy import roi_vertices, validate_roi


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

    @classmethod
    def create(cls, frame_width, frame_height, width=960, height=720, show_plates=False):
        width = max(320, int(width))
        minimum_height = (600 if width < 800 else 480) if show_plates else 300
        height = max(minimum_height, int(height))
        font_size = max(12, min(18, width // 52))
        line_height = font_size + 6
        button_height = font_size + 20
        line_count = 3 if width >= 640 else 4
        button_rows = 1 if width >= 640 else 2
        footer_height = max(130, 24 + line_height * line_count + (button_height + 6) * button_rows)
        image_height = height - footer_height
        image_width = width
        plates = None
        if show_plates:
            if width >= 800:
                panel_width = min(360, max(280, width // 3))
                image_width -= panel_width
                plates = Rect(image_width, 0, panel_width, image_height)
            else:
                # Stacked layouts leave a readable viewport for scrollable
                # capture status and the retained recognition history.
                panel_height = min(20*(font_size+4)+24, image_height - 60)
                plates = Rect(0, image_height-panel_height, width, panel_height)
                image_height -= panel_height
        scale = min(image_width / max(1, frame_width), image_height / max(1, frame_height))
        video_width = max(1, int(frame_width * scale))
        video_height = max(1, int(frame_height * scale))
        video = Rect((image_width-video_width)//2, (image_height-video_height)//2, video_width, video_height)
        footer = Rect(0, height-footer_height, width, footer_height)
        margin, gap = 12, 8
        top = footer.y + 12 + line_height * line_count
        names = ("select", "full", "stop", "confirm", "cancel") if width >= 640 else ("select", "full", "stop")
        button_width = (width-margin*2-gap*(len(names)-1))//len(names)
        buttons = {name: Rect(margin + index*(button_width+gap), top, button_width, button_height)
                   for index, name in enumerate(names)}
        if width < 640:
            second_width = (width-margin*2-gap)//2
            for index, name in enumerate(("confirm", "cancel")):
                buttons[name] = Rect(margin+index*(second_width+gap), top+button_height+6, second_width, button_height)
        return cls(width, height, video, footer, buttons, font_size, line_height, line_count, plates)

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
        self.plate_status = ""
        self.plate_error = ""
        self.plate_scan_status = ""
        self.object_counts = None
        self.plate_scroll = 0
        self.plate_scroll_max = 0
        self.plate_page_lines = 1
        self.plate_scrollbar = None
        self.plate_scroll_thumb = None
        self._plate_scroll_drag = False
        self._plate_row_cache = None

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

    def mouse(self, event, x, y):
        if self.layout is None:
            return None
        if event in ("wheel_up", "wheel_down"):
            if self.layout.plates and self.layout.plates.contains(x, y):
                self.scroll_plates(-3 if event == "wheel_up" else 3)
            return None
        if event == "down" and self.plate_scrollbar and self.plate_scrollbar.contains(x, y):
            self.pressed_button = None
            self._plate_scroll_drag = True
            self._scrollbar_at(y)
            return None
        if self._plate_scroll_drag:
            if event in ("move", "up"):
                self._scrollbar_at(y)
            if event == "up":
                self._plate_scroll_drag = False
            return None
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
             "ADB_STREAMING": "等待 ADB 新影格"}
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
            hint = ("前 2 分鐘不切段・Telegram 靜默｜滿 2 分鐘後移動即切新段" if chinese else
                    "First 2 min: no split, Telegram quiet | Then movement starts a new clip")
        else:
            hint = ("移動即切新段｜連續 2 分鐘無移動結束｜單段最多 5 分鐘" if chinese else
                    "Movement: new clip | End after 2 min inactivity | 5 min maximum")
    if controller.layout.line_count == 3:
        return [heading + "   |   " + counts, region, message or hint]
    return [heading, counts, region, message or hint]


def plate_rows(events, chinese=True):
    """Format retained recognition history without inventing OCR for failures.

    Retention belongs to the history store. This view renders all supplied
    entries, including completed replay scans without a confirmed plate.
    """
    rows = []
    def timestamp(item):
        try:
            return float(item.get("timestamp", 0))
        except (TypeError, ValueError, OverflowError):
            return 0
    valid = [event for event in events if isinstance(event, dict) and
             (event.get("confirmed") or event.get("outcome") in ("no_plate", "error", "insufficient"))]
    for event in sorted(valid, key=timestamp, reverse=True):
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
        first = "%s  %s" % (stamp, observed)
        if confirmed and event.get("matched"):
            second = (("名單：%s · 差 %s 碼" if chinese else "Match: %s · distance %s") %
                      (event["matched"], event.get("distance", "?")))
        elif confirmed:
            second = "未符合指定名單" if chinese else "No allowlist match"
        else:
            second = ""
        video = str(event.get("source_video") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if event.get("source") == "recording_replay" or video:
            source = (("原片補辨識" if chinese else "Recording replay") if event.get("source") == "recording_replay"
                      else ("錄影片段" if chinese else "Recording"))
            second += ("\n" if second else "") + source + ("："+video if video else "")
        if not confirmed and event.get("error"):
            second += ("\n" if second else "") + ("原因：" if chinese else "Reason: ") + str(event["error"])
        rows.append((first, second))
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


def render_plate_panel(draw, controller, font, chinese):
    panel = controller.layout.plates
    if panel is None:
        return
    draw.rectangle(panel.bounds, fill=(15, 24, 35))
    draw.line((panel.x, panel.y, panel.x, panel.y+panel.height), fill=(52, 93, 123), width=2)
    size = controller.layout.font_size
    line = size+6
    text_width = panel.width-42
    colors = dict(title=(106, 224, 251), detail=(160, 179, 198), error=(255, 172, 141),
                  observed=(239, 247, 252), match=(155, 202, 176))
    # The history store replaces the list when data changes. Cache its wrapped
    # rows independently of rapidly changing capture/status text.
    events = controller.plate_events
    cache = controller._plate_row_cache
    cache_key = (len(events), text_width, size, chinese)
    if cache is None or cache[0] is not events or cache[1] != cache_key:
        rows = plate_rows(events, chinese)
        row_lines = []
        for observed, match in rows:
            row_lines.extend((text, colors["observed"]) for text in wrap_panel_text(draw, observed, font, text_width))
            if match:
                row_lines.extend((text, colors["match"]) for text in wrap_panel_text(draw, match, font, text_width))
            row_lines.append(("", colors["detail"]))
        controller._plate_row_cache = (events, cache_key, row_lines, len(rows))
    _, _, row_lines, record_count = controller._plate_row_cache
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

    parts, error = plate_status_parts(controller, chinese)
    entries = []
    for part in parts:
        entries.extend((text, colors["detail"]) for text in wrap_panel_text(draw, part, font, text_width))
    if error:
        entries.extend((text, colors["error"]) for text in wrap_panel_text(draw, error, font, text_width))
    entries.append(("", colors["detail"]))
    entries.extend(row_lines)
    if not record_count:
        empty = "尚無辨識紀錄" if chinese else "No recognition records yet"
        entries.extend((text, colors["detail"]) for text in wrap_panel_text(draw, empty, font, text_width))
    controller.plate_scroll_max = max(0, len(entries)-controller.plate_page_lines)
    controller.scroll_plates(0)
    start = controller.plate_scroll
    for index, (text, color) in enumerate(entries[start:start+controller.plate_page_lines]):
        box = draw.textbbox((0, 0), text, font=font)
        position = top+index*line
        if position+box[3] <= panel.y+panel.height-4:
            draw.text((panel.x+12, position), text, font=font, fill=color)

    track = Rect(panel.x+panel.width-19, top, 11, controller.plate_page_lines*line)
    thumb_height = max(20, round(track.height*min(1, controller.plate_page_lines/max(1, len(entries)))))
    thumb_y = track.y + round((track.height-thumb_height)*start/max(1, controller.plate_scroll_max))
    thumb = Rect(track.x, thumb_y, track.width, thumb_height)
    controller.plate_scrollbar, controller.plate_scroll_thumb = track, thumb
    draw.rounded_rectangle(track.bounds, radius=4, fill=(27, 44, 60))
    draw.rounded_rectangle(thumb.bounds, radius=4, fill=(85, 169, 193) if controller.plate_scroll_max else (63, 92, 111))


def render_preview(frame, controller, status, count=None, infer_ms=None, elapsed=None, message=None):
    """Return a BGR canvas; never change the source/evidence frame."""
    import numpy as np
    from PIL import Image, ImageDraw
    layout = controller.layout
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
    labels = ({"select": "R 設定區域", "full": "C 全畫面", "stop": "Q 停止",
               "confirm": "Enter 儲存", "cancel": "Esc 取消"} if chinese else
              {"select": "R: Region", "full": "C: Full frame", "stop": "Q: Stop",
               "confirm": "Enter: Save", "cancel": "Esc: Cancel"})
    for name, rect in layout.buttons.items():
        enabled = (name not in ("confirm", "cancel") or
                   (controller.selecting if name == "cancel" else controller.can_confirm))
        color = (36, 65, 88) if enabled else (30, 39, 51)
        if name == "select" and controller.selecting:
            color = (25, 103, 119)
        if name == "stop":
            color = (115, 49, 58)
        draw.rounded_rectangle(rect.bounds, radius=5, fill=color, outline=(75, 118, 146) if enabled else (43, 54, 67))
        text = labels[name]
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((rect.x+(rect.width-(box[2]-box[0]))/2, rect.y+(rect.height-(box[3]-box[1]))/2-box[1]),
                  text, font=font, fill=(239, 247, 252) if enabled else (106, 122, 139))
    return np.array(image)[:, :, ::-1].copy()
