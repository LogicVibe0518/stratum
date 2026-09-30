"""Design families: a handful of layout ideas, each with a few honest choices.

A family is one way of putting a photo, the words and a logo on a page ("photo with
a colour panel", "photo under a scrim", "two photos", "words only"). Its parameters
are the choices a designer makes within it: which side the words go, which brand
colour carries them, left or centred, an accent rule or not, a big or a calm
headline. Every combination is a candidate; the critic (M12) decides which are good.

Rules every family keeps, so every candidate is at least correct:
- the same layers in every size (a size may only lay them out differently), so a
  poster's four sizes can never say different things;
- words and logo inside the margins, and inside a story's safe band;
- text colour always the brand's pair for what it sits on (text_on_primary on the
  primary colour, and so on), which M9 already checks for contrast;
- words measured with the brand's own fonts; if they do not fit, the candidate does
  not exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from stratum_spec.brand import BrandKit

from stratum_worker.design.frame import Frame, Rect
from stratum_worker.design.typeset import RULE_W, Anchor, Copy, Placed, Scale, place, stack

Layers = list[dict[str, Any]]
Choice = dict[str, str]

# The brand's colour for a surface, and the colour the words take on it.
FILL = {
    "primary": ("palette.primary", "palette.text_on_primary"),
    "surface": ("palette.surface", "palette.text"),
    "dark": ("palette.text", "palette.text_on_dark"),
}
ACCENT = "palette.secondary"
# Largest box a logo may have, as shares of the shorter side. The box then takes the
# logo's own shape, so a left-aligned design gets a left-aligned logo.
LOGO_W, LOGO_H = 0.24, 0.085
LOGO_MAX_W, LOGO_MAX_H = 0.3, 0.14
GUTTER = 0.012
PAD = 0.06  # inside a scrim, band or card, around the words


@dataclass(frozen=True)
class Family:
    name: str
    title: str
    params: tuple[tuple[str, tuple[str, ...]], ...]
    photos: dict[str, str]  # layer id -> role ("background" may be generated)
    build: Callable[[Frame, Choice, Copy, BrandKit, Path, float], Layers | None]
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --- small builders -----------------------------------------------------------------


def _image(layer_id: str, frame: Frame, rect: Rect, **extra: Any) -> dict[str, Any]:
    return {"type": "image", "id": layer_id, "box": frame.box(rect).model_dump(), **extra}


def _shape(
    layer_id: str, frame: Frame, rect: Rect | None, fill: str, **extra: Any
) -> dict[str, Any]:
    visible = rect is not None
    shown = rect if rect is not None else Rect(0.0, 0.0, 1.0, 1.0)
    return {
        "type": "shape",
        "id": layer_id,
        "box": frame.box(shown).model_dump(),
        "fill": fill,
        "visible": visible,
        **extra,
    }


def _texts(frame: Frame, placed: Placed, colour: str, align: str) -> Layers:
    layers: Layers = []
    for layer_id, font in (
        ("headline", "font.display"),
        ("offer", "font.body"),
        ("cta", "font.body_bold"),
    ):
        size = frame.size(placed.sizes[layer_id])
        layers.append(
            {
                "type": "text",
                "id": layer_id,
                "box": frame.box(placed.boxes[layer_id]).model_dump(),
                "content": layer_id.upper(),  # the real words are bound by build_spec
                "font": font,
                "color": colour,
                "align": align,
                "max_lines": {"headline": 3, "offer": 3, "cta": 2}[layer_id],
                "min_size": round(size * 0.8, 5),
                "max_size": size,
                "uppercase": layer_id == "headline",
            }
        )
    return layers


def _rule(frame: Frame, placed: Placed) -> dict[str, Any]:
    return _shape("rule", frame, placed.rule, ACCENT)


def _logo_rect(frame: Frame, logo_aspect: float) -> tuple[float, float]:
    """The logo's box, in the logo's own proportions.

    Sized by area rather than by a fixed height, so a tall mark (a crest, a photo
    used as a logo) gets as much presence as a wide wordmark instead of shrinking to
    a sliver - within a ceiling on each side.
    """
    short = frame.short
    if logo_aspect <= 0:
        return LOGO_W * short, LOGO_H * short
    area = LOGO_W * LOGO_H * short * short
    w, h = (area * logo_aspect) ** 0.5, (area / logo_aspect) ** 0.5
    shrink = min(1.0, LOGO_MAX_W * short / w, LOGO_MAX_H * short / h)
    return w * shrink, h * shrink


def _logo(frame: Frame, x: float, y: float, logo_aspect: float) -> dict[str, Any]:
    w, h = _logo_rect(frame, logo_aspect)
    return {
        "type": "logo",
        "id": "logo",
        "box": frame.box(Rect(x, y, w, h)).model_dump(),
        "variant": "full",
        "safe_area": 0.02,
    }


def _logo_x(zone: Rect, width: float, align: str) -> float:
    return zone.x if align == "left" else zone.x + (zone.w - width) / 2


def _words(
    frame: Frame,
    copy: Copy,
    kit: BrandKit,
    root: Path,
    zone: Rect,
    choice: Choice,
    anchor: Anchor,
    *,
    big: bool = False,
) -> Placed | None:
    words = stack(
        copy,
        frame,
        kit,
        root,
        width=zone.w,
        max_height=zone.h,
        scale=_scale(choice),
        rule=choice["accent"] == "rule",
        big=big,
    )
    if words is None:
        return None
    return place(
        words,
        zone,
        anchor,
        rule_w=RULE_W * frame.short,
        align=choice["align"],
    )


def _scale(choice: Choice) -> Scale:
    return cast(Scale, choice["scale"])


def _clip_to_safe(
    frame: Frame, zone: Rect, *, min_h: float = 0.18, min_w: float = 0.3
) -> Rect | None:
    """The part of `zone` where words or a logo may go; None if too small to use.
    Minimums are shares of the shorter side."""
    safe = frame.safe
    top, bottom = max(zone.y, safe.y), min(zone.bottom, safe.bottom)
    left, right = max(zone.x, safe.x), min(zone.right, safe.right)
    if bottom - top < min_h * frame.short or right - left < min_w * frame.short:
        return None
    return Rect(left, top, right - left, bottom - top)


# How much the photo/words split moves in the sizes that are short on room for words.
# A square has less height than a feed post; a story may only use its middle band.
PANEL_SHIFT = {"4:5": 0.0, "1:1": 0.06, "9:16": 0.08, "16:9": 0.0}
PAIR_SHIFT = {"4:5": 0.0, "1:1": -0.06, "9:16": -0.08, "16:9": 0.0}


def _logo_then_words(
    frame: Frame,
    copy: Copy,
    kit: BrandKit,
    root: Path,
    zone: Rect,
    photo: Rect,
    choice: Choice,
    logo_aspect: float,
    *,
    logo_with_words: bool,
    photo_align: str,
) -> tuple[Placed, float, float] | None:
    """Words in `zone`, and the logo either above them or on the photo.

    A designer moves the logo to the photo when the words need the room, and so does
    this: in a size too cramped for the chosen place, the other place is tried.
    """
    m = frame.margin
    logo_w, logo_h = _logo_rect(frame, logo_aspect)
    for with_words in (logo_with_words, not logo_with_words):
        if with_words:
            step = logo_h + 0.05 * frame.short
            words_zone = Rect(zone.x, zone.y + step, zone.w, zone.h - step)
            if words_zone.h <= 0:
                continue
            placed = _words(frame, copy, kit, root, words_zone, choice, "center")
            if placed is not None:
                return placed, _logo_x(zone, logo_w, choice["align"]), zone.y
        else:
            on_photo = _clip_to_safe(
                frame,
                photo.inset(m, m, m, m),
                min_h=LOGO_H * 0.6,
                min_w=LOGO_W * 0.6,
            )
            if on_photo is None:
                continue
            placed = _words(frame, copy, kit, root, zone, choice, "center")
            if placed is not None:
                return placed, _logo_x(on_photo, logo_w, photo_align), on_photo.y
    return None


# --- 1. panel: a photo and a solid panel of brand colour ------------------------------


def _panel(
    frame: Frame, choice: Choice, copy: Copy, kit: BrandKit, root: Path, logo_aspect: float
) -> Layers | None:
    share = float(choice["share"]) + PANEL_SHIFT[frame.aspect]
    page, m = frame.page, frame.margin
    words_after = choice["side"] == "end"
    if frame.landscape:
        cut = page.w * (1 - share) if words_after else page.w * share
        photo = Rect(0, 0, cut, page.h) if words_after else Rect(cut, 0, page.w - cut, page.h)
        panel = Rect(cut, 0, page.w - cut, page.h) if words_after else Rect(0, 0, cut, page.h)
    else:
        cut = page.h * (1 - share) if words_after else page.h * share
        photo = Rect(0, 0, page.w, cut) if words_after else Rect(0, cut, page.w, page.h - cut)
        panel = Rect(0, cut, page.w, page.h - cut) if words_after else Rect(0, 0, page.w, cut)
    fill, ink = FILL[choice["fill"]]
    zone = _clip_to_safe(frame, panel.inset(m, m, m, m))
    if zone is None:
        return None
    laid = _logo_then_words(
        frame,
        copy,
        kit,
        root,
        zone,
        photo,
        choice,
        logo_aspect,
        logo_with_words=choice["logo"] == "panel",
        photo_align=choice["align"],
    )
    if laid is None:
        return None
    placed, logo_x, logo_y = laid
    return [
        _image("bg", frame, photo, source="client", fit="cover"),
        _shape("panel", frame, panel, fill),
        _rule(frame, placed),
        *_texts(frame, placed, ink, choice["align"]),
        _logo(frame, logo_x, logo_y, logo_aspect),
    ]


# --- 2. overlay: a full-bleed photo, the words on a scrim, band or card ----------------

TREATMENT = {
    # fill, opacity, text colour
    "scrim": ("palette.text", 0.62, "palette.text_on_dark"),
    "band": ("palette.primary", 0.92, "palette.text_on_primary"),
    "card": ("palette.surface", 0.96, "palette.text"),
}


def _overlay(
    frame: Frame, choice: Choice, copy: Copy, kit: BrandKit, root: Path, logo_aspect: float
) -> Layers | None:
    page, safe, short = frame.page, frame.safe, frame.short
    fill, opacity, ink = TREATMENT[choice["treatment"]]
    pad = PAD * short
    card = choice["treatment"] == "card"
    inner = safe.inset(pad, 0, pad, 0) if card else safe
    budget = Rect(inner.x, inner.y, inner.w, inner.h * 0.62)
    words = stack(
        copy,
        frame,
        kit,
        root,
        width=budget.w,
        max_height=budget.h - (2 * pad if card else pad),
        scale=_scale(choice),
        rule=choice["accent"] == "rule",
    )
    if words is None:
        return None
    bottom = choice["zone"] == "bottom"
    zone_y = (
        (safe.bottom - (pad if card else 0) - words.height_px)
        if bottom
        else safe.y + (pad if card else 0)
    )
    zone = Rect(inner.x, zone_y, inner.w, words.height_px)
    placed = place(words, zone, "top", rule_w=RULE_W * short, align=choice["align"])
    if card:
        backing = Rect(safe.x, placed.top - pad, safe.w, placed.bottom - placed.top + 2 * pad)
    elif bottom:
        backing = Rect(0, placed.top - pad, page.w, page.h - (placed.top - pad))
    else:
        backing = Rect(0, 0, page.w, placed.bottom + pad)
    logo_w, logo_h = _logo_rect(frame, logo_aspect)
    logo_x = safe.x if choice["logo"] == "start" else safe.right - logo_w
    logo_y = safe.y if bottom else safe.bottom - logo_h
    if bottom and logo_y + logo_h > backing.y - 0.03 * short:
        return None
    if not bottom and logo_y < backing.bottom + 0.03 * short:
        return None
    extra = {"shape": "rounded", "radius": 0.08} if card else {}
    return [
        _image("bg", frame, page, source="client", fit="cover"),
        _shape("backing", frame, backing, fill, opacity=opacity, **extra),
        _rule(frame, placed),
        *_texts(frame, placed, ink, choice["align"]),
        _logo(frame, logo_x, logo_y, logo_aspect),
    ]


# --- 3. pair: two of the client's own photos, and the words ---------------------------


def _pair(
    frame: Frame, choice: Choice, copy: Copy, kit: BrandKit, root: Path, logo_aspect: float
) -> Layers | None:
    share = float(choice["share"]) + PAIR_SHIFT[frame.aspect]
    page, m, short = frame.page, frame.margin, frame.short
    gutter = GUTTER * short if choice["gutter"] == "thin" else 0.0
    if frame.landscape:
        cut = page.w * share
        photos = Rect(0, 0, cut, page.h)
        text_area = Rect(cut, 0, page.w - cut, page.h)
    else:
        cut = page.h * share
        photos = Rect(0, 0, page.w, cut)
        text_area = Rect(0, cut, page.w, page.h - cut)
    half = (photos.w - gutter) / 2
    photo_a = Rect(photos.x, photos.y, half, photos.h)
    photo_b = Rect(photos.x + half + gutter, photos.y, half, photos.h)
    fill, ink = FILL[choice["fill"]]
    zone = _clip_to_safe(frame, text_area.inset(m, m, m, m))
    if zone is None:
        return None
    laid = _logo_then_words(
        frame,
        copy,
        kit,
        root,
        zone,
        photo_a,
        choice,
        logo_aspect,
        logo_with_words=choice["logo"] == "text",
        photo_align="left",
    )
    if laid is None:
        return None
    placed, logo_x, logo_y = laid
    return [
        _shape("base", frame, page, fill),
        _image("photo_a", frame, photo_a, source="client", fit="cover"),
        _image("photo_b", frame, photo_b, source="client", fit="cover"),
        _rule(frame, placed),
        *_texts(frame, placed, ink, choice["align"]),
        _logo(frame, logo_x, logo_y, logo_aspect),
    ]


# --- 4. type: no photo, the words are the design --------------------------------------


def _type(
    frame: Frame, choice: Choice, copy: Copy, kit: BrandKit, root: Path, logo_aspect: float
) -> Layers | None:
    if choice["zone"] == "bottom" and choice["logo"] == "bottom":
        return None  # the logo would sit on the words
    page, safe, short = frame.page, frame.safe, frame.short
    fill, ink = FILL[choice["fill"]]
    logo_w, logo_h = _logo_rect(frame, logo_aspect)
    step = logo_h + 0.06 * short
    top_logo = choice["logo"] == "top"
    zone = Rect(safe.x, safe.y + (step if top_logo else 0), safe.w, safe.h - step)
    anchor: Anchor = "bottom" if choice["zone"] == "bottom" else "center"
    placed = _words(frame, copy, kit, root, zone, choice, anchor, big=True)
    if placed is None:
        return None
    logo_x = _logo_x(safe, logo_w, choice["align"])
    logo_y = safe.y if top_logo else safe.bottom - logo_h
    return [
        _shape("base", frame, page, fill),
        _rule(frame, placed),
        *_texts(frame, placed, ink, choice["align"]),
        _logo(frame, logo_x, logo_y, logo_aspect),
    ]


FAMILIES: dict[str, Family] = {
    family.name: family
    for family in (
        Family(
            name="panel",
            title="Photo and panel",
            params=(
                ("side", ("end", "start")),
                ("share", ("0.42", "0.5", "0.58")),
                ("fill", ("primary", "surface", "dark")),
                ("align", ("left", "center")),
                ("accent", ("rule", "none")),
                ("logo", ("photo", "panel")),
                ("scale", ("bold", "calm")),
            ),
            photos={"bg": "background"},
            build=_panel,
        ),
        Family(
            name="overlay",
            title="Photo with words over it",
            params=(
                ("zone", ("bottom", "top")),
                ("treatment", ("scrim", "band", "card")),
                ("align", ("left", "center")),
                ("accent", ("rule", "none")),
                ("logo", ("start", "end")),
                ("scale", ("bold", "calm")),
            ),
            photos={"bg": "background"},
            build=_overlay,
        ),
        Family(
            name="pair",
            title="Two photos",
            params=(
                ("share", ("0.5", "0.58")),
                ("gutter", ("thin", "none")),
                ("fill", ("surface", "primary", "dark")),
                ("align", ("left", "center")),
                ("accent", ("rule", "none")),
                ("logo", ("text", "photo")),
                ("scale", ("bold", "calm")),
            ),
            photos={"photo_a": "photo", "photo_b": "photo"},
            build=_pair,
        ),
        Family(
            name="type",
            title="Words only",
            params=(
                ("fill", ("primary", "surface", "dark")),
                ("align", ("left", "center")),
                ("accent", ("rule", "none")),
                ("zone", ("center", "bottom")),
                ("logo", ("top", "bottom")),
                ("scale", ("bold", "calm")),
            ),
            photos={},
            build=_type,
        ),
    )
}
