"""Which part of each photo to show, in each size: the crop a designer would choose.

A photo rarely has the shape of its box, so part of it is cut. Before this, that
part was always cut evenly from both sides: a 3:2 phone photo in a 4:5 post lost a
sixth from each side whatever was there, a story lost more than half, and the
headline landed wherever the template put it - over a face as often as over sky.

For every photo layer, in every size, this tries windows across the photo (and a
little zoom, when the photo has pixels to spare) and keeps the one with the lowest
cost:

    cost =  FACE_CUT      x how much of any head falls outside the window
          + FACE_UNDER    x how much of any face is under words, the logo or a panel
          + SUBJECT_LOST  x how much of what stands out is outside   (x strength)
          + SUBJECT_UNDER x how much of it is under something drawn  (x strength)
          + SUBJECT_AT_EDGE x how much of it is right at the frame's edge (x strength)
          + BUSY          x how busy the photo is under the words
          + SHIFT         x how far the window moved from the centre
          + ZOOM          x how much it zoomed in

Faces are close to a hard rule: cutting a head costs more than everything else
together. The subject counts as much as the salience is sure there is one (see
`read.strength`), so a flat or evenly busy photo is placed for its words alone.
Moving and zooming cost a little, so with nothing to gain the photo stays centred,
exactly as it was before.

Everything here is a pure function of the spec, the brand kit and the photo reads:
the same poster always gets the same crop, and the crop is stored in the poster
(spec schema 4), so what the client approved is what renders later.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from stratum_spec import (
    BrandKit,
    Crop,
    ImageLayer,
    LayoutOverride,
    PosterSpec,
    ShapeLayer,
    TextLayer,
    retarget,
)
from stratum_spec.models import Aspect

from stratum_worker.draft.models import Flag
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP
from stratum_worker.photo.read import PhotoRead
from stratum_worker.render.layout import box_px, crop_window, place_text
from stratum_worker.render.text import load_metrics

Floats = npt.NDArray[np.float64]
Rect = tuple[float, float, float, float]

FACE_CUT = 30.0
FACE_UNDER = 12.0
# Losing the subject off the edge is worse than half-hiding it under a panel: it is
# still there, and the design meant the panel to sit over the photo.
SUBJECT_LOST = 6.0
SUBJECT_UNDER = 2.5
# A subject jammed against the edge of the frame looks cut even when it is not.
SUBJECT_AT_EDGE = 3.0
EDGE = 0.08
BUSY = 1.5
SHIFT = 0.3
ZOOM = 1.2

ZOOMS = (1.0, 1.15, 1.3)
# Window positions tried along each direction the photo can move.
STEPS = 21
# Zoom only while every poster pixel still has at least this many photo pixels
# behind it: at most a 1.25x enlargement, which a phone photo survives unnoticed.
MIN_DETAIL = 0.8
# A face this tall (share of the photo's height) or taller counts in full; smaller,
# background faces count less, down to a quarter.
FULL_FACE = 0.12
# Story furniture (profile row, caption, buttons) half-hides what is under it.
STORY_FURNITURE = 0.5

# When the best crop is still not good enough, a person is told. A quarter of a
# head outside the frame, or a third of a face under something drawn, is visible at
# a glance; faces shorter than FLAG_FACE of the photo are in the background.
CUT_TOO_MUCH = 0.25
HIDDEN_TOO_MUCH = 0.3
FLAG_FACE = 0.06
FACE_CUT_FLAG = Flag(
    code="face_cut",
    message=(
        "A face in the photo is cut off at the edge of the frame. Try another design, "
        "or a photo with more room around the person."
    ),
)
FACE_COVERED_FLAG = Flag(
    code="face_covered",
    message=(
        "The words, the logo or a panel sit over a face in the photo. Try a design "
        "with the words elsewhere, or move them."
    ),
)


@dataclass(frozen=True)
class Cover:
    """Something drawn over the photo, as fractions of the photo layer's box."""

    x: float
    y: float
    w: float
    h: float
    # 1 = hides the photo completely; a 50% scrim is 0.5.
    weight: float
    # How much letters here are read against the photo itself (0..1): 1 straight on
    # it, 0 on a solid panel, where the photo's busyness under them does not matter.
    words: float = 0.0


# --- geometry ------------------------------------------------------------------


def _table(grid: Floats) -> Floats:
    """Summed-area table with a zero row and column in front."""
    out: Floats = np.zeros((grid.shape[0] + 1, grid.shape[1] + 1))
    out[1:, 1:] = np.cumsum(np.cumsum(grid, 0), 1)
    return out


