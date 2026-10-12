"""Reproducible SuperLive lens mark, drawn at 4x for small Windows icon sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "assets"
ROOT.mkdir(exist_ok=True)
S = 4
im = Image.new("RGBA", (256*S, 256*S))
d = ImageDraw.Draw(im)
def box(values):
    return tuple(int(v*S) for v in values)
d.rounded_rectangle(box((8, 8, 248, 248)), radius=54*S, fill="#101e35")
d.rounded_rectangle(box((11, 11, 245, 245)), radius=51*S, outline="#29415e", width=2*S)
# Camera body and lens use bold geometry that remains legible at 16 pixels.
d.rounded_rectangle(box((40, 61, 216, 202)), radius=36*S, fill="#233c55")
d.rounded_rectangle(box((55, 76, 201, 187)), radius=25*S, outline="#5de7d2", width=10*S)
d.ellipse(box((82, 87, 174, 179)), fill="#101e35")
d.arc(box((88, 93, 168, 173)), 30, 305, fill="#46d8ec", width=10*S)
d.polygon([box(p) for p in ((119,112),(148,133),(119,154))], fill="#f2fbff")
d.ellipse(box((178, 38, 222, 82)), fill="#101e35")
d.ellipse(box((187, 47, 213, 73)), fill="#ff686f")
im = im.resize((512,512), Image.Resampling.LANCZOS)
im.save(ROOT / "superlive-ai.png")
im.save(ROOT / "superlive-ai.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICON_GENERATED=PASS")
