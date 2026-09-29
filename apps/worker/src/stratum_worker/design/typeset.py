"""Setting the words: measure the real copy with the brand's fonts, then stack it.

The nine hand-made templates give every text layer a fixed box, so a short headline
leaves a gap and a long one shrinks. Here the copy is measured first and the boxes
are made to hold it, so the offer sits one fixed gap under the last line of the
headline whatever that headline says.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stratum_spec.brand import BrandKit

from stratum_worker.design.frame import Frame, Rect
from stratum_worker.render.text import Metrics, load_metrics, wrap

Scale = Literal["bold", "calm"]
Anchor = Literal["top", "center", "bottom"]

# Type sizes as shares of the page's shorter side, so a headline looks the same size
# on a feed post, a story and a banner.
HEADLINE = {"bold": 0.108, "calm": 0.084}
HEADLINE_BIG = {"bold": 0.13, "calm": 0.1}  # type-only designs: the words are the picture
HEADLINE_MIN = 0.05
OFFER, OFFER_MIN = 0.038, 0.03
CTA, CTA_MIN = 0.031, 0.025
MAX_LINES = {"headline": 3, "offer": 3, "cta": 2}
# Space between the pieces, as shares of the shorter side.
RULE_W, RULE_H = 0.13, 0.009
GAP_AFTER_RULE, GAP_AFTER_HEADLINE, GAP_AFTER_OFFER = 0.034, 0.03, 0.038
# A text box is made this much taller than its lines, so auto-fit lands on the
# measured size rather than one step under it.
SLACK = 1.04
# How far auto-fit may shrink the words later (after an edit makes them longer).
SHRINK = 0.8
STEP = 0.94


@dataclass(frozen=True)
class Copy:
    headline: str
    offer: str
    cta: str


# What a design is measured with before the real words exist: about as long as the
# copy model is allowed to write, so the space reserved is the space needed.
PLACEHOLDER = Copy(
    headline="A headline about as long as allowed",
    offer="An offer line of the usual length, with a date and a price in it",
    cta="Call today to book yours",
)


@dataclass(frozen=True)
class Block:
    """One text layer, measured."""

    layer_id: str
    font: str  # brand token, e.g. "font.display"
    size_px: float
    lines: int
    height_px: float
    uppercase: bool


@dataclass(frozen=True)
class Stack:
    """The whole text block: optional accent rule, headline, offer, call to action."""

    rule: bool
    blocks: tuple[Block, ...]
    gaps: tuple[float, ...]  # before each block (the first is after the rule, or 0)
    rule_h: float
    height_px: float


@dataclass(frozen=True)
class Placed:
    rule: Rect | None
    boxes: dict[str, Rect]
    sizes: dict[str, float]  # layer id -> px
    lines: dict[str, int]
    top: float
    bottom: float


def _metrics(kit: BrandKit, token: str, root: Path) -> tuple[Metrics, float]:
    face = kit.font(token)
    return load_metrics(str(root / face.file)), face.line_height


def _measure(
    layer_id: str,
    text: str,
    token: str,
    size: float,
    width: float,
    kit: BrandKit,
    root: Path,
    *,
    uppercase: bool,
) -> Block | None:
    metrics, line_height = _metrics(kit, token, root)
    shown = text.upper() if uppercase else text
    lines = wrap(shown, metrics, size, width)
    too_wide = any(metrics.width(line, size) > width for line in lines)
    if too_wide or len(lines) > MAX_LINES[layer_id]:
        return None
    return Block(layer_id, token, size, len(lines), size * line_height * len(lines), uppercase)


def _largest(
    layer_id: str,
    text: str,
    token: str,
    target: float,
    floor: float,
    width: float,
    kit: BrandKit,
    root: Path,
    *,
    uppercase: bool,
) -> Block | None:
    """The biggest size at or under `target` at which the words fit the width."""
    size = target
    while size >= floor:
        block = _measure(layer_id, text, token, size, width, kit, root, uppercase=uppercase)
        if block is not None:
            return block
        size *= STEP
    return None


def stack(
    copy: Copy,
    frame: Frame,
    kit: BrandKit,
    root: Path,
    *,
    width: float,
    max_height: float,
    scale: Scale,
    rule: bool,
    big: bool = False,
) -> Stack | None:
    """The words at the largest headline size that fits `width` x `max_height`.

    None when even the smallest readable sizes will not fit: the design is not a
    candidate for this copy, rather than a poster with cut-off words.
    """
    short = frame.short
    target = (HEADLINE_BIG if big else HEADLINE)[scale] * short
    offer = _largest(
        "offer",
        copy.offer,
        "font.body",
        OFFER * short,
        OFFER_MIN * short,
        width,
        kit,
        root,
        uppercase=False,
    )
    cta = _largest(
        "cta",
        copy.cta,
        "font.body_bold",
        CTA * short,
        CTA_MIN * short,
        width,
        kit,
        root,
        uppercase=False,
    )
    if offer is None or cta is None:
        return None
    rule_h = RULE_H * short if rule else 0.0
    gaps = (
        GAP_AFTER_RULE * short if rule else 0.0,
        GAP_AFTER_HEADLINE * short,
        GAP_AFTER_OFFER * short,
    )
    fixed = rule_h + sum(gaps) + offer.height_px + cta.height_px
    size = target
    while size >= HEADLINE_MIN * short:
        headline = _largest(
            "headline",
            copy.headline,
            "font.display",
            size,
            HEADLINE_MIN * short,
            width,
            kit,
            root,
            uppercase=True,
        )
        if headline is None:
            return None
        total = fixed + headline.height_px
        if total <= max_height:
            return Stack(rule, (headline, offer, cta), gaps, rule_h, total)
        size = headline.size_px * STEP
    return None


def place(words: Stack, zone: Rect, anchor: Anchor, *, rule_w: float, align: str) -> Placed:
    """Put a measured stack into a zone. Boxes span the zone's width; alignment is the
    text layer's own job, so centred text stays centred when it is edited."""
    if anchor == "top":
        cursor = zone.y
    elif anchor == "bottom":
        cursor = zone.bottom - words.height_px
    else:
        cursor = zone.y + (zone.h - words.height_px) / 2
    top = cursor
    rule_rect = None
    if words.rule:
        x = zone.x if align == "left" else zone.x + (zone.w - rule_w) / 2
        rule_rect = Rect(x, cursor, rule_w, words.rule_h)
        cursor += words.rule_h
    boxes: dict[str, Rect] = {}
    sizes: dict[str, float] = {}
    lines: dict[str, int] = {}
    for gap, block in zip(words.gaps, words.blocks, strict=True):
        cursor += gap
        extra = block.height_px * (SLACK - 1)
        # The slack never reaches past the zone: a story's safe band is a hard line.
        top_edge = max(zone.y, cursor - extra / 2)
        bottom_edge = min(zone.bottom, cursor + block.height_px + extra / 2)
        boxes[block.layer_id] = Rect(zone.x, top_edge, zone.w, bottom_edge - top_edge)
        sizes[block.layer_id] = block.size_px
        lines[block.layer_id] = block.lines
        cursor += block.height_px
    return Placed(rule_rect, boxes, sizes, lines, top, cursor)
