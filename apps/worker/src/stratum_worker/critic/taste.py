"""Critic v2: learning taste from Taste Lab answers (taste plan step 2).

Critic v1 learned "an original beats a damaged copy of itself" from synthetic data. It
never saw two good designs compared, so among valid designs it mostly scores noise.
The Taste Lab (M15a, M15b) asks a person which of four designs for a real post they
would send the client. Each answer is a small ranking. Since M15c a rater can also
tick every design they would send as it is (issue I-31), so there are two kinds:

| | picked (best) | would send | unmarked | never send |
|---|---|---|---|---|
| with "would send" ticks | 3 | 2 | 1 | 0 |
| an older answer | 2 | - | 1 | 0 |

"None of these" with no never-sends says nothing about order, so it is skipped for
ranking; it still says every design was below the bar (see `acceptable`). Repeat
screens (served again to measure self-consistency) are kept out of training and used
only to measure how consistent the rater is: the ceiling no model can beat.

Raters say what they are (designer, design student, or other). Designers' answers are
the answer key: they weigh more in training and are scored on their own.

Everything here is plain numpy: reading the export, labels, folds by post, the
measures. Training itself happens in the walkthrough notebook with LightGBM, which
the server never needs (ranker.py evaluates the saved trees).

Pure: no database, no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from stratum_worker.critic.features import FEATURES

Scores = Callable[[npt.NDArray[np.float64]], npt.NDArray[np.float64]]

ROLES = ("designer", "design_student", "other")
# How much one answer counts in training, by what the rater is. Judgement, stated:
# a working designer's choice is worth three of someone who does not design.
WEIGHT = {"designer": 3.0, "design_student": 2.0, "other": 1.0, "": 1.0}
EXPERT_ROLES = frozenset({"designer", "design_student"})

OK, BAD, UNKNOWN = 1, 0, -1


@dataclass(frozen=True)
class Answer:
    """One person's answer about one screen."""

    screen: str
    post: str
    rater: int
    picked: int | None  # index into the screen's designs; None: "none of these"
    never: tuple[int, ...]
    features: npt.NDArray[np.float64]  # designs x FEATURES
    product: npt.NDArray[np.float64]  # the ranking's own score for each design
    families: tuple[str, ...]
    repeat: bool = False
    shared: bool = False
    ms: int = 0
    send: tuple[int, ...] | None = None  # "would send" ticks; None: asked before M15c
    role: str = ""

    @property
    def labels(self) -> npt.NDArray[np.int64] | None:
        """The ranking this answer gives (table above); None when it orders nothing."""
        out = np.ones(len(self.features), dtype=np.int64)
        for index in self.send or ():
            out[index] = 2
        for index in self.never:
            out[index] = 0
        if self.picked is not None:
            out[self.picked] = 3 if self.send is not None else 2
        return out if len(set(out.tolist())) > 1 else None

    @property
    def acceptable(self) -> npt.NDArray[np.int64]:
        """Each design: OK (would be sent), BAD (would not) or UNKNOWN.

        - with "would send" ticks: the pick and the ticks are OK, the rest BAD;
        - "none of these": every design is BAD;
        - an older answer: the pick is OK, a never-send BAD, and the rest UNKNOWN
          (not chosen is not the same as not good enough).
        """
        n = len(self.features)
        if self.send is not None or self.picked is None:
            out = np.full(n, BAD, dtype=np.int64)
            for index in (*(self.send or ()), *((self.picked,) if self.picked is not None else ())):
                out[index] = OK
            return out
        out = np.full(n, UNKNOWN, dtype=np.int64)
        for index in self.never:
            out[index] = BAD
        out[self.picked] = OK
        return out

    @property
    def expert(self) -> bool:
        return self.role in EXPERT_ROLES

    @property
    def weight(self) -> float:
        return WEIGHT.get(self.role, 1.0)


