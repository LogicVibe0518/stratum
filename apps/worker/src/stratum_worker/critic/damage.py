"""Breaking good designs on purpose, by known amounts, to teach the critic.

Every design the design space makes is correct. To learn what makes one design
better than another, the critic first learns what makes a design worse: each
operator here does one thing a rushed designer does - nudges a line out of
alignment, jams the words against the edge, lets the offer run into the headline,
shrinks the small print until nobody can read it, drops the scrim so the words sit
on a busy photo - at three strengths. A design and its damaged copies form one
group, ranked: untouched 3, then 2, 1, 0 as the damage grows.

What this teaches is "broken vs fine". Choosing between two fine designs is taste,
and taste has to come from people (M12d/e) - the model card says so.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from stratum_spec.brand import BrandKit
from stratum_spec.colour import contrast_ratio
from stratum_spec.models import Box, ImageLayer, LogoLayer, PosterSpec, ShapeLayer, TextLayer

from stratum_worker.draft.backdrop import behind

Damage = Callable[[PosterSpec, int, random.Random, BrandKit], PosterSpec | None]
LEVELS = (1, 2, 3)


def _with(spec: PosterSpec, changes: dict[str, dict[str, Any]]) -> PosterSpec | None:
    """The same poster with some layers changed; None if the result is not valid."""
    layers = [
        {**layer.model_dump(), **changes[layer.id]} if layer.id in changes else layer.model_dump()
        for layer in spec.layers
    ]
    try:
        return PosterSpec.model_validate({**spec.model_dump(), "layers": layers})
    except ValueError:
        return None


def _box(layer: Any, **moves: float) -> dict[str, Any]:
    box: Box = layer.box
    data = box.model_dump()
    for key, delta in moves.items():
        data[key] = data[key] + delta
    return {"box": data}


def _texts(spec: PosterSpec) -> list[TextLayer]:
    return [layer for layer in spec.layers if isinstance(layer, TextLayer) and layer.visible]


def _named(spec: PosterSpec, *ids: str) -> TextLayer | None:
    for layer_id in ids:
        for layer in _texts(spec):
            if layer.id == layer_id:
                return layer
    return None


def misalign(spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit) -> PosterSpec | None:
    """One line of text nudged sideways, off the edge the others share."""
    texts = _texts(spec)
    if len(texts) < 2:
        return None
    victim = rng.choice(texts)
    shift = (0.018, 0.04, 0.075)[level - 1] * rng.choice((-1, 1))
    return _with(spec, {victim.id: _box(victim, x=shift)})


def crowd_edge(
    spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit
) -> PosterSpec | None:
    """All the words pushed towards the side, into the margin."""
    shift = (0.035, 0.055, 0.07)[level - 1] * rng.choice((-1, 1))
    moved = {
        layer.id: _box(layer, x=shift)
        for layer in spec.layers
        if isinstance(layer, TextLayer) or layer.id == "rule"
    }
    return _with(spec, moved) if moved else None


def collide(spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit) -> PosterSpec | None:
    """The offer pulled up into the headline."""
    headline, offer = _named(spec, "headline"), _named(spec, "offer", "quote")
    if headline is None or offer is None or offer.box.y <= headline.box.y:
        return None
    gap = offer.box.y - (headline.box.y + headline.box.h)
    lift = gap + (0.3, 0.6, 1.0)[level - 1] * offer.box.h
    return _with(spec, {offer.id: _box(offer, y=-lift)})


def tiny_type(spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit) -> PosterSpec | None:
    """The small print shrunk until it strains the eye."""
    factor = (0.78, 0.62, 0.48)[level - 1]
    changes = {}
    for layer in _texts(spec):
        if layer.id in ("offer", "cta", "contact"):
            changes[layer.id] = {
                "min_size": layer.min_size * factor,
                "max_size": layer.max_size * factor,
            }
    return _with(spec, changes) if changes else None


def shout(spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit) -> PosterSpec | None:
    """A headline blown up past its space, crowding everything under it."""
    headline = _named(spec, "headline")
    if headline is None:
        return None
    factor = (1.3, 1.6, 2.0)[level - 1]
    grow = headline.box.h * (factor - 1)
    return _with(
        spec,
        {
            headline.id: {
                **_box(headline, h=grow),
                "max_size": min(1.0, headline.max_size * factor),
                "min_size": min(1.0, headline.min_size * factor),
            }
        },
    )


def strip_backing(
    spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit
) -> PosterSpec | None:
    """The scrim, band or card thinned or dropped, so the words sit on the photo."""
    has_photo = any(isinstance(layer, ImageLayer) and layer.visible for layer in spec.layers)
    texts = _texts(spec)
    if not has_photo or not texts:
        return None
    first_text = min(spec.layers.index(t) for t in texts)
    candidates = [
        layer
        for layer in spec.layers[:first_text]
        if isinstance(layer, ShapeLayer)
        and layer.visible
        and layer.id not in ("rule", "base")
        and layer.box.w * layer.box.h < 0.95  # a page-filling base is not a backing
        and any(isinstance(below, ImageLayer) for below in spec.layers[: spec.layers.index(layer)])
    ]
    if not candidates:
        return None
    backing = candidates[-1]
    opacity = (0.45, 0.2, 0.0)[level - 1]
    change: dict[str, Any] = {"opacity": opacity} if opacity else {"visible": False}
    return _with(spec, {backing.id: change})


def low_contrast(
    spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit
) -> PosterSpec | None:
    """A text colour swapped for a brand colour too close to what is behind it."""
    target = (3.0, 2.0, 1.25)[level - 1]
    for layer in rng.sample(_texts(spec), len(_texts(spec))):
        index = spec.layers.index(layer)
        backdrop = behind(spec, index, layer.box, kit)
        current = contrast_ratio(kit.color(layer.color), backdrop)
        options = [
            (abs(contrast_ratio(hex_, backdrop) - target), f"palette.{name}")
            for name, hex_ in kit.palette.items()
            if contrast_ratio(hex_, backdrop) < current - 0.5
        ]
        if options:
            return _with(spec, {layer.id: {"color": min(options)[1]}})
    return None


def unbalance(spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit) -> PosterSpec | None:
    """The whole text block slid up or down, away from where it belongs."""
    shift = (0.05, 0.1, 0.16)[level - 1] * rng.choice((-1, 1))
    moved = {
        layer.id: _box(layer, y=shift)
        for layer in spec.layers
        if isinstance(layer, TextLayer) or layer.id == "rule"
    }
    return _with(spec, moved) if moved else None


def logo_clash(
    spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit
) -> PosterSpec | None:
    """The logo parked on top of the headline."""
    headline = _named(spec, "headline")
    logo = next((layer for layer in spec.layers if isinstance(layer, LogoLayer)), None)
    if headline is None or logo is None or not logo.visible:
        return None
    overlap = (0.25, 0.55, 0.9)[level - 1]
    x = headline.box.x + headline.box.w * 0.6
    y = headline.box.y - logo.box.h * (1 - overlap)
    return _with(spec, {logo.id: {"box": {**logo.box.model_dump(), "x": x, "y": y}}})


def uneven_gaps(
    spec: PosterSpec, level: int, rng: random.Random, kit: BrandKit
) -> PosterSpec | None:
    """The call to action drifting away from the offer, or jammed up against it."""
    cta = _named(spec, "cta", "contact", "attribution")
    if cta is None:
        return None
    shift = (0.02, 0.045, 0.08)[level - 1] * rng.choice((-1, 1))
    return _with(spec, {cta.id: _box(cta, y=shift)})


DAMAGES: dict[str, Damage] = {
    "misalign": misalign,
    "crowd_edge": crowd_edge,
    "collide": collide,
    "tiny_type": tiny_type,
    "shout": shout,
    "strip_backing": strip_backing,
    "low_contrast": low_contrast,
    "unbalance": unbalance,
    "logo_clash": logo_clash,
    "uneven_gaps": uneven_gaps,
}

__all__ = ["DAMAGES", "LEVELS", "Damage"]
