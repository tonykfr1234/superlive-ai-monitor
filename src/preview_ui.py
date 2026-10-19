"""One preview canvas with a bottom control bar and in-window ROI selection.

Geometry and interaction have no OpenCV/Pillow dependency, so they can be tested
without a camera or desktop. Rendering imports Pillow only when needed.
"""
from dataclasses import dataclass
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

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

    @classmethod
    def create(cls, frame_width, frame_height, width=960, height=720):
        width, height = max(320, int(width)), max(300, int(height))
        font_size = max(12, min(18, width // 52))
        line_height = font_size + 6
        button_height = font_size + 20
        line_count = 3 if width >= 640 else 4
        button_rows = 1 if width >= 640 else 2
        footer_height = max(130, 24 + line_height * line_count + (button_height + 6) * button_rows)
        image_height = height - footer_height
        scale = min(width / max(1, frame_width), image_height / max(1, frame_height))
        video_width = max(1, int(frame_width * scale))
        video_height = max(1, int(frame_height * scale))
        video = Rect((width-video_width)//2, (image_height-video_height)//2, video_width, video_height)
        footer = Rect(0, image_height, width, footer_height)
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
        return cls(width, height, video, footer, buttons, font_size, line_height, line_count)

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

    def mouse(self, event, x, y):
        if self.layout is None:
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
             "FINDING_CAMERA_AREA": "正在辨識模擬器影像範圍"}
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
    counts = ("區域人數：%s   YOLO：%s" if chinese else "People in ROI: %s   YOLO: %s") % (people, inference)
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
        compact_status = (("人數：%s  YOLO：%s" if chinese else "People: %s  YOLO: %s") %
                          (people, inference))
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
