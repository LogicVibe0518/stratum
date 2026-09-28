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

from stratum_spec import BrandKit, Layer, PosterSpec, TextLayer

from stratum_worker.render.text import fit, load_metrics

# Where the first baseline sits inside a line box, as a fraction of the line height.
# Measured against the bundled faces; the SVG writer and the export share it.
BASELINE = 0.78


def box_px(spec: PosterSpec, layer: Layer) -> tuple[float, float, float, float]:
    """A layer's box in canvas pixels: x, y, width, height."""
    width, height = spec.canvas.size
    box = layer.box
    return box.x * width, box.y * height, box.w * width, box.h * height


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
