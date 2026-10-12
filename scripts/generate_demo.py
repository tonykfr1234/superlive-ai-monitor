"""Generate synthetic documentation media using the actual preview renderer.

No camera, model, credentials, network, or Telegram client is used. Person boxes
and event states are scripted to explain the workflow, not to benchmark YOLO.
Run: python scripts/generate_demo.py (Pillow, numpy, imageio-ffmpeg required).
The demo has bilingual captions, no audio, and deliberately accelerated time.
"""
from pathlib import Path
import math
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from preview_ui import PreviewController, PreviewLayout, render_preview

OUT = ROOT / "docs" / "media"
WIDTH, HEIGHT, FPS, DURATION = 1440, 1000, 12, 36
BG = "#0a1220"
INK = "#edf5ff"
MUTED = "#9bacc3"
CYAN = "#4ddce8"
GREEN = "#7de3b5"
AMBER = "#ffd077"
ROI = [[.26, .20], [.70, .20], [.88, .86], [.56, .92], [.40, .65], [.16, .65]]


def font(size, bold=False):
    candidates = (["C:/Windows/Fonts/msjhbd.ttc"] if bold else []) + [
        "C:/Windows/Fonts/msjh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("Install Microsoft JhengHei or Noto Sans CJK to render bilingual captions")


FONTS = {size: font(size) for size in (16, 18, 20, 22, 24, 28, 32, 40)}
TITLE = font(44, True)


def text(draw, xy, content, size=22, fill=INK):
    draw.text(xy, content, font=FONTS[size], fill=fill)


def scene(person_x=None, moving=False, moment=0):
    im = Image.new("RGB", (960, 540), "#e4e8e5")
    d = ImageDraw.Draw(im)
    # A deliberately illustrated, anonymous entrance. No captured footage.
    d.rectangle((0, 0, 960, 140), fill="#c7d2d3")
    d.polygon([(0, 140), (960, 140), (960, 540), (0, 540)], fill="#bac7c6")
    for x in range(-650, 1800, 180):
        d.line((480 + (x-480)*.28, 140, x, 540), fill="#a5b5b5", width=2)
    for y in (178, 224, 285, 363, 462):
        d.line((0, y, 960, y), fill="#a5b5b5", width=2)
    d.rectangle((590, 30, 755, 174), fill="#526e78", outline="#879b9f", width=6)
    d.rectangle((610, 48, 735, 148), fill="#739caa")
    d.line((672, 50, 672, 150), fill="#526e78", width=3)
    d.rectangle((815, 64, 922, 166), fill="#f0f2e9", outline="#a7b6b8", width=3)
    d.line((833, 91, 900, 91), fill="#91aaa6", width=5)
    d.line((833, 112, 882, 112), fill="#a7bfbc", width=4)
    d.rounded_rectangle((49, 310, 157, 395), 12, fill="#677f7c")
    for cx, cy, radius in ((84, 289, 32), (126, 306, 35), (98, 260, 28)):
        d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill="#4c8775")
    d.rounded_rectangle((24, 22, 349, 58), 8, fill="#203746")
    text(d, (36, 28), "SYNTHETIC SCENE  /  合成示意場景", 18)
    if person_x is not None:
        cx, cy = int(person_x), 285
        stride = math.sin(moment * 7) * 14 if moving else 5
        d.ellipse((cx-38, cy+130, cx+44, cy+148), fill="#8b9f9e")
        d.line((cx-11, cy+69, cx-14-stride, cy+133), fill="#243f58", width=15)
        d.line((cx+11, cy+69, cx+14+stride, cy+133), fill="#243f58", width=15)
        d.line((cx-22, cy+12, cx-39-stride*.5, cy+65), fill="#456d89", width=13)
        d.line((cx+22, cy+12, cx+39+stride*.5, cy+65), fill="#456d89", width=13)
        d.rounded_rectangle((cx-25, cy-4, cx+25, cy+80), 15, fill="#3e708c")
        d.ellipse((cx-18, cy-48, cx+18, cy-12), fill="#d1ae91")
        d.arc((cx-19, cy-51, cx+19, cy-12), 180, 345, fill="#334856", width=12)
        d.rectangle((cx-54, cy-58, cx+54, cy+149), outline="#30ec87", width=3)
    return np.array(im)[:, :, ::-1].copy()


def state(t):
    if t < 4:
        return (0, "多邊形偵測，每段 2～5 分鐘", "Polygon monitoring · 2–5 minute clips", "IDLE", None, False, 0, 0)
    if t < 10:
        return (1, "沿著範圍點選，Enter 儲存", "Click vertices. Press Enter to save.", "IDLE", None, False, 0, 0)
    if t < 16:
        return (2, "前兩分鐘錄影，Telegram 靜默", "First 120 seconds: recording, no Telegram sends", "RECORDING", 280+(t-10)*44, True, (t-10)*20, 0)
    if t < 19:
        return (3, "滿兩分鐘後移動，立即切新段", "Clip A ends at 120 s. Clip B starts a fresh quiet period.", "RECORDING", 544+(t-16)*10, True, t-16, 0)
    if t < 27:
        return (4, "新片段繼續觀察，無移動計時", "Clip B: continue recording and measure inactivity", "RECORDING", 574, False, 3+(t-19)*15, min(120, (t-19)*15))
    if t < 31:
        return (5, "無移動滿兩分鐘，結束並待命", "Clip B ends. Telegram queue can send completed videos.", "IDLE", 574, False, 0, 120)
    if t < 34:
        return (6, "再有移動，重新錄影並靜默", "New activity starts clip C, with another quiet period", "RECORDING", 574+(t-31)*20, True, t-31, 0)
    return (7, "持續偵測，每段重新靜默兩分鐘", "2–5 minute clips · Movement rollover · Quiet first 120 seconds", "RECORDING", 634, False, t-31, 0)


