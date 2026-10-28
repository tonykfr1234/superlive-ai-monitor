"""Non-blocking, frozen-image manual review; private database only."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from record_protocol import atomic_json, read_json
from vehicle_registry import VehicleRegistry, COLORS, vehicle_color


def prepare_review(root, tracker):
    import cv2
    record = tracker.snapshot()
    if tracker.image is None or not record.get('group_id'):
        raise ValueError('請等待一張有效車輛影像，再按確認車牌。')
    photos = tracker.review_images or ([tracker.image] if record.get('vehicle_count', 1) == 1 else [])
    if not photos:
        raise ValueError('這張照片有多台車，請等待新影格後再選擇要確認的車輛。')
    folder = Path(root)/'runtime/plate_reviews'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    def save(name, photo):
        ok, encoded = cv2.imencode('.jpg', photo, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise ValueError('無法建立確認圖片。')
        data = encoded.tobytes()
        (folder/name).write_bytes(data)
        return dict(file=name, sha256=hashlib.sha256(data).hexdigest())
    cards = []
    for index, photo in enumerate(photos):
        card = save('vehicle_'+str(index)+'.jpg', photo)
        card.update(index=index, color=tracker.review_colors[index] if index < len(tracker.review_colors) else
                    dict(name='unknown', confidence=0.))
        cards.append(card)
    plate = None
    if tracker.plate_image is not None:
        plate = save('plate.jpg', tracker.plate_image)
    snapshot = dict(id=folder.name, group_id=record['group_id'], timestamp=record.get('image_timestamp'),
                    captured_at=time.time(), observed=record.get('observed_plate') or record.get('candidate_plate') or record.get('plate', ''),
                    plate=record.get('plate', ''), plate_image=plate,
                    plate_vehicle_index=record.get('plate_vehicle_index', 0 if len(photos) == 1 else None),
                    vehicles=cards)
    atomic_json(folder/'snapshot.json', snapshot)
    return folder/'snapshot.json'


def launch_review(root, tracker):
    path = prepare_review(root, tracker)
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--root', str(root), '--snapshot', str(path)],
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def save_confirmation(root, snapshot, car_index, plate, color, tolerance, use_color):
    cars = snapshot.get('vehicles', [])
    if not isinstance(car_index, int) or not 0 <= car_index < len(cars):
        raise ValueError('請選擇照片中的一台車輛。')
    observed = snapshot.get('observed', '') if snapshot.get('plate_vehicle_index') == car_index else ''
    context = dict(snapshot_id=snapshot['id'], group_id=snapshot['group_id'], vehicle_index=car_index,
                   image_timestamp=snapshot.get('timestamp'), image_sha256=cars[car_index]['sha256'])
    result = VehicleRegistry(root).confirm(plate, color, observed, context, tolerance, use_color)
    receipt = dict(result, **context, observed_plate=observed, id=uuid.uuid4().hex)
    # A receipt updates the visible card only if that exact vehicle group remains.
    atomic_json(Path(root)/'runtime/manual_plate_confirmation.json', receipt)
    return result


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox
    from PIL import Image, ImageTk
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    root, path = Path(args.root), Path(args.snapshot)
    snapshot = read_json(path, {})
    cards = snapshot.get('vehicles', [])
    if not cards:
        return 1
    window = tk.Tk()
    window.title('SuperLive AI — 人工確認車牌 / 本機車輛資料庫')
    window.geometry('890x780')
    window.minsize(840, 740)
    outer = ttk.Frame(window, padding=18)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='確認正確車牌，建立本機車輛資料庫', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
    ttk.Label(outer, text='畫面已固定；監控與錄影繼續運作。多台車請先選取對象。').pack(anchor='w', pady=(6, 10))
    car = ttk.Combobox(outer, state='readonly', values=['車輛 '+str(i+1) for i in range(len(cards))])
    car.pack(fill='x')
    car.current(snapshot.get('plate_vehicle_index') if isinstance(snapshot.get('plate_vehicle_index'), int) else 0)
    pictures = ttk.Frame(outer)
    pictures.pack(fill='x', pady=10)
    vehicle_view = ttk.Label(pictures, anchor='center')
    vehicle_view.pack(side='left', expand=True)
    plate_view = ttk.Label(pictures, anchor='center')
    plate_view.pack(side='right', expand=True)
    enhanced = tk.BooleanVar(value=True)
    ttk.Checkbutton(outer, text='放大並清晰化車牌（取消可核對原始裁切）', variable=enhanced, command=lambda: show_car()).pack(anchor='w')
    retained = []
    def load(meta, size, is_plate=False):
        from io import BytesIO
        data = (path.parent/meta['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != meta['sha256']:
            raise ValueError('確認圖片已變更，請重新開啟確認視窗。')
        picture = Image.open(BytesIO(data)).convert('RGB')
        if is_plate and enhanced.get():
            from preview_ui import enhance_plate_display
            picture = enhance_plate_display(picture, size)
        else:
            factor = min(size[0]/picture.width, size[1]/picture.height)
            picture = picture.resize((max(1, round(picture.width*factor)), max(1, round(picture.height*factor))), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', size, '#152436')
        canvas.paste(picture, ((size[0]-picture.width)//2, (size[1]-picture.height)//2))
        result = ImageTk.PhotoImage(canvas)
        retained.append(result)
        return result
    fields = ttk.Frame(outer)
    fields.pack(fill='x')
    plate = tk.StringVar()
    color = tk.StringVar(value=COLORS['unknown'])
    tolerance = tk.IntVar(value=3)
    use_color = tk.BooleanVar(value=True)
    ttk.Label(fields, text='正確車牌').grid(row=0, column=0, sticky='w')
    ttk.Entry(fields, textvariable=plate, font=('Consolas', 20), width=18).grid(row=1, column=0, sticky='w', padx=(0, 18))
    ttk.Label(fields, text='確認車身顏色').grid(row=0, column=1, sticky='w')
    ttk.Combobox(fields, textvariable=color, state='readonly', values=list(COLORS.values()), width=10).grid(row=1, column=1)
    ttk.Label(fields, text='容錯碼數').grid(row=0, column=2, sticky='w', padx=18)
    ttk.Combobox(fields, textvariable=tolerance, state='readonly', values=(1, 2, 3), width=5).grid(row=1, column=2, padx=18)
    ttk.Checkbutton(fields, text='搭配車色比對', variable=use_color).grid(row=1, column=3)
    status = tk.StringVar()
    ttk.Label(outer, textvariable=status, wraplength=820).pack(anchor='w', pady=10)
    def show_car(event=None):
        retained.clear()
        index = car.current()
        try:
            vehicle_view.configure(image=load(cards[index], (440, 215)))
            associated = snapshot.get('plate_vehicle_index') == index
            plate_view.configure(image=load(snapshot['plate_image'], (330, 215), is_plate=True) if associated and snapshot.get('plate_image') else '',
                                 text='' if associated and snapshot.get('plate_image') else '沒有可核對的車牌裁切圖')
            plate.set(snapshot.get('plate') or snapshot.get('observed', '') if associated else '')
            color.set(COLORS.get(cards[index].get('color', {}).get('name'), COLORS['unknown']))
            status.set('原始讀值：'+(snapshot.get('observed') or '尚未確認') if associated else '請確認所選車輛的車牌與顏色。')
        except (OSError, ValueError):
            status.set('圖片驗證失敗，請關閉後重新開啟。')
            save_button.configure(state='disabled')
    car.bind('<<ComboboxSelected>>', show_car)
    buttons = ttk.Frame(outer)
    buttons.pack(fill='x', pady=4)
    registry = VehicleRegistry(root)
    table_frame = ttk.Frame(outer)
    table = ttk.Treeview(table_frame, columns=('plate', 'color', 'enabled'), show='headings', height=6, selectmode='browse')
    for name, heading in [('plate', '已確認車牌'), ('color', '車色'), ('enabled', '比對狀態')]:
        table.heading(name, text=heading)
        table.column(name, width=180, stretch=True)
    def refresh():
        try:
            rows, preferences = registry.read()
            table.delete(*table.get_children())
            for row in rows:
                table.insert('', 'end', iid=row['plate'], values=(row['plate'], COLORS.get(row['color'], '不確定'), '啟用' if row['enabled'] else '停用'))
            tolerance.set(preferences['tolerance'])
            use_color.set(bool(preferences['use_color']))
        except Exception:
            status.set('資料庫無法讀取，請確認安裝資料夾可寫入。')
    def save():
        try:
            result = save_confirmation(root, snapshot, car.current(), plate.get(),
                                       next(k for k, v in COLORS.items() if v == color.get()), tolerance.get(), use_color.get())
            refresh()
            status.set('已儲存 '+result['plate_display']+'；後續即時辨識優先比對資料庫。本次人工確認不補發 Telegram。')
        except ValueError as error:
            messagebox.showerror('請檢查資料', str(error), parent=window)
        except Exception:
            messagebox.showerror('儲存失敗', '本機資料庫或確認紀錄無法寫入，請重試。', parent=window)
    save_button = ttk.Button(buttons, text='確認並儲存到資料庫', command=save)
    save_button.pack(side='left')
    ttk.Button(buttons, text='關閉', command=window.destroy).pack(side='right')
    ttk.Label(outer, text='已確認資料長期保留；錯誤資料可停用，重新確認同一車牌可恢復。').pack(anchor='w', pady=(10, 3))
    table_frame.pack(fill='both', expand=True)
    table.pack(side='left', fill='both', expand=True)
    scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    def disable():
        if not table.selection():
            return
        try:
            registry.disable(table.selection()[0])
            refresh()
            status.set('選取的資料已停用；後續辨識不再套用它。')
        except Exception:
            status.set('停用失敗，請稍後重試。')
    ttk.Button(outer, text='停用選取的資料庫車牌', command=disable).pack(anchor='w', pady=5)
    refresh()
    show_car()
    window.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