def _at(table: Floats, fx: Floats, fy: Floats) -> Floats:
    """The table at fractional positions. The integral of a grid of constant cells is
    exactly bilinear inside each cell, so interpolating the table is not an estimate."""
    rows, cols = table.shape[0] - 1, table.shape[1] - 1
    x = np.clip(fx, 0.0, 1.0) * cols
    y = np.clip(fy, 0.0, 1.0) * rows
    x0 = np.minimum(np.floor(x).astype(int), cols - 1)
    y0 = np.minimum(np.floor(y).astype(int), rows - 1)
    tx, ty = x - x0, y - y0
    out: Floats = (
        table[y0, x0] * (1 - tx) * (1 - ty)
        + table[y0, x0 + 1] * tx * (1 - ty)
        + table[y0 + 1, x0] * (1 - tx) * ty
        + table[y0 + 1, x0 + 1] * tx * ty
    )
    return out


def _mass(table: Floats, x: Floats, y: Floats, w: Floats, h: Floats) -> Floats:
    """Sum of the grid over rectangles given as fractions of the photo."""
    x1, y1 = x + w, y + h
    out: Floats = _at(table, x1, y1) - _at(table, x, y1) - _at(table, x1, y) + _at(table, x, y)
    return out


def _share_inside(rect: Rect, x: Floats, y: Floats, w: Floats, h: Floats) -> Floats:
    """How much of `rect` lies inside each of the rectangles (x, y, w, h)."""
    rx, ry, rw, rh = rect
    area = rw * rh
    if area <= 0:
        return np.zeros_like(x)
    inter_w = np.clip(np.minimum(rx + rw, x + w) - np.maximum(rx, x), 0.0, None)
    inter_h = np.clip(np.minimum(ry + rh, y + h) - np.maximum(ry, y), 0.0, None)
    out: Floats = inter_w * inter_h / area
    return out


def _clip_to_photo(rect: Rect) -> Rect:
    x, y, w, h = rect
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(1.0, x + w), min(1.0, y + h)
    return x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)


# --- choosing one crop ------------------------------------------------------------


def choose_crop(read: PhotoRead, box_size: tuple[float, float], covers: Iterable[Cover]) -> Crop:
    """The crop with the lowest cost for one photo in one box. See the module doc."""
    covers = list(covers)
    width, height = read.size
    box_w, box_h = box_size
    # Squared, so the one thing that stands out most outweighs a scatter of lesser
    # ones: keeping the rocket beats keeping every lamp on the launch pad.
    peak = read.salience**2
    total = float(peak.sum())
    sal_table = _table(peak / total if total > 0 else read.salience)
    busy_table = _table(read.busy)
    busy_cells = read.busy.shape[0] * read.busy.shape[1]
    heads = [
        (_clip_to_photo(face.head()), float(np.clip(face.h / FULL_FACE, 0.25, 1.0)), face)
        for face in read.faces
    ]

    xs: list[float] = []
    ys: list[float] = []
    ws: list[float] = []
    hs: list[float] = []
    zooms: list[float] = []
    for zoom in ZOOMS:
        _, _, win_w, win_h = crop_window((width, height), (box_w, box_h), Crop(zoom=zoom))
        if zoom > 1.0 and win_w / box_w < MIN_DETAIL:
            continue  # zooming this photo would enlarge it visibly
        share_w, share_h = win_w / width, win_h / height
        lefts = np.linspace(0.0, 1.0 - share_w, STEPS) if 1.0 - share_w > 1e-6 else [0.0]
        tops = np.linspace(0.0, 1.0 - share_h, STEPS) if 1.0 - share_h > 1e-6 else [0.0]
        for left in lefts:
            for top in tops:
                xs.append(float(left))
                ys.append(float(top))
                ws.append(share_w)
                hs.append(share_h)
                zooms.append(zoom)
    x, y = np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64)
    w, h = np.array(ws, dtype=np.float64), np.array(hs, dtype=np.float64)
    zoom_of = np.array(zooms, dtype=np.float64)

    cost = np.zeros_like(x)
    # What stands out: kept in the window, and not under what is drawn over it.
    lost = 1.0 - _mass(sal_table, x, y, w, h)
    under = np.zeros_like(x)
    busy_sum, word_area = np.zeros_like(x), np.zeros_like(x)
    for cover in covers:
        cx, cy = x + cover.x * w, y + cover.y * h
        cw, ch = cover.w * w, cover.h * h
        under += cover.weight * _mass(sal_table, cx, cy, cw, ch)
        if cover.words > 0:
            busy_sum += cover.words * _mass(busy_table, cx, cy, cw, ch)
            word_area += cover.words * cw * ch * busy_cells
    inner = _mass(sal_table, x + EDGE * w, y + EDGE * h, (1 - 2 * EDGE) * w, (1 - 2 * EDGE) * h)
    at_edge = (1.0 - lost) - inner
    cost += read.strength * (
        SUBJECT_LOST * lost + SUBJECT_UNDER * under + SUBJECT_AT_EDGE * at_edge
    )
    cost += BUSY * np.divide(busy_sum, word_area, out=np.zeros_like(x), where=word_area > 0)

    for head, importance, face in heads:
        cut = 1.0 - _share_inside(head, x, y, w, h)
        hidden = np.zeros_like(x)
        for cover in covers:
            rect = (face.x, face.y, face.w, face.h)
            hidden += cover.weight * _share_inside(
                rect, x + cover.x * w, y + cover.y * h, cover.w * w, cover.h * h
            )
        cost += importance * (FACE_CUT * cut + FACE_UNDER * np.clip(hidden, 0.0, 1.0))

    # Staying put and showing more of the photo win whenever nothing else is at stake.
    slack_x, slack_y = np.maximum(1.0 - w, 1e-9), np.maximum(1.0 - h, 1e-9)
    moved = np.hypot(
        np.abs(x - slack_x / 2) / (slack_x / 2), np.abs(y - slack_y / 2) / (slack_y / 2)
    )
    moved = np.where((1.0 - w <= 1e-6) & (1.0 - h <= 1e-6), 0.0, moved)
    cost += SHIFT * moved / np.sqrt(2.0) + ZOOM * (zoom_of - 1.0)

    best = int(np.argmin(cost))
    return Crop(
        x=round(float(x[best] + w[best] / 2), 4),
        y=round(float(y[best] + h[best] / 2), 4),
        zoom=float(zoom_of[best]),
    )


