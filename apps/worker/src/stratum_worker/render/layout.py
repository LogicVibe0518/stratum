"""Where every layer lands, in pixels: the one layout both outputs draw from.

The SVG renderer and the PowerPoint export must agree to the pixel about where a
headline breaks and how big it is. If each worked it out for itself, a change to
one would quietly make the file a designer edits in Canva differ from the poster
the client approved. So the geometry and the text fitting live here, once, and
both writers only translate the result into their own format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stratum_spec import BrandKit, Crop, Layer, LogoPlate, PosterSpec, TextLayer

from stratum_worker.render.text import fit, load_metrics

# Where the first baseline sits inside a line box, as a fraction of the line height.
# Measured against the bundled faces; the SVG writer and the export share it.
BASELINE = 0.78


def box_px(spec: PosterSpec, layer: Layer) -> tuple[float, float, float, float]:
    """A layer's box in canvas pixels: x, y, width, height."""
    width, height = spec.canvas.size
    box = layer.box
    return box.x * width, box.y * height, box.w * width, box.h * height


Geom = tuple[float, float, float, float]


def contained(box: Geom, image_size: tuple[int, int]) -> Geom:
    """Where an image drawn "contain" really lands: as large as fits, centred.

    Logos and cutouts are always drawn this way (never cropped), so a wide wordmark
    in a squarish box fills its width and only part of its height.
    """
    x, y, w, h = box
    image_w, image_h = image_size
    scale = min(w / image_w, h / image_h)
    shown_w, shown_h = image_w * scale, image_h * scale
    return x + (w - shown_w) / 2, y + (h - shown_h) / 2, shown_w, shown_h


CENTRE = Crop()


def crop_window(
    image_size: tuple[int, int], box_size: tuple[float, float], crop: Crop | None
) -> Geom:
    """The part of a photo a "cover" image shows, in the photo's own pixels: x, y, w, h.

    At zoom 1 the window is the largest one with the box's shape that fits in the
    photo, so the photo fills the box with nothing to spare. Zoom shrinks it about
    its centre. The window never leaves the photo: a crop asking for more than the
    edge gets the edge. With no crop, the centre - what every poster before schema 4
    showed, and what "xMidYMid slice" draws.
    """
    crop = crop or CENTRE
    image_w, image_h = image_size
    box_w, box_h = box_size
    ratio = box_w / box_h
    if image_w / image_h > ratio:  # the photo is wider than the box: trim the sides
        window_h = float(image_h)
        window_w = window_h * ratio
    else:  # taller: trim top and bottom
        window_w = float(image_w)
        window_h = window_w / ratio
    window_w, window_h = window_w / crop.zoom, window_h / crop.zoom
    x = min(max(crop.x * image_w - window_w / 2, 0.0), image_w - window_w)
    y = min(max(crop.y * image_h - window_h / 2, 0.0), image_h - window_h)
    return x, y, window_w, window_h


def plated(box: Geom, image_size: tuple[int, int], plate: LogoPlate) -> tuple[Geom, Geom, float]:
    """A logo on its panel, both kept inside the logo's box.

    The panel hugs the logo as drawn, with `pad` of space on every side (a fraction
    of the logo's shorter side), and the pair is scaled to fit the box the template
    gave the logo. Growing outwards instead would run the panel into whatever the
    template put next to the logo, usually the offer text.

    Returns (where the logo is drawn, where the panel is drawn, corner radius).
    """
    x, y, w, h = box
    image_w, image_h = image_size
    short = min(image_w, image_h)
    scale = min(w / (image_w + 2 * plate.pad * short), h / (image_h + 2 * plate.pad * short))
    logo_w, logo_h, pad = image_w * scale, image_h * scale, plate.pad * short * scale
    panel_w, panel_h = logo_w + 2 * pad, logo_h + 2 * pad
    panel_x, panel_y = x + (w - panel_w) / 2, y + (h - panel_h) / 2
    return (
        (panel_x + pad, panel_y + pad, logo_w, logo_h),
        (panel_x, panel_y, panel_w, panel_h),
        plate.radius * min(panel_w, panel_h),
    )


@dataclass(frozen=True)
class PlacedText:
    """A text layer after auto-fit: the exact size, lines and position to draw."""

    family: str
    weight: int
    color: str
    align: Literal["left", "center", "right"]
    size_px: float
    line_height_px: float
    lines: tuple[str, ...]
    box: tuple[float, float, float, float]

    @property
    def height_px(self) -> float:
        return self.line_height_px * len(self.lines)

    @property
    def top_px(self) -> float:
        """The block is centred vertically inside its box."""
        _, y, _, h = self.box
        return y + (h - self.height_px) / 2

    @property
    def anchor_x(self) -> float:
        x, _, w, _ = self.box
        return {"left": x, "center": x + w / 2, "right": x + w}[self.align]

    def baseline(self, index: int) -> float:
        return self.top_px + self.line_height_px * (index + BASELINE)


def place_text(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> PlacedText:
    """Fit a text layer into its box with the brand's real font metrics."""
    x, y, w, h = box_px(spec, layer)
    _, canvas_h = spec.canvas.size
    face = kit.font(layer.font)
    metrics = load_metrics(str(root / face.file))
    content = layer.content.upper() if layer.uppercase else layer.content
    fitted = fit(
        content,
        metrics,
        box_w_px=w,
        box_h_px=h,
        min_px=layer.min_size * canvas_h,
        max_px=layer.max_size * canvas_h,
        max_lines=layer.max_lines,
        line_height=face.line_height,
        balanced=layer.wrap == "balanced",
    )
    return PlacedText(
        family=face.family,
        weight=face.weight,
        color=kit.color(layer.color),
        align=layer.align,
        size_px=fitted.size_px,
        line_height_px=fitted.line_height_px,
        lines=tuple(fitted.lines),
        box=(x, y, w, h),
    )
