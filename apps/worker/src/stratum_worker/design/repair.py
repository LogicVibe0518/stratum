"""When nothing is good enough (M15c): the quality bar, and what Stratum tries next.

The critic only orders designs: its best of a bad lot is still bad. The quality bar
(critic/bar.py) says whether a design is good enough to send at all. After the
ranking, the top designs are measured against it, and those that clear it go first.
When none of them does, Stratum tries these, cheapest and likeliest first, and stops
at the first that gets a design over the bar:

1. the other photo first, when the client sent two;
2. the shortest headline the copy has, in every design;
3. words-only designs on equal terms (the photo itself may be the problem);
4. a wider search: three times the designs, drawn afresh.

A darker photo under the words is not a step, though the chat can do it: the
critic's measurements assume any photo is mid-grey (draft/backdrop.py), so the bar
could not tell whether darkening helped. Measuring the real photo under the words
comes first (issue I-36).

If nothing clears the bar, the attempt that came closest is kept and the post is
flagged `below_bar`, saying what was tried, so a person looks before it goes to the
client. The bar never sends or holds anything back by itself: it orders and flags.

With no bar shipped (critic/model/bar.json), this does nothing and costs nothing.

Pure: no database, no network.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from stratum_spec.models import Aspect

from stratum_worker.critic.bar import Bar, active_bar
from stratum_worker.critic.features import measure
from stratum_worker.design import choose as chooser
from stratum_worker.design.choose import Candidate, Material, NothingFits
from stratum_worker.draft.models import Flag

TOP = 12  # how many of a ranking are measured against the bar
WIDER = 3  # the wider search draws this many times the usual number of designs
BUDGET_S = 30.0  # repairs stop starting after this long; the draft is a queued job


@dataclass(frozen=True)
class Tried:
    step: str
    best: float  # the lowest chance of "would not send" among the top designs
    cleared: int  # how many of the top designs cleared the bar


@dataclass(frozen=True)
class Outcome:
    ranked: list[Candidate]
    tried: tuple[Tried, ...] = ()
    cleared: bool = True
    skipped: tuple[str, ...] = ()  # steps not started for lack of time

    @property
    def flag(self) -> Flag | None:
        if self.cleared:
            return None
        steps = [one.step for one in self.tried[1:]]
        also = f" It also tried {_listed(steps)}." if steps else ""
        return Flag(
            code="below_bar",
            message=(
                "None of the designs looks ready to send yet (Stratum's quality bar, "
                f"learned from designers' choices).{also} Look closely before this goes "
                "to the client: a calmer or sharper photo, or a shorter headline, "
                "usually helps."
            ),
        )


def _listed(steps: Sequence[str]) -> str:
    return steps[0] if len(steps) == 1 else ", ".join(steps[:-1]) + " and " + steps[-1]


def judged(
    ranked: Sequence[Candidate], material: Material, root: Path, bar: Bar
) -> list[Candidate]:
    """The top designs measured against the bar (their chance of "would not send" in
    `parts["p_bad"]`), those that clear it first; the rest after, unmeasured."""
    top, rest = list(ranked[:TOP]), list(ranked[TOP:])
    if not top:
        return []
    edges = dict(material.edges)
    x = np.array([measure(one.spec, material.kit, root, edges=edges) for one in top])
    p = bar.p_bad(x)
    marked = [
        replace(one, parts={**one.parts, "p_bad": round(float(value), 4)})
        for one, value in zip(top, p.tolist(), strict=True)
    ]
    over = [one for one in marked if one.parts["p_bad"] <= bar.threshold]
    under = [one for one in marked if one.parts["p_bad"] > bar.threshold]
    return [*over, *under, *rest]


def _summary(step: str, ranked: Sequence[Candidate], bar: Bar) -> Tried:
    measured = [one.parts["p_bad"] for one in ranked if "p_bad" in one.parts]
    return Tried(
        step=step,
        best=min(measured) if measured else float("nan"),
        cleared=sum(value <= bar.threshold for value in measured),
    )


def _shortest(material: Material) -> Material | None:
    """The material with only its shortest headline, or None when the headline in use
    is already the shortest."""
    words = dict(material.words)
    main = (words.get("headline") or "").strip()
    short = (words.get("headline_short") or "").strip()
    if not short or len(short) >= len(main):
        return None
    kept = {k: v for k, v in words.items() if k not in ("headline_short", "headline_long")}
    return replace(material, words={**kept, "headline": short})


def improve(
    material: Material,
    aspects: Sequence[Aspect],
    root: Path,
    *,
    seed: int,
    ranked: Sequence[Candidate],
    bar: Bar | None = None,
    budget_s: float = BUDGET_S,
) -> Outcome:
    """The ranking with the bar applied, repaired if nothing clears it (above)."""
    bar = bar or active_bar()
    if bar is None:
        return Outcome(list(ranked))
    started = time.perf_counter()
    first = judged(ranked, material, root, bar)
    tried = [_summary("as drafted", first, bar)]
    if tried[0].cleared:
        return Outcome(first, tuple(tried))

    attempts: list[tuple[list[Candidate], Tried]] = [(first, tried[0])]

    def again(
        used: Material, *, shift: int = 0, limit: int | None = None, penalty: float | None = None
    ) -> Callable[[], list[Candidate] | None]:
        def run() -> list[Candidate] | None:
            try:
                found = chooser.candidates(
                    used, aspects, root, seed=seed + shift, limit=limit, photo_penalty=penalty
                )
            except NothingFits:
                return None
            return judged(found, used, root, bar)

        return run

    steps: list[tuple[str, Callable[[], list[Candidate] | None]]] = []
    if len(material.photos) >= 2:
        swapped = replace(material, photos=tuple(reversed(material.photos)))
        steps.append(("the other photo first", again(swapped)))
    shortest = _shortest(material)
    if shortest is not None:
        steps.append(("the shortest headline", again(shortest)))
    if material.photos:
        steps.append(("words-only designs on equal terms", again(material, penalty=0.0)))
    steps.append(("a wider search", again(material, shift=1, limit=WIDER * chooser.MAX_CANDIDATES)))

    skipped: list[str] = []
    for name, run in steps:
        if time.perf_counter() - started > budget_s:
            skipped.append(name)
            continue
        found = run()
        if not found:
            continue
        summary = _summary(name, found, bar)
        tried.append(summary)
        attempts.append((found, summary))
        if summary.cleared:
            return Outcome(found, tuple(tried), True, tuple(skipped))
    closest = min(attempts, key=lambda pair: np.nan_to_num(pair[1].best, nan=2.0))
    return Outcome(closest[0], tuple(tried), False, tuple(skipped))


__all__ = ["BUDGET_S", "TOP", "WIDER", "Outcome", "Tried", "improve", "judged"]