# --- what is drawn over a photo -----------------------------------------------------


def _ink(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> Rect:
    """Where a text layer's letters are, in canvas pixels: not its whole box."""
    placed = place_text(layer, spec, kit, root)
    metrics = load_metrics(str(root / kit.font(layer.font).file))
    widest = max((metrics.width(line, placed.size_px) for line in placed.lines), default=0.0)
    box_x, _, box_w, _ = placed.box
    left = {
        "left": box_x,
        "center": box_x + (box_w - widest) / 2,
        "right": box_x + box_w - widest,
    }[placed.align]
    return left, placed.top_px, widest, placed.height_px


def _inside(inner: Rect, outer: Rect) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def covers_over(
    spec: PosterSpec, index: int, kit: BrandKit, root: Path, *, furniture: bool = True
) -> list[Cover]:
    """Everything drawn over the photo at `index` in this one-size spec, in its box.

    `furniture` adds the bands a story's profile row and buttons sit on (9:16).
    """
    photo = spec.layers[index]
    px, py, pw, ph = box_px(spec, photo)
    width, height = spec.canvas.size
    out: list[Cover] = []

    def add(rect: Rect, weight: float, words: float = 0.0) -> None:
        x, y, w, h = rect
        x0, y0 = max(x, px), max(y, py)
        x1, y1 = min(x + w, px + pw), min(y + h, py + ph)
        if x1 > x0 and y1 > y0 and (weight > 0 or words > 0):
            out.append(
                Cover((x0 - px) / pw, (y0 - py) / ph, (x1 - x0) / pw, (y1 - y0) / ph, weight, words)
            )

    for above, layer in enumerate(spec.layers[index + 1 :], start=index + 1):
        if not layer.visible:
            continue
        if isinstance(layer, TextLayer):
            ink = _ink(layer, spec, kit, root)
            # The panels between the photo and these letters already hide the photo
            # under them; the letters hide only what shows through.
            backing = 0.0
            for between in spec.layers[index + 1 : above]:
                if (
                    isinstance(between, ShapeLayer)
                    and between.visible
                    and _inside(ink, box_px(spec, between))
                ):
                    backing = backing + (1 - backing) * between.opacity
            add(ink, 1.0 - backing, words=1.0 - backing)
        else:  # a panel, the logo, a cutout, another photo
            add(box_px(spec, layer), layer.opacity)

    # A photo that runs off the canvas: the part outside is never seen.
    far = 10.0 * max(width, height)
    add((-far, -far, far, 3 * far), 1.0)
    add((width, -far, far, 3 * far), 1.0)
    add((-far, -far, 3 * far, far), 1.0)
    add((-far, height, 3 * far, far), 1.0)
    if furniture and spec.canvas.aspect == "9:16":
        add((0.0, 0.0, width, STORY_SAFE_TOP * height), STORY_FURNITURE)
        add(
            (0.0, STORY_SAFE_BOTTOM * height, width, (1 - STORY_SAFE_BOTTOM) * height),
            STORY_FURNITURE,
        )
    return out


# --- a whole poster -----------------------------------------------------------------


def _framable(layer: object) -> bool:
    return (
        isinstance(layer, ImageLayer)
        and layer.fit == "cover"
        and layer.visible
        and layer.asset is not None
    )


def frame_photos(
    spec: PosterSpec,
    reads: Mapping[str, PhotoRead],
    kit: BrandKit,
    root: Path,
    aspects: Iterable[Aspect],
    *,
    only: set[str] | None = None,
) -> PosterSpec:
    """The poster with a crop chosen for every photo, in its own size and each of
    `aspects`. `reads` maps an asset id to its photo read; a photo without one keeps
    whatever crop it had. `only` limits the work to those layer ids (a swapped photo).

    A poster that carries no sizes of its own (made before schema 3, laid out from
    its template file) gets a crop in its own size only; the others inherit it.
    """
    base = spec.canvas.aspect
    sizes: list[Aspect] = [base] if not spec.variants else list(dict.fromkeys([base, *aspects]))
    chosen: dict[Aspect, dict[str, Crop]] = {}
    for aspect in sizes:
        one = retarget(spec, aspect) if aspect != base else spec
        for index, layer in enumerate(one.layers):
            if not _framable(layer) or (only is not None and layer.id not in only):
                continue
            assert isinstance(layer, ImageLayer) and layer.asset is not None
            read = reads.get(layer.asset)
            if read is None:
                continue
            _, _, box_w, box_h = box_px(one, layer)
            chosen.setdefault(aspect, {})[layer.id] = choose_crop(
                read, (box_w, box_h), covers_over(one, index, kit, root)
            )

    own = chosen.pop(base, {})
    layers = [
        layer.model_copy(update={"crop": own[layer.id]})
        if isinstance(layer, ImageLayer) and layer.id in own
        else layer
        for layer in spec.layers
    ]
    variants = {aspect: dict(overrides) for aspect, overrides in spec.variants.items()}
    for aspect, crops in chosen.items():
        size = variants.setdefault(aspect, {})
        for layer_id, crop in crops.items():
            size[layer_id] = size.get(layer_id, LayoutOverride()).model_copy(update={"crop": crop})
    return spec.model_copy(update={"layers": layers, "variants": variants})


def face_flags(
    spec: PosterSpec, reads: Mapping[str, PhotoRead], kit: BrandKit, root: Path
) -> tuple[Flag, ...]:
    """What a person should look at in this one-size poster: a face cut off by the
    crop, or under the words or the logo. Checked on every version, so a tweak that
    moves the headline over a face is caught as well as a draft that starts that way.
    """
    found: dict[str, Flag] = {}
    for index, layer in enumerate(spec.layers):
        if not _framable(layer):
            continue
        assert isinstance(layer, ImageLayer) and layer.asset is not None
        read = reads.get(layer.asset)
        if read is None or not read.faces:
            continue
        width, height = read.size
        _, _, box_w, box_h = box_px(spec, layer)
        win_x, win_y, win_w, win_h = crop_window(read.size, (box_w, box_h), layer.crop)
        x, y = np.array([win_x / width]), np.array([win_y / height])
        w, h = np.array([win_w / width]), np.array([win_h / height])
        covers = covers_over(spec, index, kit, root, furniture=False)
        for face in read.faces:
            if face.h < FLAG_FACE:
                continue
            cut = 1.0 - float(_share_inside(_clip_to_photo(face.head()), x, y, w, h)[0])
            rect = (face.x, face.y, face.w, face.h)
            hidden = sum(
                cover.weight
                * float(
                    _share_inside(rect, x + cover.x * w, y + cover.y * h, cover.w * w, cover.h * h)[
                        0
                    ]
                )
                for cover in covers
            )
            if cut > CUT_TOO_MUCH:
                found[FACE_CUT_FLAG.code] = FACE_CUT_FLAG
            if hidden > HIDDEN_TOO_MUCH:
                found[FACE_COVERED_FLAG.code] = FACE_COVERED_FLAG
    return tuple(found[code] for code in sorted(found))


__all__ = ["Cover", "choose_crop", "covers_over", "face_flags", "frame_photos"]
