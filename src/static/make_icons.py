"""Generate the PWA / install icons for the NLC Dashboard from the brand logo.

Run once (or whenever the branding changes):
    python src/static/make_icons.py

Produces, next to this file:
    icon-192.png, icon-512.png   -> normal "any" icons
    icon-maskable-512.png        -> full-bleed navy, text kept inside the safe zone
    apple-touch-icon.png (180)   -> iOS/Safari home-screen icon

Colours mirror the in-app SVG logo: navy #1a3f8f, green #4a9d4f, white "NLC".
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY = (26, 63, 143)
GREEN = (74, 157, 79)
WHITE = (255, 255, 255)

HERE = Path(__file__).resolve().parent


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "Arial Bold.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_text(d: ImageDraw.ImageDraw, size: int, text: str, font: ImageFont.FreeTypeFont):
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), text, font=font, fill=WHITE)


def make_icon(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if maskable:
        # Launchers may crop to a circle: fill the whole canvas navy (no rounded
        # corners) and keep the green tile + text well inside the ~80% safe zone.
        d.rectangle([0, 0, size, size], fill=NAVY)
        inset = int(size * 0.22)
        radius = int(size * 0.10)
        d.rounded_rectangle([inset, inset, size - inset, size - inset], radius=radius, fill=GREEN)
        _draw_text(d, size, "NLC", _font(int(size * 0.24)))
    else:
        pad = int(size * 0.06)
        radius = int(size * 0.16)
        d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=NAVY)
        inner = int(size * 0.13)
        d.rounded_rectangle([inner, inner, size - inner, size - inner],
                            radius=int(size * 0.10), fill=GREEN)
        _draw_text(d, size, "NLC", _font(int(size * 0.30)))
    return img


def main() -> None:
    make_icon(192).save(HERE / "icon-192.png")
    make_icon(512).save(HERE / "icon-512.png")
    make_icon(512, maskable=True).save(HERE / "icon-maskable-512.png")
    make_icon(180).save(HERE / "apple-touch-icon.png")
    print("Wrote icons to", HERE)


if __name__ == "__main__":
    main()
