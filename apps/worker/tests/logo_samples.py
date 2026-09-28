"""Fictional logos drawn in code, so every test knows the exact colours that went in.

Drawn at four times the size and scaled down, the way a designer's export tool does,
so the edges are anti-aliased: blends of two colours that a naive reader would report
as brand colours of their own.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[3]
FONT = str(REPO / "assets" / "fonts" / "Anton-Regular.ttf")

NAVY = "#12264a"
ORANGE = "#ff6a00"
GREEN = "#1f4d2b"
LEAF = "#6fa35a"
CREAM = "#f3ebdd"
INK = "#1a1a1a"
TEAL = "#19b5b0"

Draw = Callable[[ImageDraw.ImageDraw, int], None]


def rgba(hex_colour: str) -> tuple[int, int, int, int]:
    text = hex_colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255


def draw(
    painter: Draw,
    *,
    size: tuple[int, int] = (600, 200),
    background: str | None = None,
    fmt: str = "PNG",
    quality: int = 85,
    pad: int = 0,
) -> bytes:
    scale = 4
    fill = rgba(background) if background else (0, 0, 0, 0)
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale), fill)
    painter(ImageDraw.Draw(big), scale)
    image = big.resize(size, Image.Resampling.LANCZOS)
    if pad:
        framed = Image.new("RGBA", (size[0] + 2 * pad, size[1] + 2 * pad), fill)
        framed.alpha_composite(image, (pad, pad))
        image = framed
    out = io.BytesIO()
    if fmt == "JPEG":
        flat = Image.new("RGB", image.size, fill[:3])
        flat.paste(image, mask=image.getchannel("A"))
        flat.save(out, "JPEG", quality=quality)
    else:
        image.save(out, "PNG")
    return out.getvalue()


def two_colour(main: str = NAVY, accent: str = ORANGE) -> Draw:
    """A disc with a bar through it and a wordmark: one main colour, one accent."""

    def paint(pen: ImageDraw.ImageDraw, s: int) -> None:
        pen.ellipse((10 * s, 10 * s, 190 * s, 190 * s), fill=rgba(main))
        pen.rectangle((60 * s, 90 * s, 140 * s, 110 * s), fill=rgba(accent))
        pen.text(
            (210 * s, 20 * s), "KOWAL", font=ImageFont.truetype(FONT, 110 * s), fill=rgba(main)
        )
        pen.rectangle((210 * s, 170 * s, 560 * s, 182 * s), fill=rgba(accent))

    return paint


def wordmark(colour: str) -> Draw:
    def paint(pen: ImageDraw.ImageDraw, s: int) -> None:
        pen.text(
            (20 * s, 20 * s), "LONE STAR", font=ImageFont.truetype(FONT, 120 * s), fill=rgba(colour)
        )

    return paint


def badge(fill: str = GREEN, mark: str = LEAF) -> Draw:
    """A filled rectangle edge to edge, with lettering on it."""

    def paint(pen: ImageDraw.ImageDraw, s: int) -> None:
        pen.rectangle((0, 0, 600 * s, 200 * s), fill=rgba(fill))
        pen.text((40 * s, 20 * s), "BLOOM", font=ImageFont.truetype(FONT, 110 * s), fill=rgba(mark))

    return paint


def gradient() -> Draw:
    def paint(pen: ImageDraw.ImageDraw, s: int) -> None:
        width = 600 * s
        for x in range(width):
            t = x / width
            pen.line(
                (x, 0, x, 200 * s), fill=(int(255 * t), int(80 + 100 * t), int(255 * (1 - t)), 255)
            )

    return paint
