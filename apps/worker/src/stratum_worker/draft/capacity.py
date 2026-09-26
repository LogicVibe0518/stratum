"""How many characters actually fit in a text layer.

The first version of this was a hand-written table of guesses per template, and the
guesses were wrong often enough that eight of thirty-five posters came back truncated.
Guessing was never necessary: the renderer already knows, because it does the fitting.

So we ask it. A sample string is grown by binary search until the real auto-fit
function starts truncating, and the largest length that survives is the capacity. The
number cannot disagree with the renderer, because it IS the renderer.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from stratum_spec.brand import BrandKit
from stratum_spec.models import PosterSpec, TextLayer

from stratum_worker.render.text import fit, load_metrics

# Ordinary English letter frequencies, so the measurement reflects real copy rather
# than a worst case of all-W or a best case of all-i.
SAMPLE = "the quick brown fox jumps over a lazy dog and then rests beside the river "
# Real words break at spaces and leave ragged ends; a continuous sample packs better
# than a sentence does. Nine tenths of the measured figure is what survives contact.
SAFETY = 0.9
MAX_CHARS = 400
# Measuring down to the layer's minimum size answers "what can be crammed in", which
# is the wrong question - copy that only fits at the smallest permitted size is the
# copy preflight then flags as hard to read on a phone. The floor is raised a quarter
# of the way towards the maximum instead, so the measured budget is what fits while
# still rendering at a readable size.
COMFORTABLE = 0.25


def _sample(length: int, *, upper: bool) -> str:
    repeats = (length // len(SAMPLE)) + 1
    text = (SAMPLE * repeats)[:length]
    return text.upper() if upper else text


@cache
def _capacity(
    font_file: str,
    box_w_px: float,
    box_h_px: float,
    min_px: float,
    max_px: float,
    max_lines: int,
    line_height: float,
    upper: bool,
) -> int:
    metrics = load_metrics(font_file)

    def truncates(length: int) -> bool:
        fitted = fit(
            _sample(length, upper=upper),
            metrics,
            box_w_px=box_w_px,
            box_h_px=box_h_px,
            min_px=min_px,
            max_px=max_px,
            max_lines=max_lines,
            line_height=line_height,
        )
        return bool(fitted.lines) and fitted.lines[-1].endswith("…")

    low, high = 1, MAX_CHARS
    if not truncates(high):
        return high
    while low < high:
        mid = (low + high + 1) // 2
        if truncates(mid):
            high = mid - 1
        else:
            low = mid
    return low


def measure(template: PosterSpec, kit: BrandKit, *, root: Path) -> dict[str, int]:
    """Characters that fit in each text layer of this template, in this brand.

    Brand matters: a condensed display face fits half again as many characters in the
    same box as a wide one, so the budget is per client, not per template.
    """
    width, height = template.canvas.size
    measured: dict[str, int] = {}
    for layer in template.layers:
        if not isinstance(layer, TextLayer):
            continue
        face = kit.font(layer.font)
        measured[layer.id] = int(
            _capacity(
                str(root / face.file),
                layer.box.w * width,
                layer.box.h * height,
                (layer.min_size + COMFORTABLE * (layer.max_size - layer.min_size)) * height,
                layer.max_size * height,
                layer.max_lines,
                face.line_height,
                layer.uppercase,
            )
            * SAFETY
        )
    return measured


__all__ = ["measure"]
