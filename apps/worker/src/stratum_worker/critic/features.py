"""Design measurements: what a trained eye checks, as numbers.

Each feature is something a designer would say out loud about a poster: "the offer
is not lined up with the headline", "the words are jammed against the edge", "the
headline is barely bigger than the offer", "that last line is one lonely word",
"the text is sitting on a busy part of the photo". None of them is a verdict by
itself; the critic (M12) learns how much each matters.

They are measured on the poster as it will be drawn - the fitted type size, the real
line breaks, the width of the ink rather than of its box - because the box is where
text may go, and the eye only sees where it went.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, pairwise
from pathlib import Path

import numpy as np
from stratum_spec.brand import BrandKit
from stratum_spec.colour import contrast_ratio, min_contrast_for
from stratum_spec.models import Box, ImageLayer, LogoLayer, PosterSpec, ShapeLayer, TextLayer

from stratum_worker.draft.backdrop import behind, covers
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP
from stratum_worker.render.layout import place_text
from stratum_worker.render.text import load_metrics

FEATURES: tuple[str, ...] = (
    "align_misfit",
    "left_edge_spread",
    "margin_min",
    "safe_overflow",
    "text_overlap",
    "logo_text_overlap",
    "gap_min",
    "gap_unevenness",
    "balance_x",
    "balance_y",
    "text_centre_y",
    "headline_size",
    "offer_size",
    "cta_size",
    "hierarchy",
    "cta_to_offer",
    "headline_lines",
    "offer_lines",
    "orphan",
    "raggedness",
    "truncated",
    "at_minimum",
    "contrast_min",
    "contrast_margin",
    "text_on_photo",
    "busyness_under_text",
    "photo_share",
    "logo_share",
    "text_share",
    "offer_line_chars",
    "accent",
    "is_4x5",
    "is_1x1",
    "is_9x16",
    "is_16x9",
)
INDEX = {name: i for i, name in enumerate(FEATURES)}
ELLIPSIS = "…"


@dataclass(frozen=True)
class Ink:
    """Where a text layer's letters actually are, in pixels."""

    layer_id: str
    x: float
    y: float
    w: float
    h: float
    size: float
    lines: tuple[str, ...]
    widths: tuple[float, ...]
    at_minimum: bool
    index: int

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def _ink(spec: PosterSpec, index: int, layer: TextLayer, kit: BrandKit, root: Path) -> Ink:
    placed = place_text(layer, spec, kit, root)
    metrics = load_metrics(str(root / kit.font(layer.font).file))
    widths = tuple(metrics.width(line, placed.size_px) for line in placed.lines)
    widest = max(widths, default=0.0)
    box_x, _, box_w, _ = placed.box
    x = {"left": box_x, "center": box_x + (box_w - widest) / 2, "right": box_x + box_w - widest}[
        placed.align
    ]
    _, canvas_h = spec.canvas.size
    return Ink(
        layer.id,
        x,
        placed.top_px,
        widest,
        placed.height_px,
        placed.size_px,
        placed.lines,
        widths,
        placed.size_px <= layer.min_size * canvas_h + 0.5,
        index,
    )


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return max(0.0, w) * max(0.0, h)


def _role(inks: list[Ink], wanted: str, rank: int) -> Ink | None:
    by_id = {ink.layer_id: ink for ink in inks}
    if wanted in by_id:
        return by_id[wanted]
    ordered = sorted(inks, key=lambda ink: ink.size, reverse=True)
    return ordered[rank] if rank < len(ordered) else None


def _backing_opacity(spec: PosterSpec, index: int, ink: Ink) -> float:
    """How much of the photo a shape between it and these letters hides (0..1)."""
    width, height = spec.canvas.size
    box = Box(x=ink.x / width, y=ink.y / height, w=max(ink.w, 1) / width, h=max(ink.h, 1) / height)
    hidden = 0.0
    for below in range(index - 1, -1, -1):
        layer = spec.layers[below]
        if not layer.visible or not covers(layer.box, box):
            continue
        if isinstance(layer, ShapeLayer):
            hidden = hidden + (1 - hidden) * layer.opacity
            if hidden >= 0.999:
                return 1.0
        if isinstance(layer, ImageLayer):
            return hidden
    return 1.0  # nothing but the page behind it


