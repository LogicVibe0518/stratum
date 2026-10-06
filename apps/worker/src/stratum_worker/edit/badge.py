"""An offer badge (M10i): "20% OFF" in a starburst, in a corner, in every size.

Trades posts live on offers, and a badge is how designers make one jump out. It is
two layers: a shape ("badge", a burst in the brand's accent colour) and its words
("badge_text"). The words take the brand colour that stands out most from the
burst, so the badge is readable without anyone choosing.

Where it goes: the corner asked for, or ("auto") the corner it covers the least of
the words and the logo in, judged in the post's own size. Every other size keeps
that corner, round in its own proportions, unless there the badge would sit on the
words: then that size takes its own best corner (a banner's words are on the right).

The words come from the designer (or the poster's own offer): the prompt says never
to invent a discount, and the claims check reads them like any other words on the
poster.

Pure: no database, no network.
"""

from __future__ import annotations

from typing import Literal

from stratum_spec import BrandKit, PosterSpec
from stratum_spec.colour import contrast_ratio
from stratum_spec.models import Box, Canvas, LayoutOverride, LogoLayer, ShapeLayer, TextLayer
from stratum_spec.ops import STORY_SAFE_BOTTOM, STORY_SAFE_TOP
from stratum_spec.templates import base_width_for, retarget

from stratum_worker.design.frame import MARGIN

Corner = Literal["auto", "top_left", "top_right", "bottom_left", "bottom_right"]
CORNERS: tuple[Corner, ...] = ("top_right", "top_left", "bottom_right", "bottom_left")
SIZE = 0.26  # the burst's diameter, as a share of the shorter side
TEXT_SHARE = 0.62  # the words sit in this much of the burst's middle
MAX_CHARS = 14
SHAPE_ID, TEXT_ID = "badge", "badge_text"


class BadgeError(ValueError):
    """A badge that cannot be made, said in a sentence for the designer."""


def _canvas(aspect: str, base_width: int) -> tuple[int, int]:
    width = base_width_for(base_width, aspect)
    return Canvas(aspect=aspect, base_width=width).size  # type: ignore[arg-type]


def _box(corner: Corner, aspect: str, base_width: int) -> Box:
    width, height = _canvas(aspect, base_width)
    short = min(width, height)
    w, h = SIZE * short / width, SIZE * short / height
    mx, my = MARGIN * short / width, MARGIN * short / height
    top, bottom = my, 1 - my
    if aspect == "9:16":
        top, bottom = max(top, STORY_SAFE_TOP), min(bottom, STORY_SAFE_BOTTOM)
    x = mx if corner.endswith("left") else 1 - mx - w
    y = top if corner.startswith("top") else bottom - h
    return Box(x=round(x, 5), y=round(y, 5), w=round(w, 5), h=round(h, 5))


def _inner(box: Box) -> Box:
    w, h = box.w * TEXT_SHARE, box.h * TEXT_SHARE
    return Box(x=round(box.x + (box.w - w) / 2, 5), y=round(box.y + (box.h - h) / 2, 5), w=w, h=h)


def _overlap(a: Box, b: Box) -> float:
    w = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    h = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return w * h


# A size moves the badge to another corner when, in the corner the post's own size
# chose, it would cover more than this share of the poster's words or logo.
CLASH = 0.002


def _covered(laid: PosterSpec, corner: Corner, aspect: str, base_width: int) -> float:
    box = _box(corner, aspect, base_width)
    return sum(
        _overlap(box, one.box)
        for one in laid.layers
        if one.visible and isinstance(one, TextLayer | LogoLayer) and one.id != TEXT_ID
    )


def best_corner(spec: PosterSpec, aspect: str | None = None) -> Corner:
    """The corner where the badge covers the least of the words and the logo, in the
    post's own size or in `aspect`."""
    size = aspect or spec.canvas.aspect
    laid = retarget(spec, size) if size != spec.canvas.aspect else spec
    return min(CORNERS, key=lambda corner: _covered(laid, corner, size, spec.canvas.base_width))


def corners(spec: PosterSpec, asked: Corner) -> dict[str, Corner]:
    """The corner for every size: the one asked for (or the best in the post's own
    size), kept in every other size unless it would sit on the words there."""
    base = spec.canvas.aspect
    chosen: Corner = best_corner(spec) if asked == "auto" else asked
    out: dict[str, Corner] = {base: chosen}
    for aspect in spec.variants:
        if aspect == base:
            continue
        laid = retarget(spec, aspect)
        clash = _covered(laid, chosen, aspect, spec.canvas.base_width) > CLASH
        out[aspect] = best_corner(spec, aspect) if asked == "auto" and clash else chosen
    return out


def _fill(kit: BrandKit) -> str:
    for name in ("secondary", "accent", "primary"):
        if name in kit.palette:
            return f"palette.{name}"
    return f"palette.{next(iter(kit.palette))}"


def _ink(kit: BrandKit, fill: str) -> str:
    """The brand colour that stands out most from the burst."""
    behind = kit.color(fill)
    return max(
        (f"palette.{name}" for name in kit.palette if f"palette.{name}" != fill),
        key=lambda token: contrast_ratio(kit.color(token), behind),
    )


def remove(spec: PosterSpec) -> PosterSpec:
    """The poster without its badge (unchanged when it has none)."""
    gone = {SHAPE_ID, TEXT_ID}
    return spec.model_copy(
        update={
            "layers": [one for one in spec.layers if one.id not in gone],
            "variants": {
                aspect: {key: value for key, value in layers.items() if key not in gone}
                for aspect, layers in spec.variants.items()
            },
        }
    )


def place(spec: PosterSpec, kit: BrandKit, text: str, corner: Corner = "auto") -> PosterSpec:
    """The poster with this badge, replacing any it had. Raises BadgeError."""
    words = " ".join(text.split()).upper()
    if not words:
        raise BadgeError("A badge needs a few words, like 20% OFF or FREE QUOTE.")
    if len(words) > MAX_CHARS:
        raise BadgeError(
            f"A badge holds {MAX_CHARS} characters at most; '{words}' is {len(words)}. "
            f"Try something shorter, like 20% OFF."
        )
    bare = remove(spec)
    canvas = bare.canvas
    where = corners(bare, corner)
    box = _box(where[canvas.aspect], canvas.aspect, canvas.base_width)
    fill = _fill(kit)
    burst = ShapeLayer(id=SHAPE_ID, box=box, shape="burst", fill=fill)
    label = TextLayer(
        id=TEXT_ID,
        box=_inner(box),
        content=words,
        font="font.display" if "display" in kit.fonts else f"font.{next(iter(kit.fonts))}",
        color=_ink(kit, fill),
        align="center",
        max_lines=2,
        min_size=0.02,
        max_size=0.06,
        uppercase=True,
    )
    variants = {}
    for aspect, layers in bare.variants.items():
        other = _box(where.get(aspect, where[canvas.aspect]), aspect, canvas.base_width)
        variants[aspect] = {
            **layers,
            SHAPE_ID: LayoutOverride(box=other),
            TEXT_ID: LayoutOverride(box=_inner(other)),
        }
    return bare.model_copy(update={"layers": [*bare.layers, burst, label], "variants": variants})


__all__ = [
    "CORNERS",
    "MAX_CHARS",
    "BadgeError",
    "Corner",
    "best_corner",
    "corners",
    "place",
    "remove",
]