def render(t):
    phase, title, subtitle, status, px, moving, elapsed, inactive = state(t)
    controller = PreviewController(ROI if phase != 1 else None)
    controller.layout = PreviewLayout.create(960, 540)
    if phase == 1:
        controller.command("select")
        count = min(6, int(t-4)+1)
        for point in ROI[:count]:
            controller.mouse("down", *controller.layout.canvas_point(point))
        if t >= 9.6:
            action = controller.key(13)
            controller.roi = action.roi
    preview = render_preview(scene(px, moving, t), controller, status,
                             count=int(px is not None), infer_ms=None, elapsed=elapsed)
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((40, 32, 201, 62), 8, fill="#193346")
    text(d, (52, 36), "SUPERLIVE AI", 18, CYAN)
    text(d, (1050, 36), "PRODUCT WALKTHROUGH", 18, MUTED)
    d.text((40, 73), title, font=TITLE, fill=INK)
    text(d, (41, 130), subtitle, 24, MUTED)
    d.rounded_rectangle((38, 178, 1002, 902), 12, outline="#324b65", width=2)
    im.paste(Image.fromarray(preview[:, :, ::-1]), (40, 180))
    # Side annotations are a presentation layer, not application controls.
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((1028, 180, 1400, 900), 16, fill="#142235", outline="#253c54")
    text(d, (1052, 204), "WORKFLOW / 運作流程", 20, CYAN)
    rows = [("01", "指定多邊形", "Choose a polygon", 1),
            ("02", "前 120 秒靜默", "Record with Telegram quiet", 2),
            ("03", "滿兩分鐘活動切段", "Movement starts a new clip", 3),
            ("04", "無移動結束與待命", "Inactivity ends the clip", 4)]
    for i, (number, zh, en, step) in enumerate(rows):
        y = 254+i*88
        selected = step == phase or (step == 4 and phase == 5) or (step == 2 and phase == 6) or phase == 7
        text(d, (1053, y+2), number, 22, CYAN if selected else "#536b85")
        text(d, (1100, y), zh, 24, INK if selected else MUTED)
        text(d, (1100, y+36), en, 16, MUTED)
    d.line((1052, 616, 1376, 616), fill="#2c435d", width=1)
    if phase in (4, 5):
        text(d, (1052, 638), "無移動 / INACTIVITY", 18, MUTED)
        d.text((1052, 673), f"{int(inactive):03d}", font=TITLE, fill=AMBER)
        text(d, (1166, 694), "/ 120 sec", 24, MUTED)
        d.rounded_rectangle((1052, 746, 1376, 756), 5, fill="#2b4055")
        if inactive > 0:
            d.rounded_rectangle((1052, 746, 1052+324*inactive/120, 756), 5, fill=AMBER)
        text(d, (1052, 783), "15× 時間加速" if phase == 4 else "靜默結束，待傳影片可傳送", 22, AMBER)
        text(d, (1052, 819), "Time compressed" if phase == 4 else "Queue released; keep detecting", 18, MUTED)
    elif phase in (2, 3, 6):
        clip = "A" if phase == 2 else "B" if phase == 3 else "C"
        text(d, (1052, 638), "● CLIP " + clip, 28, GREEN)
        text(d, (1052, 685), "Telegram 靜默", 24, AMBER)
        text(d, (1052, 727), f"{int(elapsed):03d} / 120 sec", 28, INK)
        text(d, (1052, 779), "20× 時間加速" if phase == 2 else "新片段，計時重新開始", 22, MUTED)
        text(d, (1052, 817), "No new Telegram sends" if phase == 2 else "Previous video waits in queue", 18, MUTED)
    else:
        text(d, (1052, 638), "區域內的人員活動", 24, INK)
        text(d, (1052, 687), "Person activity inside the ROI", 18, MUTED)
        text(d, (1052, 745), "正常片段 2～5 分鐘", 24, CYAN)
        text(d, (1052, 788), "Normal clips: 120–300 seconds", 18, MUTED)
    text(d, (40, 923), "合成場景・示意偵測框・加速計時・通知為示意", 18, MUTED)
    text(d, (40, 952), "Synthetic scene & scripted detections · Actual preview renderer · No real alerts sent", 16, MUTED)
    d.rounded_rectangle((1050, 934, 1400, 940), 3, fill="#263d57")
    if t > 0:
        d.rounded_rectangle((1050, 934, 1050+350*min(t/DURATION, 1), 940), 3, fill=CYAN)
    return im


def main():
    import imageio_ffmpeg
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, moment in (("polygon-selection.png", 9.3),
                              ("activity-recording.png", 13),
                              ("inactivity-rearm.png", 29)):
        render(moment).save(OUT / filename, optimize=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-vcodec", "rawvideo",
               "-s", f"{WIDTH}x{HEIGHT}", "-pix_fmt", "rgb24", "-r", str(FPS), "-i", "-",
               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT / "superlive-demo.mp4")]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame in range(FPS*DURATION):
            process.stdin.write(render(frame/FPS).tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("Video encoding failed")
    # Palette generation preserves readable captions while keeping README light.
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(OUT / "superlive-demo.mp4"),
                    "-filter_complex", "fps=3,scale=864:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer",
                    "-loop", "0", str(OUT / "superlive-demo.gif")], check=True)
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
