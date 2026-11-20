"""Draws the Stundl Student Client (SSC) app icon: a timetable grid whose lit cells spell an "S"
(one amber cell = the current lesson) on a teal-to-indigo squircle -> icon.png, icon.icns"""
import os, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 2048                                   # draw at 2x, downsample to 1024
OUT = Path(__file__).parent
X = lambda f: int(S * f)
FULL = os.environ.get("SSC_FULL") == "1"     # iOS: full-bleed opaque square (iOS rounds the corners itself)
K = 1.244 if FULL else 1.0
TEAL, INDIGO, AMBER = (14, 165, 164), (59, 63, 216), (255, 200, 61)

# squircle with a diagonal teal -> indigo gradient
m = Image.new("L", (S, S), 0)
ImageDraw.Draw(m).rounded_rectangle((X(.098), X(.098), X(.902), X(.902)), X(.19), fill=255)
if FULL:
    m = Image.new("L", (S, S), 255)
g = Image.linear_gradient("L").resize((S * 2, S * 2)).rotate(45, resample=Image.BICUBIC)
g = g.crop((S // 2, S // 2, S // 2 + S, S // 2 + S))
bg = Image.composite(Image.new("RGB", (S, S), INDIGO), Image.new("RGB", (S, S), TEAL), g).convert("RGBA")
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(bg, (0, 0), m)

# soft light from the top-left
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse((X(-.1), X(-.2), X(.7), X(.5)), fill=(255, 255, 255, 42))
glow = glow.filter(ImageFilter.GaussianBlur(X(.14)))
icon.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S), (0, 0, 0, 0)), m))

# 3 x 5 timetable grid; the lit cells form an S
PATTERN = ["XXX", "X..", "XXX", "..X", "XXX"]
cell, gap = X(.108 * K), X(.026 * K)
gw, gh = 3 * cell + 2 * gap, 5 * cell + 4 * gap
x0, y0 = (S - gw) // 2, (S - gh) // 2
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(sh)
for r, row in enumerate(PATTERN):
    for c, ch in enumerate(row):
        if ch == "X":
            x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
            d.rounded_rectangle((x, y + X(.01), x + cell, y + cell + X(.01)), X(.026 * K), fill=(10, 12, 70, 110))
icon.alpha_composite(sh.filter(ImageFilter.GaussianBlur(X(.012))))
for lit in (False, True):                  # unlit cells first, as a translucent layer that blends with the background
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for r, row in enumerate(PATTERN):
        for c, ch in enumerate(row):
            if (ch == "X") != lit:
                continue
            x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
            fill = (255, 255, 255, 255) if lit else (255, 255, 255, 52)
            if (r, c) == (2, 1):
                fill = AMBER + (255,)         # the current lesson
            d.rounded_rectangle((x, y, x + cell, y + cell), X(.026 * K), fill=fill)
    icon.alpha_composite(layer)

icon = icon.resize((1024, 1024), Image.LANCZOS)
if FULL:
    icon.convert("RGB").save(OUT / "icon-ios.png")
    print("wrote", OUT / "icon-ios.png")
    raise SystemExit
icon.save(OUT / "icon.png")
iset = OUT / "SSC.iconset"
iset.mkdir(exist_ok=True)
for sz in (16, 32, 128, 256, 512):
    icon.resize((sz, sz), Image.LANCZOS).save(iset / f"icon_{sz}x{sz}.png")
    icon.resize((sz * 2, sz * 2), Image.LANCZOS).save(iset / f"icon_{sz}x{sz}@2x.png")
subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(OUT / "icon.icns")], check=True)
for f in iset.iterdir():
    f.unlink()
iset.rmdir()
print("wrote", OUT / "icon.png", OUT / "icon.icns")