def _busyness(spec: PosterSpec, ink: Ink, edges: dict[str, np.ndarray] | None) -> float:
    """Edge density of the photo under these letters (0 = flat, 1 = very busy)."""
    if not edges:
        return 0.0
    width, height = spec.canvas.size
    for layer in reversed(spec.layers[: ink.index]):
        if not isinstance(layer, ImageLayer) or not layer.visible or layer.asset not in edges:
            continue
        box = layer.box
        lx, ly, lw, lh = box.x * width, box.y * height, box.w * width, box.h * height
        if not (lx <= ink.x and ly <= ink.y and lx + lw >= ink.right and ly + lh >= ink.bottom):
            continue
        edge = edges[layer.asset]
        eh, ew = edge.shape
        scale = max(lw / ew, lh / eh)  # "cover": the photo fills its box, centred
        off_x, off_y = (ew * scale - lw) / 2, (eh * scale - lh) / 2
        x0 = int((ink.x - lx + off_x) / scale)
        x1 = max(x0 + 1, int((ink.right - lx + off_x) / scale))
        y0 = int((ink.y - ly + off_y) / scale)
        y1 = max(y0 + 1, int((ink.bottom - ly + off_y) / scale))
        patch = edge[max(0, y0) : min(eh, y1), max(0, x0) : min(ew, x1)]
        return float(patch.mean()) if patch.size else 0.0
    return 0.0