def read_export(lines: Iterable[str]) -> list[Answer]:
    """The Taste Lab export (one JSON object per line), as answers. Lines without
    measurements (an export from before 6 Oct) are skipped."""
    out = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        features = row.get("features")
        if not features:
            continue
        matrix = np.asarray(features, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != len(FEATURES):
            raise ValueError(
                f"screen {row.get('screen')}: {matrix.shape[1] if matrix.ndim == 2 else '?'} "
                f"measurements per design, expected {len(FEATURES)}"
            )
        designs = row.get("designs", [])
        out.append(
            Answer(
                screen=str(row["screen"]),
                post=str(row["post"]),
                rater=int(row.get("rater", 1)),
                picked=row.get("picked"),
                never=tuple(row.get("never", [])),
                features=matrix,
                product=np.asarray([float(one.get("score", 0.0)) for one in designs]),
                families=tuple(str(one.get("family", "")) for one in designs),
                repeat=bool(row.get("repeat")),
                shared=bool(row.get("shared")),
                ms=int(row.get("ms") or 0),
                send=tuple(row["send"]) if row.get("send") is not None else None,
                role=str(row.get("role") or ""),
            )
        )
    return _roles_filled(out)


def _roles_filled(answers: list[Answer]) -> list[Answer]:
    """A rater's role is what they last said it was: an answer given before they said
    takes the same role."""
    said: dict[int, str] = {}
    for one in answers:
        if one.role:
            said[one.rater] = one.role
    return [replace(one, role=said.get(one.rater, one.role)) for one in answers]


def experts(answers: Sequence[Answer], raters: Iterable[int] = ()) -> list[Answer]:
    """The answer key: answers from designers and design students, plus any rater
    numbers named (a designer who rated before roles were asked)."""
    named = set(raters)
    return [one for one in answers if one.expert or one.rater in named]


def training(answers: Sequence[Answer]) -> list[Answer]:
    """The answers a model learns from: first answers that order something."""
    return [one for one in answers if not one.repeat and one.labels is not None]


def consistency(answers: Sequence[Answer]) -> tuple[int, float | None]:
    """How often a rater picked the same design when a screen came back: the number
    of repeats, and the share that matched the first answer."""
    first: dict[tuple[int, str], int | None] = {}
    same = total = 0
    for one in answers:
        key = (one.rater, one.screen)
        if one.repeat and key in first:
            total += 1
            same += int(first[key] == one.picked)
        elif key not in first:
            first[key] = one.picked
    return total, (same / total if total else None)


def agreement(answers: Sequence[Answer]) -> tuple[int, float | None]:
    """How often two raters picked the same design for the same screen (first answers
    only; "none of these" counts as a pick): the number of rater pairs, and the share
    that agreed. On its own, a person's taste; across designers, how much taste is
    shared - the other ceiling."""
    picks: dict[str, dict[int, int | None]] = {}
    for one in answers:
        if not one.repeat:
            picks.setdefault(one.screen, {}).setdefault(one.rater, one.picked)
    pairs = same = 0
    for by_rater in picks.values():
        chosen = list(by_rater.values())
        for i in range(len(chosen)):
            for j in range(i + 1, len(chosen)):
                pairs += 1
                same += int(chosen[i] == chosen[j])
    return pairs, (same / pairs if pairs else None)


def folds(
    answers: Sequence[Answer], k: int = 5, seed: int = 0
) -> list[tuple[list[int], list[int]]]:
    """k train/test splits by post: every screen of a post is on the same side, so a
    model is always tested on posts it never saw."""
    posts = sorted({one.post for one in answers})
    rng = np.random.default_rng(seed)
    rng.shuffle(posts)
    k = max(2, min(k, len(posts)))
    fold_of = {post: index % k for index, post in enumerate(posts)}
    out = []
    for fold in range(k):
        test = [i for i, one in enumerate(answers) if fold_of[one.post] == fold]
        train = [i for i, one in enumerate(answers) if fold_of[one.post] != fold]
        out.append((train, test))
    return out


def stack(
    answers: Sequence[Answer],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int64], list[int]]:
    """Rows, labels and group sizes for LightGBM's lambdarank."""
    labelled = [(one, one.labels) for one in answers]
    kept = [(one, labels) for one, labels in labelled if labels is not None]
    x = np.vstack([one.features for one, _ in kept])
    y = np.concatenate([labels for _, labels in kept])
    return x, y, [len(labels) for _, labels in kept]


