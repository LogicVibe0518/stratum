"""Scoring designs: the trained critic, and the hand-written rule it has to beat.

The trained model ships as data - LightGBM's own dump of its trees, as JSON - and is
evaluated here with plain numpy. So the server needs no machine-learning library at
all, the model is a file anyone can read, and a test proves this evaluator agrees
with LightGBM's own predictions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

from stratum_worker.critic.features import FEATURES, INDEX

ZERO = 1e-35  # LightGBM's kZeroThreshold


@dataclass(frozen=True)
class Tree:
    feature: np.ndarray  # per node; -1 marks a leaf
    threshold: np.ndarray
    left: np.ndarray
    right: np.ndarray
    default_left: np.ndarray
    missing: np.ndarray  # 0 none, 1 zero, 2 nan
    value: np.ndarray  # leaf values (index = node)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Every row at once: all rows step down the tree together, one level per pass."""
        node = np.zeros(len(x), dtype=np.int64)
        rows = np.arange(len(x))
        while True:
            feature = self.feature[node]
            active = feature >= 0
            if not active.any():
                return self.value[node]
            at, feat = rows[active], feature[active]
            where = node[active]
            v = x[at, feat]
            kind = self.missing[where]
            nan = np.isnan(v)
            v = np.where(nan & (kind != 2), 0.0, v)
            missing = ((kind == 1) & (np.abs(v) <= ZERO)) | ((kind == 2) & nan)
            go_left = np.where(missing, self.default_left[where], v <= self.threshold[where])
            node[active] = np.where(go_left, self.left[where], self.right[where])


def _flatten(structure: dict[str, Any]) -> Tree:
    feature: list[int] = []
    threshold: list[float] = []
    left: list[int] = []
    right: list[int] = []
    default_left: list[bool] = []
    missing: list[int] = []
    value: list[float] = []

    def add(node: dict[str, Any]) -> int:
        index = len(feature)
        feature.append(-1)
        threshold.append(0.0)
        left.append(-1)
        right.append(-1)
        default_left.append(False)
        missing.append(0)
        value.append(0.0)
        if "leaf_value" in node:
            value[index] = float(node["leaf_value"])
            return index
        if node.get("decision_type", "<=") != "<=":
            raise ValueError("only numerical <= splits are supported")
        feature[index] = int(node["split_feature"])
        threshold[index] = float(node["threshold"])
        default_left[index] = bool(node.get("default_left", True))
        missing[index] = {"None": 0, "Zero": 1, "NaN": 2}[node.get("missing_type", "None")]
        left[index] = add(node["left_child"])
        right[index] = add(node["right_child"])
        return index

    add(structure)
    return Tree(
        np.asarray(feature),
        np.asarray(threshold),
        np.asarray(left),
        np.asarray(right),
        np.asarray(default_left),
        np.asarray(missing),
        np.asarray(value),
    )


@dataclass(frozen=True)
class Forest:
    """A LightGBM model, from its JSON dump, evaluated without LightGBM."""

    features: tuple[str, ...]
    trees: tuple[Tree, ...]
    meta: dict[str, Any]

    @classmethod
    def from_dump(cls, dump: dict[str, Any], meta: dict[str, Any] | None = None) -> Forest:
        return cls(
            tuple(dump["feature_names"]),
            tuple(_flatten(tree["tree_structure"]) for tree in dump["tree_info"]),
            meta or {},
        )

    @classmethod
    def load(cls, path: Path) -> Forest:
        data = json.loads(path.read_text(encoding="utf-8"))
        forest = cls.from_dump(data["model"], data.get("meta", {}))
        if forest.features != FEATURES:
            raise ValueError(
                f"{path} was trained on different features; retrain with `make train-critic`"
            )
        return forest

    def predict(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        return np.asarray(sum(tree.predict(x) for tree in self.trees), dtype=np.float64)


def heuristic(x: np.ndarray) -> np.ndarray:
    """The rule a careful person would write by hand. Gate D: the critic must beat it.

    Penalties for the things everyone agrees are wrong - misalignment, text in the
    margin or outside a story's safe band, collisions, low contrast, small print,
    words on a busy photo - and a small reward for a clear headline-over-offer
    hierarchy. The weights are judgement, not fitted.
    """
    x = np.atleast_2d(np.asarray(x, dtype=np.float64))
    f = {name: x[:, i] for name, i in INDEX.items()}
    score = (
        -40.0 * f["align_misfit"]
        - 20.0 * np.clip(0.07 - f["margin_min"], 0, None)
        - 30.0 * f["safe_overflow"]
        - 60.0 * f["text_overlap"]
        - 3.0 * f["logo_text_overlap"]
        - 10.0 * np.clip(0.015 - f["gap_min"], 0, None)
        - 1.5 * np.clip(-f["contrast_margin"], 0, None)
        - 60.0 * np.clip(0.03 - f["offer_size"], 0, None)
        - 2.0 * f["text_on_photo"] * f["busyness_under_text"]
        - 2.0 * f["truncated"]
        - 0.5 * f["at_minimum"]
        + 0.5 * np.clip(f["hierarchy"], 0, 1.2)
        - 1.0 * f["balance_y"]
    )
    return np.asarray(score, dtype=np.float64)


# Shipped inside the package, so it is in the server image and in every install.
# The walkthrough notebook (ml/critic/walkthrough.ipynb) writes it here.
DEFAULT_MODEL = Path(__file__).resolve().parent / "model" / "critic-v1.json"

# What prediction needs from LightGBM's dump. Gains, counts and weights are for
# inspection only, and would more than double the file.
KEEP = frozenset(
    {
        "split_feature",
        "threshold",
        "decision_type",
        "default_left",
        "missing_type",
        "left_child",
        "right_child",
        "leaf_value",
    }
)


def _slim(node: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in node.items() if key in KEEP}
    for child in ("left_child", "right_child"):
        if child in out:
            out[child] = _slim(out[child])
    return out


def save_model(
    booster: Any,
    rounds: int,
    path: Path,
    *,
    proof_x: np.ndarray,
    meta: dict[str, Any] | None = None,
) -> Forest:
    """Write a trained LightGBM booster as the file the app reads, and prove it.

    Keeps the first `rounds` trees. The file carries rows and LightGBM's own scores
    for them; this refuses to write if the numpy evaluator disagrees, and a test
    re-checks the shipped file on every run.
    """
    dump = booster.dump_model(num_iteration=rounds)
    model = {
        "feature_names": dump["feature_names"],
        "tree_info": [{"tree_structure": _slim(t["tree_structure"])} for t in dump["tree_info"]],
    }
    forest = Forest.from_dump(model)
    expected = np.asarray(booster.predict(proof_x, num_iteration=rounds), dtype=np.float64)
    if not np.allclose(forest.predict(proof_x), expected, atol=1e-9):
        raise ValueError("the numpy evaluator disagrees with LightGBM; not saving")
    data = {
        "meta": {
            **(meta or {}),
            "features": list(FEATURES),
            "trees": len(forest.trees),
            "proof": {"x": np.asarray(proof_x).tolist(), "lightgbm": expected.tolist()},
        },
        "model": model,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    return forest


@cache
def _forest(path: str) -> Forest | None:
    file = Path(path)
    return Forest.load(file) if file.is_file() else None


def score(x: np.ndarray, model: Path | None = None) -> np.ndarray:
    """Higher is better. The trained critic when its file is present, the rule if not."""
    forest = _forest(str(model or DEFAULT_MODEL))
    return forest.predict(x) if forest is not None else heuristic(x)


__all__ = ["DEFAULT_MODEL", "Forest", "heuristic", "save_model", "score"]
