"""Taste Lab screens (taste plan step 1): which designs to put side by side.

Critic v1 learned "an original beats a damaged copy of itself". It never saw two good
designs compared, so among valid designs it mostly scores noise. Taste only comes
from people choosing, so the lab shows a person four designs for one real post and
asks which they would send the client.

What goes on a screen decides what can be learned from it:
- two from the top of the ranking, of different families where possible: the close
  calls between good designs, which is exactly what v1 cannot judge;
- one from the middle of the ranking: does the ranking's order mean anything;
- one drawn from every valid design: a check on what the ranking never shows.
Each design keeps its bucket and rank, so the walkthrough can ask "do people prefer
the ranking's top?" with numbers. Positions are shuffled.

Pure: no database, no network.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from stratum_worker.design.choose import Candidate

TOP = 12  # "the top of the ranking": a shortlist a person would plausibly see
SIZE = 4


@dataclass(frozen=True)
class LabDesign:
    candidate: Candidate
    bucket: str  # "top", "middle" or "any"
    rank: int  # 0 = the ranking's best


def _draw(
    pool: Sequence[tuple[int, Candidate]],
    rng: random.Random,
    taken: set[str],
    avoid_families: frozenset[str] = frozenset(),
) -> tuple[int, Candidate] | None:
    free = [one for one in pool if one[1].design_id not in taken]
    preferred = [one for one in free if one[1].family not in avoid_families]
    choices = preferred or free
    return rng.choice(choices) if choices else None


def lab_screen(
    ranked: Sequence[Candidate], rng: random.Random, size: int = SIZE
) -> list[LabDesign] | None:
    """`size` different designs for one screen, shuffled, or None if fewer than two
    exist. `ranked` is best first (choose.candidates)."""
    if len(ranked) < 2:
        return None
    indexed = list(enumerate(ranked))
    top = indexed[:TOP]
    middle = indexed[TOP : max(TOP + 1, len(indexed) // 2)]
    picked: list[LabDesign] = []
    taken: set[str] = set()

    def add(found: tuple[int, Candidate] | None, bucket: str) -> None:
        if found is not None:
            rank, candidate = found
            picked.append(LabDesign(candidate, bucket, rank))
            taken.add(candidate.design_id)

    add(_draw(top, rng, taken), "top")
    add(_draw(top, rng, taken, frozenset(one.candidate.family for one in picked)), "top")
    add(_draw(middle, rng, taken) or _draw(indexed, rng, taken), "middle")
    while len(picked) < size:
        found = _draw(indexed, rng, taken)
        if found is None:
            break
        add(found, "any")
    picked = picked[:size]
    if len(picked) < 2:
        return None
    rng.shuffle(picked)
    return picked


__all__ = ["SIZE", "TOP", "LabDesign", "lab_screen"]