def row_weights(answers: Sequence[Answer]) -> npt.NDArray[np.float64]:
    """One weight per row of `stack(answers)`: every design in an answer carries the
    answer's weight, so a designer's ranking counts for more."""
    kept = [one for one in answers if one.labels is not None]
    return np.concatenate([np.full(len(one.features), one.weight) for one in kept])


def bar_rows(
    answers: Sequence[Answer],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.int64], npt.NDArray[np.float64], list[int]]:
    """Rows for the quality bar: every design whose fate is known. Returns the
    measurements, 1 for below the bar (would not be sent), the weights, and which
    answer each row came from (to keep a post's rows on one side of a fold)."""
    xs, bad, weight, source = [], [], [], []
    for index, one in enumerate(answers):
        if one.repeat:
            continue
        for design, fate in enumerate(one.acceptable.tolist()):
            if fate == UNKNOWN:
                continue
            xs.append(one.features[design])
            bad.append(int(fate == BAD))
            weight.append(one.weight)
            source.append(index)
    width = len(FEATURES)
    return (
        np.asarray(xs, dtype=np.float64).reshape(-1, width),
        np.asarray(bad, dtype=np.int64),
        np.asarray(weight, dtype=np.float64),
        source,
    )


def marks(scores: Sequence[npt.NDArray[np.float64]], answers: Sequence[Answer]) -> dict[str, float]:
    """How well per-screen scores agree with the answers (higher is better):

    - top1: the design scored best is the one picked (chance is 1 in the screen size);
    - pairs: of the pairs the answer orders (picked over the rest, the rest over a
      never-send), the share in the right order;
    - never_last: a never-send design scored below the picked one;
    - top_ok: the design scored best is one the rater would send, over answers that
      say which are good enough (with "would send" ticks); top_ok_n counts them.
    """
    top1 = top1_n = pairs = pairs_n = never = never_n = ok = ok_n = 0
    for score, one in zip(scores, answers, strict=True):
        if one.send is not None:
            ok_n += 1
            ok += int(one.acceptable[int(np.argmax(score))] == OK)
        labels = one.labels
        if labels is None:
            continue
        if one.picked is not None:
            top1_n += 1
            top1 += int(int(np.argmax(score)) == one.picked)
            for index in one.never:
                never_n += 1
                never += int(score[index] < score[one.picked])
        for i in range(len(labels)):
            for j in range(len(labels)):
                if labels[i] > labels[j]:
                    pairs_n += 1
                    pairs += int(score[i] > score[j])
    return {
        "top1": top1 / top1_n if top1_n else float("nan"),
        "pairs": pairs / pairs_n if pairs_n else float("nan"),
        "never_last": never / never_n if never_n else float("nan"),
        "top_ok": ok / ok_n if ok_n else float("nan"),
        "top_ok_n": float(ok_n),
        "answers": float(len(answers)),
    }


COUNTS = frozenset({"answers", "top_ok_n"})


def score_all(score_fn: Scores, answers: Sequence[Answer]) -> list[npt.NDArray[np.float64]]:
    return [np.asarray(score_fn(one.features), dtype=np.float64) for one in answers]


def with_range(
    scores: Sequence[npt.NDArray[np.float64]],
    answers: Sequence[Answer],
    repeats: int = 1000,
    seed: int = 0,
) -> dict[str, tuple[float, float, float]]:
    """Each mark with a 95% bootstrap interval over answers: (low, value, high)."""
    point = marks(scores, answers)
    rng = np.random.default_rng(seed)
    draws: dict[str, list[float]] = {key: [] for key in point if key not in COUNTS}
    n = len(answers)
    for _ in range(repeats):
        pick = rng.integers(0, n, n)
        one = marks([scores[i] for i in pick], [answers[i] for i in pick])
        for key in draws:
            draws[key].append(one[key])
    out: dict[str, tuple[float, float, float]] = {}
    for key, values in draws.items():
        finite = np.asarray(values)[np.isfinite(values)]
        if not len(finite):  # nothing to measure it on (top_ok with no ticks yet)
            out[key] = (float("nan"), point[key], float("nan"))
            continue
        low, high = np.percentile(finite, [2.5, 97.5])
        out[key] = (float(low), point[key], float(high))
    return out