def measure(
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    *,
    edges: dict[str, np.ndarray] | None = None,
) -> np.ndarray:
    """Every feature of one poster in one size, in FEATURES order.

    `edges` maps a photo's asset id to its edge-density map (see `edge_map`); without
    it, the busyness under text is 0.
    """
    width, height = spec.canvas.size
    short = min(width, height)
    out = np.zeros(len(FEATURES), dtype=np.float64)
    f = out.__setitem__

    inks = [
        _ink(spec, index, layer, kit, root)
        for index, layer in enumerate(spec.layers)
        if isinstance(layer, TextLayer) and layer.visible
    ]
    logos = [
        (layer.box.x * width, layer.box.y * height, layer.box.w * width, layer.box.h * height)
        for layer in spec.layers
        if isinstance(layer, LogoLayer) and layer.visible
    ]
    rules = [
        layer
        for layer in spec.layers
        if isinstance(layer, ShapeLayer) and layer.visible and layer.id == "rule"
    ]
    rects = [(ink.x, ink.y, ink.w, ink.h) for ink in inks] + logos
    rects += [(r.box.x * width, r.box.y * height, r.box.w * width, r.box.h * height) for r in rules]

    # --- alignment and margins ---------------------------------------------------------
    if len(rects) > 1:
        misfits = []
        for i, (x, _, w, _) in enumerate(rects):
            best = min(
                min(abs(x - ox), abs(x + w / 2 - ox - ow / 2), abs(x + w - ox - ow))
                for j, (ox, _, ow, _) in enumerate(rects)
                if j != i
            )
            misfits.append(best / width)
        f(INDEX["align_misfit"], float(np.mean(misfits)))
    lefts = [ink.x / width for ink in inks]
    f(INDEX["left_edge_spread"], float(np.std(lefts)) if len(lefts) > 1 else 0.0)
    if rects:
        f(
            INDEX["margin_min"],
            min(min(x, y, width - x - w, height - y - h) for x, y, w, h in rects) / short,
        )
    if spec.canvas.aspect == "9:16" and rects:
        over = max(
            max(0.0, STORY_SAFE_TOP * height - y, y + h - STORY_SAFE_BOTTOM * height)
            for x, y, w, h in rects
        )
        f(INDEX["safe_overflow"], over / height)

    # --- collisions and rhythm --------------------------------------------------------
    ink_rects = [(ink.x, ink.y, ink.w, ink.h) for ink in inks]
    f(
        INDEX["text_overlap"],
        sum(_overlap(a, b) for a, b in combinations(ink_rects, 2)) / (short * short),
    )
    if logos:
        logo = logos[0]
        area = max(logo[2] * logo[3], 1.0)
        f(INDEX["logo_text_overlap"], sum(_overlap(logo, r) for r in ink_rects) / area)
    stacked = sorted(inks, key=lambda ink: ink.y)
    gaps = [
        (b.y - a.bottom) / short
        for a, b in pairwise(stacked)
        if min(a.right, b.right) > max(a.x, b.x)  # one above the other, not side by side
    ]
    if gaps:
        f(INDEX["gap_min"], min(gaps))
        f(INDEX["gap_unevenness"], float(np.std(gaps)) / max(float(np.mean(np.abs(gaps))), 1e-3))

    # --- balance ------------------------------------------------------------------------
    weights = [(x + w / 2, y + h / 2, w * h) for x, y, w, h in rects]
    total = sum(weight for _, _, weight in weights)
    if total > 0:
        cx = sum(x * weight for x, _, weight in weights) / total
        cy = sum(y * weight for _, y, weight in weights) / total
        f(INDEX["balance_x"], abs(cx / width - 0.5))
        f(INDEX["balance_y"], abs(cy / height - 0.5))
    if inks:
        top = min(ink.y for ink in inks)
        bottom = max(ink.bottom for ink in inks)
        f(INDEX["text_centre_y"], (top + bottom) / 2 / height)

    # --- type ---------------------------------------------------------------------------
    headline, offer, cta = (
        _role(inks, "headline", 0),
        _role(inks, "offer", 1),
        _role(inks, "cta", 2),
    )
    if headline:
        f(INDEX["headline_size"], headline.size / short)
        f(INDEX["headline_lines"], len(headline.lines))
        if len(headline.widths) > 1:
            f(INDEX["raggedness"], float(np.std(headline.widths)) / max(headline.widths))
    if offer:
        f(INDEX["offer_size"], offer.size / short)
        f(INDEX["offer_lines"], len(offer.lines))
        f(INDEX["offer_line_chars"], max(len(line) for line in offer.lines) if offer.lines else 0)
    if cta:
        f(INDEX["cta_size"], cta.size / short)
    if headline and offer:
        f(INDEX["hierarchy"], float(np.log(headline.size / max(offer.size, 1.0))))
    if offer and cta:
        f(INDEX["cta_to_offer"], cta.size / max(offer.size, 1.0))
    # A headline whose last line is one short word ("FOR") reads as a mistake.
    if headline and len(headline.widths) > 1:
        f(INDEX["orphan"], headline.widths[-1] / max(headline.widths))
    else:
        f(INDEX["orphan"], 1.0)
    f(INDEX["truncated"], sum(1 for ink in inks if ink.lines and ink.lines[-1].endswith(ELLIPSIS)))
    f(INDEX["at_minimum"], sum(1 for ink in inks if ink.at_minimum))

    # --- readability --------------------------------------------------------------------
    margins, on_photo, busy = [], 0.0, []
    text_area = sum(ink.w * ink.h for ink in inks) or 1.0
    for ink in inks:
        layer = spec.layers[ink.index]
        assert isinstance(layer, TextLayer)
        box = Box(
            x=max(ink.x, 0) / width,
            y=max(ink.y, 0) / height,
            w=max(ink.w, 1) / width,
            h=max(ink.h, 1) / height,
        )
        backdrop = behind(spec, ink.index, box, kit)
        ratio = contrast_ratio(kit.color(layer.color), backdrop)
        margins.append((ratio, ratio - min_contrast_for(ink.size / height)))
        hidden = _backing_opacity(spec, ink.index, ink)
        if hidden < 0.5:
            on_photo += ink.w * ink.h
        busy.append(_busyness(spec, ink, edges) * (1 - hidden))
    if margins:
        f(INDEX["contrast_min"], min(ratio for ratio, _ in margins))
        f(INDEX["contrast_margin"], min(margin for _, margin in margins))
    f(INDEX["text_on_photo"], on_photo / text_area)
    f(INDEX["busyness_under_text"], max(busy, default=0.0))

    # --- composition --------------------------------------------------------------------
    area = float(width * height)
    photos = [layer for layer in spec.layers if isinstance(layer, ImageLayer) and layer.visible]
    f(
        INDEX["photo_share"],
        min(1.0, sum(min(layer.box.w, 1.0) * min(layer.box.h, 1.0) for layer in photos)),
    )
    f(INDEX["logo_share"], sum(w * h for _, _, w, h in logos) / area)
    f(INDEX["text_share"], sum(ink.w * ink.h for ink in inks) / area)
    f(INDEX["accent"], 1.0 if rules else 0.0)
    f(INDEX[f"is_{spec.canvas.aspect.replace(':', 'x')}"], 1.0)
    return out


def edge_map(image: np.ndarray, *, size: int = 96) -> np.ndarray:
    """Edge density of a photo, 0..1, on a small grid: how busy each part of it is.

    Gradient magnitude of the luminance, normalised by a high percentile so one
    bright edge does not flatten the rest, then box-blurred so a text line samples
    the neighbourhood it sits in, not single pixels.
    """
    from PIL import Image

    grey = Image.fromarray(image).convert("L")
    grey.thumbnail((size, size))
    lum = np.asarray(grey, dtype=np.float64) / 255.0
    gy, gx = np.gradient(lum)
    magnitude = np.hypot(gx, gy)
    scale = float(np.percentile(magnitude, 99)) or 1.0
    norm: np.ndarray = np.clip(magnitude / scale, 0.0, 1.0)
    padded = np.pad(norm, 2, mode="edge")
    out: np.ndarray = np.zeros_like(norm)
    for dy in range(5):
        for dx in range(5):
            out += padded[dy : dy + norm.shape[0], dx : dx + norm.shape[1]] / 25.0
    return out


__all__ = ["FEATURES", "INDEX", "edge_map", "measure"]
