"""The photo-understanding lab's shared code: paths, the test cases, scoring.

No model in here: the notebook stages bring their own. Every stage imports this, so
the test is read and scored the same way every time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
# Where the photos and the model live. The environment variables are only there so
# the same code runs somewhere else; on your Mac the defaults are right.
PHOTOS = Path(os.environ.get("STRATUM_PHOTOS", Path.home() / "dev" / "stratum-photos"))
MODEL_DIR = Path(
    os.environ.get("SIGLIP_DIR", Path.home() / "dev" / "models" / "siglip-base-patch16-224")
)
TYPES = Path(os.environ.get("STRATUM_TYPES", Path.home() / "dev" / "stratum-photo-types"))
CASES = HERE / "cases.jsonl"
CACHE = HERE / "cache"  # embeddings saved between runs (git-ignored)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class Case:
    """One thing a designer might type, and the photo(s) that would be right."""

    client: str
    query: str
    want: tuple[str, ...]  # empty: nothing fits, the right answer is to ask


def load_cases(path: Path = CASES) -> list[Case]:
    cases = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(Case(row["client"], row["query"], tuple(row["want"])))
    return cases


def photos_of(client: str) -> list[Path]:
    """A client's photos, in name order. The logo is not a photo."""
    folder = PHOTOS / client
    return sorted(
        p
        for p in folder.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES and not p.stem.lower().startswith("logo")
    )


def check_cases(cases: Sequence[Case]) -> None:
    """Fail loudly on a typo: every client folder and every wanted photo must exist."""
    for case in cases:
        names = {p.name for p in photos_of(case.client)}
        missing = [w for w in case.want if w not in names]
        if missing:
            raise ValueError(f"{case.client} / {case.query!r}: no photo named {missing}")


# --- scoring -----------------------------------------------------------------------------

# A searcher answers one case: the client's photo names and one score per photo
# (higher = better match), plus a confidence in the best match between 0 and 1.
Searcher = Callable[[Case], tuple[list[str], np.ndarray, float]]


def rank_of_first_right(names: Sequence[str], scores: np.ndarray, want: Sequence[str]) -> int:
    """1 if a right photo is ranked first, 2 if second, and so on."""
    order = np.argsort(-scores, kind="stable")
    for rank, index in enumerate(order, start=1):
        if names[index] in want:
            return rank
    raise ValueError("no wanted photo among the names")


def evaluate(
    cases: Sequence[Case], search: Searcher, threshold: float | None = None
) -> dict[str, float]:
    """How good a searcher is on the test.

    Ranking (cases with a right photo): recall@1, recall@3, MRR.
    Acting (with a threshold): the share of ALL cases where Stratum would do the right
    thing - swap in a right photo when one fits, ask when none does.
    """
    ranks, right_actions, asked_when_none, none_cases = [], 0, 0, 0
    for case in cases:
        names, scores, confidence = search(case)
        acts = threshold is None or confidence >= threshold
        if case.want:
            rank = rank_of_first_right(names, scores, case.want)
            ranks.append(rank)
            right_actions += int(acts and rank == 1)
        else:
            none_cases += 1
            asked_when_none += int(not acts)
            right_actions += int(not acts)
    ranks_array = np.array(ranks)
    out = {
        "recall@1": float(np.mean(ranks_array == 1)),
        "recall@3": float(np.mean(ranks_array <= 3)),
        "mrr": float(np.mean(1.0 / ranks_array)),
        "cases": float(len(cases)),
    }
    if threshold is not None:
        out["right_action"] = right_actions / len(cases)
        out["asked_when_nothing_fits"] = asked_when_none / max(none_cases, 1)
    return out


def best_threshold(cases: Sequence[Case], search: Searcher) -> float:
    """The confidence line that gives the most right actions on these cases."""
    confidences = sorted({search(case)[2] for case in cases})
    candidates = [0.0, *confidences, 1.0]
    return max(candidates, key=lambda t: evaluate(cases, search, t)["right_action"])


def leave_one_client_out(cases: Sequence[Case], search: Searcher) -> dict[str, float]:
    """An honest right-action rate: the threshold is chosen on the other clients and
    tested on the one left out, for each client in turn (grouped cross-validation)."""
    clients = sorted({case.client for case in cases})
    right, total, chosen = 0.0, 0, []
    for held in clients:
        train = [c for c in cases if c.client != held]
        test = [c for c in cases if c.client == held]
        threshold = best_threshold(train, search)
        chosen.append(threshold)
        right += evaluate(test, search, threshold)["right_action"] * len(test)
        total += len(test)
    return {"right_action": right / total, "thresholds": chosen}  # type: ignore[dict-item]


# --- baselines ---------------------------------------------------------------------------


def random_searcher(seed: int = 0) -> Searcher:
    """Scores at random: what a coin would do."""
    rng = np.random.default_rng(seed)

    def search(case: Case) -> tuple[list[str], np.ndarray, float]:
        names = [p.name for p in photos_of(case.client)]
        return names, rng.random(len(names)), float(rng.random())

    return search


def first_photo_searcher(case: Case) -> tuple[list[str], np.ndarray, float]:
    """Today's product, as far as a query goes: it ignores the words and takes the
    next photo in its own order. Always confident: it never asks."""
    names = [p.name for p in photos_of(case.client)]
    return names, -np.arange(len(names), dtype=float), 1.0


# --- photo types (stages 6 and 7) ---------------------------------------------------------

# What kind of photo it is, as far as making a poster goes. The order is fixed: the
# exported weights and every report use it.
CLASSES = ("people_posing", "people_working", "place", "object", "vehicle", "graphic")


def type_photos() -> list[tuple[Path, str]]:
    """Every labelled photo and its class. The label is the folder it sits in, so
    relabelling a photo is moving it to another folder."""
    rows = []
    for label in CLASSES:
        folder = TYPES / label
        if folder.exists():
            rows += [
                (p, label) for p in sorted(folder.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES
            ]
    return rows
