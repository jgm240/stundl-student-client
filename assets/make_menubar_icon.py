"""Menu bar icon: the SSC grid-S as a macOS *template* image (black + alpha; macOS tints it for light/dark).
36x36 px at 144 dpi = 18x18 pt. Lit cells are solid, the rest of the grid is faint. Also writes a preview."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent
S, PATTERN = 144, ["XXX", "X..", "XXX", "..X", "XXX"]      # drawn at 4x, then downsampled to 36 px
cell, gap = 20, 8
gw, gh = 3 * cell + 2 * gap, 5 * cell + 4 * gap
x0, y0 = (S - gw) // 2, (S - gh) // 2
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
for r, row in enumerate(PATTERN):
    for c, ch in enumerate(row):
        x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
        d.rounded_rectangle((x, y, x + cell, y + cell), 5, fill=(0, 0, 0, 255 if ch == "X" else 70))
icon = im.resize((36, 36), Image.LANCZOS)
icon.save(OUT / "menubar.png", dpi=(144, 144))
prev = Image.new("RGBA", (440, 120), (0, 0, 0, 0))
for i, (bg, ink) in enumerate((((236, 236, 240, 255), (30, 30, 30)), ((40, 40, 44, 255), (240, 240, 240)))):
    tile = Image.new("RGBA", (220, 120), bg)
    big = icon.resize((96, 96), Image.LANCZOS)
    tile.paste(Image.new("RGBA", (96, 96), ink + (255,)), (62, 12), big.getchannel("A"))
    prev.paste(tile, (i * 220, 0))
prev.save(OUT / "menubar-preview.png")
print("wrote", OUT / "menubar.png")