def paired(
    better: Sequence[npt.NDArray[np.float64]],
    than: Sequence[npt.NDArray[np.float64]],
    answers: Sequence[Answer],
    key: str = "pairs",
    repeats: int = 1000,
    seed: int = 0,
) -> dict[str, float]:
    """Is `better` really better than `than` on these answers? Both are scored on the
    same resampled answers each time (a paired bootstrap), so luck in which answers
    were drawn affects both alike. Returns the difference in `key` with a 95%
    interval, and the share of resamples in which `better` won."""
    point = marks(better, answers)[key] - marks(than, answers)[key]
    rng = np.random.default_rng(seed)
    n = len(answers)
    diffs = []
    for _ in range(repeats):
        pick = rng.integers(0, n, n)
        drawn = [answers[i] for i in pick]
        ours = marks([better[i] for i in pick], drawn)[key]
        theirs = marks([than[i] for i in pick], drawn)[key]
        diffs.append(ours - theirs)
    values = np.asarray(diffs)
    values = values[np.isfinite(values)]
    return {
        "low": float(np.percentile(values, 2.5)) if len(values) else float("nan"),
        "difference": float(point),
        "high": float(np.percentile(values, 97.5)) if len(values) else float("nan"),
        "wins": float(np.mean(values > 0)) if len(values) else float("nan"),
    }


def chance(answers: Sequence[Answer]) -> float:
    """top1 for a coin: the mean of 1 / designs on screen, over answers with a pick."""
    sizes = [len(one.features) for one in answers if one.picked is not None]
    return float(np.mean([1 / size for size in sizes])) if sizes else float("nan")


# --- a rater whose taste is known, for tests and a dry run -------------------------------


def simulate(
    screens: int,
    taste: npt.NDArray[np.float64],
    *,
    noise: float = 0.5,
    size: int = 4,
    posts: int = 20,
    repeat_rate: float = 0.1,
    seed: int = 0,
    bar: float | None = None,
    rater: int = 1,
    role: str = "",
    prefix: str = "",
) -> list[Answer]:
    """Answers from a made-up rater who scores each design as `features @ taste` plus
    noise, picks the best, and marks the worst "never send" when it is clearly bad.
    With a `bar`, they also tick every design whose value clears it ("would send"),
    and say "none of these" when none does. Some screens come back as repeats. Used
    to check the training recovers a taste, and as the notebook's dry run."""
    rng = np.random.default_rng(seed)
    out: list[Answer] = []
    for index in range(screens):
        features = rng.normal(size=(size, len(FEATURES)))
        value = features @ taste + rng.normal(scale=noise, size=size)
        picked: int | None = int(np.argmax(value))
        worst = int(np.argmin(value))
        never = (worst,) if value[worst] < value.mean() - 1.0 else ()
        send: tuple[int, ...] | None = None
        if bar is not None:
            send = tuple(int(i) for i in np.flatnonzero(value > bar) if i not in never)
            picked = picked if picked in send else None
        answer = Answer(
            screen=f"{prefix}s{index}",
            post=f"{prefix}p{index % posts}",
            rater=rater,
            picked=picked,
            never=never,
            features=features,
            product=rng.normal(size=size),
            families=tuple("panel" for _ in range(size)),
            send=send,
            role=role,
        )
        out.append(answer)
        if rng.random() < repeat_rate:
            again = value - rng.normal(scale=noise, size=size) + rng.normal(scale=noise, size=size)
            out.append(
                Answer(
                    screen=answer.screen,
                    post=answer.post,
                    rater=rater,
                    picked=int(np.argmax(again)),
                    never=(),
                    features=features,
                    product=answer.product,
                    families=answer.families,
                    repeat=True,
                    role=role,
                )
            )
    return out


__all__ = [
    "BAD",
    "EXPERT_ROLES",
    "OK",
    "ROLES",
    "UNKNOWN",
    "WEIGHT",
    "Answer",
    "agreement",
    "bar_rows",
    "chance",
    "consistency",
    "experts",
    "folds",
    "marks",
    "paired",
    "read_export",
    "row_weights",
    "score_all",
    "simulate",
    "stack",
    "training",
    "with_range",
]
