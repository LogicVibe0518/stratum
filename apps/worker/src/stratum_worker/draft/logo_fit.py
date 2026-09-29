"""Make sure the logo can be seen, without touching the logo.

A template is designed once, for every client, so it cannot know the logo's
colours. A navy logo on a navy band, or a cream logo on a cream page, disappears.
The rule we will not break is that the logo is the client's own file, never
recoloured. So the fix is what the logo sits on:

1. As designed, if the logo already stands out there.
2. Otherwise on a light panel, or on a dark one, whichever lets more of the logo
   stand out. If the template gave the logo a see-through chip, the panel replaces
   it.

"Stands out" is measured, not judged: each colour in the logo, weighted by how much
of the logo it covers, against the colour right behind it, at WCAG's 3:1 for
graphics. At least three quarters of the logo must clear it, in EVERY size the post
is made in, because a story moves the logo somewhere else.

A logo file with its own solid background (a JPEG on white) carries its own backdrop
wherever it goes, so it is left alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from stratum_spec import BrandKit, LogoLayer, LogoPlate, PosterSpec, retarget
from stratum_spec.colour import blend, contrast_ratio
from stratum_spec.models import Layer
from stratum_spec.templates import Variants

from stratum_worker.brand import LogoReading
from stratum_worker.draft.backdrop import behind

# WCAG 2.2's floor for graphics a person needs to see (1.4.11 Non-text Contrast).
LEGIBLE = 3.0
# How much of the logo, by area, has to clear that floor.
ENOUGH = 0.75

LIGHT_PLATE = LogoPlate(fill="palette.surface", opacity=0.96)
DARK_PLATE = LogoPlate(fill="palette.text", opacity=0.92)


@dataclass(frozen=True)
class LogoInk:
    """The colours of a logo and how much of it each covers."""

    colours: tuple[tuple[str, float], ...]
    # True when the file has a solid background of its own: it shows on anything.
    self_backed: bool = False

    @classmethod
    def from_reading(cls, reading: LogoReading) -> LogoInk:
        return cls(
            colours=tuple((s.hex, s.share) for s in reading.swatches),
            self_backed=not reading.transparent,
        )


def visible_share(ink: LogoInk, backdrop: str) -> float:
    """How much of the logo stands out against `backdrop`, from 0 to 1."""
    total = sum(share for _, share in ink.colours)
    if total <= 0:
        return 1.0
    seen = sum(
        share
        for hex_colour, share in ink.colours
        if contrast_ratio(hex_colour, backdrop) >= LEGIBLE
    )
    return seen / total


def logo_backdrop(spec: PosterSpec, index: int, kit: BrandKit) -> str:
    """The colour directly behind the logo at `index`, its own panel included."""
    logo = spec.layers[index]
    under = behind(spec, index, logo.box, kit)
    if isinstance(logo, LogoLayer) and logo.plate is not None:
        return blend(kit.color(logo.plate.fill), under, logo.plate.opacity)
    return under


def _seen(
    spec: PosterSpec,
    layer_id: str,
    kit: BrandKit,
    ink: LogoInk,
    variants: Variants,
    aspects: Iterable[str],
) -> float:
    """The worst visible share of the logo across every size of the post."""
    worst = 1.0
    for aspect in aspects:
        view = retarget(spec, aspect, variants)
        index = next(i for i, layer in enumerate(view.layers) if layer.id == layer_id)
        worst = min(worst, visible_share(ink, logo_backdrop(view, index, kit)))
    return worst


def _with(spec: PosterSpec, changes: Mapping[str, dict[str, object]]) -> PosterSpec:
    layers: list[Layer] = [
        type(layer).model_validate({**layer.model_dump(), **changes[layer.id]})
        if layer.id in changes
        else layer
        for layer in spec.layers
    ]
    return spec.model_copy(update={"layers": layers})


def fit_logo(
    spec: PosterSpec,
    kit: BrandKit,
    ink: LogoInk,
    variants: Variants,
    aspects: Iterable[str],
) -> PosterSpec:
    """The same poster, with a panel behind any logo that would not otherwise show."""
    if ink.self_backed:
        return spec
    sizes = list(dict.fromkeys([spec.canvas.aspect, *aspects]))
    ids = {layer.id for layer in spec.layers}
    for logo in [one for one in spec.layers if isinstance(one, LogoLayer)]:
        if not logo.visible or logo.asset is None:
            continue
        if _seen(spec, logo.id, kit, ink, variants, sizes) >= ENOUGH:
            continue
        chip = f"{logo.id}_chip"
        options = []
        for plate in (LIGHT_PLATE, DARK_PLATE):
            changes: dict[str, dict[str, object]] = {logo.id: {"plate": plate.model_dump()}}
            if chip in ids:
                changes[chip] = {"visible": False}
            candidate = _with(spec, changes)
            options.append((_seen(candidate, logo.id, kit, ink, variants, sizes), candidate))
        # Best first; on a tie the light panel, which is how most brands present a logo.
        spec = max(options, key=lambda option: option[0])[1]
    return spec


def check_logos(
    spec: PosterSpec, kit: BrandKit, inks: Mapping[str, LogoInk]
) -> list[tuple[str, float]]:
    """(layer id, visible share) for every visible logo that does not stand out."""
    hidden: list[tuple[str, float]] = []
    for index, layer in enumerate(spec.layers):
        if not isinstance(layer, LogoLayer) or not layer.visible or layer.asset is None:
            continue
        ink = inks.get(layer.asset)
        if ink is None or ink.self_backed:
            continue
        share = visible_share(ink, logo_backdrop(spec, index, kit))
        if share < ENOUGH:
            hidden.append((layer.id, share))
    return hidden


__all__ = [
    "ENOUGH",
    "LEGIBLE",
    "LogoInk",
    "check_logos",
    "fit_logo",
    "logo_backdrop",
    "visible_share",
]
