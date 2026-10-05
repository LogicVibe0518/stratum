"""Taste Lab screens (taste plan step 1): which designs to put side by side.

Critic v1 learned "an original beats a damaged copy of itself". It never saw two good
designs compared, so among valid designs it mostly scores noise. Taste only comes
from people choosing, so the lab shows a person four designs for one real post and
asks which they would send the client.

What goes on a screen decides what can be learned from it:
- two from the top: the close calls between good designs, which is exactly what v1
  cannot judge. "The top" is the ranking's first TOP plus each family's best
  FAMILY_TOP, so a family the critic happens to rank low still meets people;
- one from the middle of the ranking (after the top, up to two thirds of the way
  down): does the ranking's order mean anything;
- one drawn from every valid design: a check on what the ranking never shows.
Each design keeps its bucket and rank, so the walkthrough can ask "do people prefer
the ranking's top?" with numbers. Positions are shuffled.

A comparison only teaches something if a person can see the difference. The first
live batch (5 Oct) showed screens of four panel designs on one photo that differed
by an alignment or a thin rule: people choose at random between near-twins, and a
coin flip is a label that teaches nothing. So:
- no two designs on a screen look alike (`apart` < MIN_APART), when the ranking
  offers anything else;
- across a post's screens, the top picks rotate through the families, and designs
  already shown on this post are used again only when nothing new is left.

`apart` is the mean colour difference (CIELAB delta E 1976) between small renders
(LOOK_WIDTH px wide) of two designs: what a person sees at a glance, measured on
what is drawn rather than on the settings that drew it. Calibrated on the 5 Oct
Sparkle Squad post: pairs below 6 differed only by alignment or which headline
version was used; from about 9 the headline size or the panel changed. The floor
is 8.

Pure: no database, no network.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from stratum_worker.design.choose import Candidate

TOP = 12  # "the top of the ranking": a shortlist a person would plausibly see
FAMILY_TOP = 2  # each family's best two count as the top too
SIZE = 4
# "The middle of the ranking": after the top, up to two thirds of the way down. Wide
# enough that a post's six screens do not all show the same middle design.
MIDDLE_END = 2 / 3
MIN_APART = 8.0  # mean delta E below which two designs look the same at a glance
LOOK_WIDTH = 96  # px: the small render `apart` compares

Look = npt.NDArray[np.float64]  # rows x cols x 3, CIELAB


@dataclass(frozen=True)
class LabDesign:
    candidate: Candidate
    bucket: str  # "top", "middle" or "any"
    rank: int  # 0 = the ranking's best


@dataclass
class Shown:
    """What a post's earlier screens already showed: kept across its screens so they
    cover the design space instead of repeating its top."""

    designs: Counter[str]
    families: Counter[str]

    @classmethod
    def nothing(cls) -> Shown:
        return cls(Counter(), Counter())


# --- what a person sees ------------------------------------------------------------------

_XYZ = np.array(  # linear sRGB to XYZ (IEC 61966-2-1)
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_WHITE = _XYZ.sum(axis=1)  # D65: the white sRGB is built on
_EPS = (6 / 29) ** 3


def cielab(rgb: npt.ArrayLike) -> Look:
    """sRGB (0..1, rows x cols x 3) to CIELAB, D65 white."""
    value = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    linear = np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)
    xyz = linear @ _XYZ.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    lab: Look = np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )
    return lab


def apart(one: Look, two: Look) -> float:
    """How different two designs look: mean delta E 1976 between their small renders.
    0 is identical; black against white is 100. Looks of different shapes (posters of
    different sizes) are as different as can be."""
    if one.shape != two.shape:
        return float("inf")
    return float(np.sqrt(((one - two) ** 2).sum(axis=-1)).mean())


# --- choosing a screen -------------------------------------------------------------------


def _top(ranked: Sequence[Candidate]) -> list[int]:
    """The ranking's first TOP, then each family's best FAMILY_TOP."""
    out = list(range(min(TOP, len(ranked))))
    per_family: Counter[str] = Counter()
    for index, candidate in enumerate(ranked):
        if per_family[candidate.family] < FAMILY_TOP:
            per_family[candidate.family] += 1
            if index not in out:
                out.append(index)
    return out


def lab_screen(
    ranked: Sequence[Candidate],
    rng: random.Random,
    size: int = SIZE,
    *,
    looks: Sequence[Look] | None = None,
    shown: Shown | None = None,
) -> list[LabDesign] | None:
    """`size` different designs for one screen, shuffled, or None if fewer than two
    exist. `ranked` is best first (choose.candidates); `looks[i]` is how ranked[i]
    looks (optional: without looks, designs are only kept different by id); `shown`
    is updated with what this screen uses."""
    if len(ranked) < 2:
        return None
    if looks is not None and len(looks) != len(ranked):
        raise ValueError("one look per ranked design")
    shown = shown if shown is not None else Shown.nothing()
    everything = list(range(len(ranked)))
    top = _top(ranked)
    middle_end = max(TOP + 1, round(MIDDLE_END * len(ranked)))
    middle = [i for i in everything[TOP:middle_end] if i not in top]
    picked: list[int] = []
    buckets: list[str] = []

    def distance(index: int) -> float:
        """How far `index` looks from the nearest design already on the screen."""
        if looks is None or not picked:
            return float("inf")
        return min(apart(looks[index], looks[other]) for other in picked)

    def draw(pool: Sequence[int], tier: Callable[[int], tuple[int, ...]]) -> int | None:
        free = [i for i in pool if i not in picked]
        if not free:
            return None
        visible = [i for i in free if distance(i) >= MIN_APART]
        if not visible:  # everything left is a near-twin: the least alike will do
            return max(free, key=lambda i: (distance(i), -i))
        best = min(tier(i) for i in visible)
        return rng.choice([i for i in visible if tier(i) == best])

    def family_on_screen(index: int) -> int:
        return int(any(ranked[index].family == ranked[other].family for other in picked))

    def top_tier(index: int) -> tuple[int, ...]:
        candidate = ranked[index]
        return (
            family_on_screen(index),
            shown.families[candidate.family],
            shown.designs[candidate.design_id],
        )

    def fresh_tier(index: int) -> tuple[int, ...]:
        return (shown.designs[ranked[index].design_id],)

    plan: list[tuple[str, Sequence[int], Callable[[int], tuple[int, ...]]]] = [
        ("top", top, top_tier),
        ("top", top, top_tier),
        ("middle", middle, fresh_tier),
    ]
    for bucket, pool, tier in plan:
        found = draw(pool, tier)
        if found is None and bucket == "middle":
            found = draw(everything, fresh_tier)
        if found is not None:
            picked.append(found)
            buckets.append(bucket)
    while len(picked) < size:
        found = draw(everything, fresh_tier)
        if found is None:
            break
        picked.append(found)
        buckets.append("any")
    if len(picked) < 2:
        return None
    for index in picked:
        shown.designs[ranked[index].design_id] += 1
        shown.families[ranked[index].family] += 1
    screen = [
        LabDesign(ranked[index], bucket, index)
        for index, bucket in zip(picked, buckets, strict=True)
    ]
    rng.shuffle(screen)
    return screen


def closest(screen: Sequence[LabDesign], looks: Sequence[Look]) -> float:
    """The smallest `apart` between two designs on a screen: how hard its hardest
    comparison is. `looks` is aligned with the ranking the screen was drawn from."""
    ranks = [one.rank for one in screen]
    pairs = [apart(looks[a], looks[b]) for n, a in enumerate(ranks) for b in ranks[n + 1 :]]
    return min(pairs, default=float("inf"))


__all__ = [
    "FAMILY_TOP",
    "LOOK_WIDTH",
    "MIDDLE_END",
    "MIN_APART",
    "SIZE",
    "TOP",
    "LabDesign",
    "Look",
    "Shown",
    "apart",
    "cielab",
    "closest",
    "lab_screen",
]
