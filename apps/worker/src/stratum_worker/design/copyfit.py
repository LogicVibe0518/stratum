"""Copy that fits (M10e): each design sets the headline that suits it best.

The model writes the headline three ways (post.copy v2): the main one, a short one
with the words a passer-by must not miss, and a fuller one with one more true fact.
One string written for one budget either shrinks until it whispers or, at the
smallest size, is cut short with an ellipsis. With three, a roomy words-only design
can carry the fuller line and a tight overlay the short one.

For each design, every version is laid out (the design space does the measuring),
and the main headline - the one written to the brief's budget - is kept unless
another version is clearly better in this design:
- the main one does not fit: the biggest version that does;
- the main one is cramped (three lines or more) and the short one sets at least a
  quarter bigger: the short one;
- the fuller one sets at least 90% as big on at most three lines: the fuller one,
  which says more at the same impact.

Pure: no database, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from stratum_spec import BrandKit, PosterSpec
from stratum_spec.models import TextLayer

from stratum_worker.design.space import lay_out
from stratum_worker.design.typeset import Copy
from stratum_worker.render.layout import place_text

CRAMPED_LINES = 3  # a main headline on this many lines may give way to the short one
SHORT_GAIN = 1.25  # ...if the short one sets at least this much bigger
LONG_KEEPS = 0.9  # the fuller one replaces the main one if it keeps this much of its size
LONG_LINES = 3


@dataclass(frozen=True)
class Fitted:
    copy: Copy
    laid: PosterSpec
    size_px: float
    lines: int
    main: bool


def versions(words: dict[str, str]) -> list[tuple[Copy, bool]]:
    """The headline versions to try, main first, each once: (copy, is_main)."""
    offer, cta = words.get("offer_detail", ""), words.get("cta", "")
    out: list[tuple[Copy, bool]] = []
    seen: set[str] = set()
    for key in ("headline", "headline_short", "headline_long"):
        text = (words.get(key) or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append((Copy(text, offer, cta), key == "headline"))
    return out


def _set(laid: PosterSpec, headline: str, kit: BrandKit, root: Path) -> tuple[float, int]:
    """How big, and on how many lines, the headline is drawn in this layout."""
    layer = laid.layer("headline")
    if not isinstance(layer, TextLayer):
        return 0.0, 0
    shown = layer.model_copy(update={"content": headline})
    placed = place_text(shown, laid, kit, root)
    return placed.size_px, len(placed.lines)


def pick(fitted: Sequence[Fitted]) -> Fitted:
    """Which version this design should set (see the module notes)."""
    main = next((one for one in fitted if one.main), None)
    if main is None:
        return max(fitted, key=lambda one: one.size_px)
    shorter = [one for one in fitted if len(one.copy.headline) < len(main.copy.headline)]
    longer = [one for one in fitted if len(one.copy.headline) > len(main.copy.headline)]
    for one in longer:
        if one.size_px >= LONG_KEEPS * main.size_px and one.lines <= LONG_LINES:
            return one
    if main.lines >= CRAMPED_LINES:
        for one in shorter:
            if one.size_px >= SHORT_GAIN * main.size_px:
                return one
    return main


def best_copy(
    design_id: str,
    options: Sequence[tuple[Copy, bool]],
    kit: BrandKit,
    root: Path,
    *,
    logo_aspect: float = 0.0,
) -> tuple[Copy, PosterSpec] | None:
    """The headline version this design sets best, and the design laid out around it;
    None when no version fits the design in every size."""
    fitted = []
    for copy, main in options:
        laid = lay_out(design_id, kit, root, copy=copy, logo_aspect=logo_aspect)
        if laid is None:
            continue
        if len(options) == 1:
            return copy, laid  # nothing to choose between: skip the measuring
        size, lines = _set(laid, copy.headline, kit, root)
        fitted.append(Fitted(copy, laid, size, lines, main))
    if not fitted:
        return None
    winner = pick(fitted)
    return winner.copy, winner.laid


__all__ = ["Fitted", "best_copy", "pick", "versions"]
