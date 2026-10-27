"""Reproducible four-panel documentation using the real UI and synthetic pixels.

No camera footage, private plate, file paths, model inference or Telegram calls.
"""
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from preview_ui import PreviewController, PreviewLayout, render_preview, ui_font
from generate_demo import scene

OUT = ROOT/'docs/media'
STAMP = 1793056500


def synthetic_camera():
    image = Image.fromarray(scene()[:, :, ::-1])
    d = ImageDraw.Draw(image)
    # An intentionally illustrated vehicle, not a generated recognition claim.
    d.ellipse((338, 361, 685, 412), fill='#8f9fa0')
    d.rounded_rectangle((350, 297, 682, 386), 20, fill='#315975', outline='#223d56', width=4)
    d.polygon([(406, 297), (449, 239), (583, 239), (636, 297)], fill='#48738d')
    d.polygon([(426, 288), (458, 250), (570, 250), (610, 288)], fill='#afd0d6')
    d.line((516, 250, 516, 290), fill='#365d75', width=5)
    for x in (405, 624):
        d.ellipse((x-29, 353, x+29, 411), fill='#20313f')
        d.ellipse((x-15, 367, x+15, 398), fill='#8ea3b0')
    d.rounded_rectangle((478, 346, 557, 370), 3, fill='#e4e7d7')
    d.text((484, 348), 'AB1234', font=ui_font(17)[0], fill='#213344')
    d.rectangle((339, 228, 695, 419), outline='#38c8db', width=3)
    frame = np.array(image)[:, :, ::-1].copy()
    return frame, frame[218:430, 320:710].copy()


def controller():
    c = PreviewController([[.20, .32], [.79, .31], [.91, .88], [.25, .93]])
    c.plate_enabled = True
    c.plate_filter_targets = ['AB1234']
    c.object_counts = dict(person=0, car=1, motorcycle=0, bus=0, truck=0)
    c.latest_vehicle = dict(timestamp=STAMP, image_timestamp=STAMP, plate='AB1234')
    c.plate_events = [dict(id='demo', timestamp=STAMP, confirmed=True, text='AB1234',
                           matched='AB1234', distance=0, match_status='exact', source='live_recording',
                           source_video='Synthetic_Demo.mp4'),
                      dict(id='other', timestamp=STAMP-190, confirmed=True, text='CD5678',
                           matched=None, match_status='unmatched', source='live_recording',
                           source_video='Synthetic_Other.mp4')]
    return c


def render(c, frame, moment=0):
    c.animation_time = moment
    c.layout = PreviewLayout.create(960, 540, 1440, 900, dashboard=True,
                                    show_plates=True, panel_order=c.panel_order)
    return Image.fromarray(render_preview(frame, c, 'RECORDING', 1, 38, 46)[:, :, ::-1])


def guide(preview, chinese):
    image = Image.new('RGB', (1440, 1230), '#0a111d')
    d = ImageDraw.Draw(image)
    d.text((28, 22), 'SuperLive AI  /  '+('四區動態監控介面' if chinese else 'Four-panel live dashboard'),
           font=ui_font(32)[0], fill='#e9f5fd')
    d.text((30, 72), '拖曳標題交換位置 · L 還原排列 · 完整比例影像' if chinese else
           'Drag card headers to swap · L resets layout · Full image proportions', font=ui_font(20)[0], fill='#79cddd')
    image.paste(preview, (0, 112))
    d = ImageDraw.Draw(image)
    descriptions = ([('01  即時監視', '等比例完整畫面；錄影狀態呼吸燈'), ('02  車牌紀錄', '七天紀錄、捲軸與可點擊影片檔名'),
                     ('03  最近車輛', '實際擷取圖片、最近時間及對應車牌'), ('04  操作中心', '所有按鈕集中；拖曳交換、L 還原')] if chinese else
                    [('01  Live camera', 'Full aspect ratio with a pulsing recording indicator'), ('02  Plate history', 'Seven-day records, scrolling and video file links'),
                     ('03  Latest vehicle', 'Local captured image, last seen time and plate'), ('04  Controls', 'All actions together; drag to swap, L to reset')])
    for i, (title, body) in enumerate(descriptions):
        x, y = 30+(i%2)*710, 1025+(i//2)*72
        d.text((x, y), title, font=ui_font(21)[0], fill='#7dd9df')
        d.text((x, y+30), body, font=ui_font(17)[0], fill='#c2d4e4')
    d.text((30, 1190), '合成場景與示例車牌 · 使用實際介面繪製 · 非真實辨識準確度展示' if chinese else
           'Synthetic scene and sample plates · Actual UI renderer · Not an accuracy benchmark',
           font=ui_font(16)[0], fill='#92a8bd')
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frame, photo = synthetic_camera()
    c = controller()
    c.latest_vehicle_image = photo
    preview = render(c, frame, 1)
    preview.save(OUT/'dashboard-preview.png')
    guide(preview, True).save(OUT/'dashboard-guide.zh-TW.png')
    guide(preview, False).save(OUT/'dashboard-guide.en.png')
    frames = []
    for step in range(24):
        if step == 8:
            h = c.layout.headers['latest']
            target = c.layout.panels['plates']
            c.mouse('down', h.x+40, h.y+15)
            c.mouse('move', target.x+40, target.y+70)
        if step == 11:
            target = c.layout.panels['plates']
            c.mouse('up', target.x+40, target.y+70)
        if step == 18:
            c.command('reset_layout')
        screen = render(c, frame, step*.25)
        screen = screen.resize((960, 600), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', (960, 644), '#0a111d')
        canvas.paste(screen, (0, 0))
        ImageDraw.Draw(canvas).text((16, 610), 'SYNTHETIC DEMO  /  拖曳交換區塊 · L 還原 · 錄影呼吸燈',
                                    font=ui_font(17)[0], fill='#9ec6d8')
        frames.append(canvas)
    palette = frames[0].quantize(colors=128)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(OUT/'dashboard-layout.gif', save_all=True, append_images=frames[1:],
                   duration=250, loop=0, optimize=True, disposal=1)
    print('DASHBOARD_MEDIA=GENERATED_SYNTHETIC_ONLY')


if __name__ == '__main__':
    main()
