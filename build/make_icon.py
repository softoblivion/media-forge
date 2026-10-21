"""Build build/icon.ico (multi-size) for the exe and the window.

    python build/make_icon.py

Draws a rounded glass tile (deep blue to violet gradient, a soft top highlight and a thin light rim) with a simple
mark: a small solid square in the lower-left corner of a larger outlined square sharing that corner, i.e. "the
same picture, larger". Uses src/media_forge/ui/assets/icon-1024.png instead when that file exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PNG = ROOT / "src" / "media_forge" / "ui" / "assets" / "icon-1024.png"
OUT_ICO = ROOT / "build" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
S = 1024  # drawing canvas, downscaled with LANCZOS for the smaller sizes

TOP_LEFT = (46, 92, 255)
BOTTOM_RIGHT = (150, 70, 240)
DEEP = (10, 16, 44)


def _diagonal_gradient(size: int, a: tuple, b: tuple) -> Image.Image:
    """RGB gradient from `a` (top-left) to `b` (bottom-right)."""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            px[x, y] = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def _rounded_mask(box: tuple, radius: int, blur: float = 1.2) -> Image.Image:
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile_box = (40, 40, S - 40, S - 40)
    tile = _rounded_mask(tile_box, 230)

    # body: gradient over a deep base, darker towards the bottom for depth
    body = Image.blend(Image.new("RGB", (S, S), DEEP), _diagonal_gradient(S, TOP_LEFT, BOTTOM_RIGHT), 0.82)
    shade = Image.linear_gradient("L").resize((S, S))  # 0 at the top, 255 at the bottom
    body = Image.composite(Image.new("RGB", (S, S), DEEP), body, shade.point(lambda v: int(v * 0.35)))
    img.paste(body, (0, 0), tile)

    # glass highlight: a soft white band over the upper half
    highlight = Image.new("L", (S, S), 0)
    ImageDraw.Draw(highlight).ellipse((-160, -560, S + 160, 470), fill=70)
    highlight = ImageChops.multiply(highlight.filter(ImageFilter.GaussianBlur(40)), tile)
    img = Image.composite(Image.new("RGBA", (S, S), (255, 255, 255, 255)), img, highlight)
    img.putalpha(tile)

    # rim: thin light edge, stronger at the top
    rim = Image.new("L", (S, S), 0)
    ImageDraw.Draw(rim).rounded_rectangle(tile_box, radius=230, outline=255, width=10)
    rim = ImageChops.multiply(rim.filter(ImageFilter.GaussianBlur(1.5)),
                              Image.linear_gradient("L").rotate(180).resize((S, S)).point(lambda v: 60 + v // 2))
    img = Image.composite(Image.new("RGBA", (S, S), (255, 255, 255, 255)), img, rim)
    img.putalpha(tile)

    # mark: large outlined square and a small solid one sharing the lower-left corner
    d = ImageDraw.Draw(img)
    left, bottom = 262, S - 262
    big = (left, 262, S - 262, bottom)
    d.rounded_rectangle(big, radius=70, outline=(255, 255, 255, 235), width=44)
    small = (left, bottom - 250, left + 250, bottom)
    d.rounded_rectangle(small, radius=48, fill=(255, 255, 255, 250))
    # a short diagonal stroke from the small square towards the opposite corner
    d.line((left + 300, bottom - 300, S - 352, 352), fill=(255, 255, 255, 235), width=40)
    d.polygon([(S - 330, 330), (S - 330, 470), (S - 470, 330)], fill=(255, 255, 255, 235))
    return img


def main() -> int:
    if SOURCE_PNG.is_file():
        base = Image.open(SOURCE_PNG).convert("RGBA")
        origin = SOURCE_PNG.relative_to(ROOT)
    else:
        base = draw()
        origin = "drawn tile"
    if base.width != base.height:
        side = max(base.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(base, ((side - base.width) // 2, (side - base.height) // 2))
        base = square
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"icon.ico written from {origin} ({OUT_ICO.stat().st_size // 1024} KB, sizes {SIZES})")
    if "--png" in sys.argv[1:]:
        preview = OUT_ICO.with_suffix(".png") if len(sys.argv) < 3 else Path(sys.argv[sys.argv.index("--png") + 1])
        base.resize((256, 256), Image.LANCZOS).save(preview)
        print(f"preview written to {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
