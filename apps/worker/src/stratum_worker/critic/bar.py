"""The quality bar (M15c): is this design good enough to send at all?

A ranker only orders: its best of four bad designs is still bad. The bar answers a
different question - the chance a designer would NOT send this design as it is - and
is learned from the Taste Lab's "never send", "none of these" and (since M15c) the
designs a rater did not tick as "would send" (critic/taste.py, `acceptable`).

It is a logistic regression on the critic's measurements, on purpose:
- a few hundred answers is small data, and a linear model with a penalty does not
  memorise them;
- its probabilities are usually close to honest without extra calibration, and the
  walkthrough checks that out of fold;
- it is a list of weights anyone can read: which measurement pushes a design under.

The threshold is chosen from out-of-fold predictions with a stated cost: sending a
design a designer would refuse costs MISS_COST times as much as a needless second
look. Like the critic, the server reads it from a JSON file with numpy, and with no
file there is no bar (everything passes): it ships off until trained and checked.

Pure: no database, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from stratum_worker.critic.features import FEATURES

Array = npt.NDArray[np.float64]

MISS_COST = 3.0
L2 = 1.0
DEFAULT_BAR = Path(__file__).resolve().parent / "model" / "bar.json"


def _sigmoid(z: Array) -> Array:
    out: Array = 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))
    return out


@dataclass(frozen=True)
class Bar:
    """A fitted bar: standardise, weigh, squash to a probability, compare."""

    mean: Array
    scale: Array
    coef: Array
    bias: float
    threshold: float = 0.5
    features: tuple[str, ...] = FEATURES
    meta: dict[str, Any] = field(default_factory=dict)

    def p_bad(self, x: Array) -> Array:
        """The chance each design would not be sent, 0..1."""
        z = (np.atleast_2d(np.asarray(x, dtype=np.float64)) - self.mean) / self.scale
        z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)  # unknown: the average
        return _sigmoid(z @ self.coef + self.bias)

    def below(self, x: Array) -> npt.NDArray[np.bool_]:
        """True for each design under the bar."""
        out: npt.NDArray[np.bool_] = self.p_bad(x) > self.threshold
        return out

    def with_threshold(self, threshold: float) -> Bar:
        return Bar(self.mean, self.scale, self.coef, self.bias, threshold, self.features, self.meta)

    def weights(self) -> list[tuple[str, float]]:
        """Each measurement's weight (per standard deviation), biggest push first:
        positive pushes a design under the bar."""
        pairs = zip(self.features, self.coef.tolist(), strict=True)
        return sorted(((name, round(value, 4)) for name, value in pairs), key=lambda p: -abs(p[1]))

    def to_json(self) -> dict[str, Any]:
        return {
            "meta": {**self.meta, "features": list(self.features)},
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "coef": self.coef.tolist(),
            "bias": self.bias,
            "threshold": self.threshold,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Bar:
        features = tuple(data["meta"]["features"])
        if features != FEATURES:
            raise ValueError("the bar was fitted on different measurements; fit it again")
        return cls(
            mean=np.asarray(data["mean"], dtype=np.float64),
            scale=np.asarray(data["scale"], dtype=np.float64),
            coef=np.asarray(data["coef"], dtype=np.float64),
            bias=float(data["bias"]),
            threshold=float(data["threshold"]),
            features=features,
            meta={k: v for k, v in data["meta"].items() if k != "features"},
        )


def fit(
    x: Array,
    bad: npt.NDArray[np.int64],
    weight: Array | None = None,
    *,
    l2: float = L2,
    rounds: int = 50,
) -> Bar:
    """Penalised logistic regression by Newton's method (a few dozen small solves).

    Measurements are standardised first, so the penalty treats them alike and the
    weights are comparable. The intercept is not penalised."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(bad, dtype=np.float64)
    w = np.ones(len(y)) if weight is None else np.asarray(weight, dtype=np.float64)
    if len(y) == 0 or len(set(y.tolist())) < 2:
        raise ValueError("the bar needs designs on both sides of it to learn from")
    mean = np.nanmean(x, axis=0)
    scale = np.nanstd(x, axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    z = np.nan_to_num((x - mean) / scale, nan=0.0)
    design = np.hstack([z, np.ones((len(z), 1))])
    beta = np.zeros(design.shape[1])
    beta[-1] = float(np.log((w * y).sum() / (w * (1 - y)).sum()))
    penalty = np.full(design.shape[1], l2)
    penalty[-1] = 0.0
    for _ in range(rounds):
        p = _sigmoid(design @ beta)
        gradient = design.T @ (w * (p - y)) + penalty * beta
        hessian = (design * (w * p * (1 - p))[:, None]).T @ design + np.diag(penalty)
        step = np.linalg.solve(hessian + 1e-9 * np.eye(len(beta)), gradient)
        beta -= step
        if float(np.abs(step).max()) < 1e-8:
            break
    return Bar(mean=mean, scale=scale, coef=beta[:-1], bias=float(beta[-1]))


def threshold_for(p: Array, bad: npt.NDArray[np.int64], miss_cost: float = MISS_COST) -> float:
    """The threshold with the lowest cost on these (out-of-fold) predictions: a bad
    design let through costs `miss_cost`, a good one held back costs 1."""
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(bad, dtype=bool)
    candidates = np.unique(np.concatenate([[0.0, 1.0], p]))
    costs = [
        miss_cost * float(np.sum(y & (p <= t))) + float(np.sum(~y & (p > t))) for t in candidates
    ]
    return float(candidates[int(np.argmin(costs))])


def auc(p: Array, bad: npt.NDArray[np.int64]) -> float:
    """The chance a random bad design gets a higher p_bad than a random good one."""
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(bad, dtype=bool)
    positives, negatives = p[y], p[~y]
    if not len(positives) or not len(negatives):
        return float("nan")
    wins = (positives[:, None] > negatives[None, :]).sum()
    ties = (positives[:, None] == negatives[None, :]).sum()
    return float((wins + 0.5 * ties) / (len(positives) * len(negatives)))


def brier(p: Array, bad: npt.NDArray[np.int64]) -> float:
    """Mean squared error of the probabilities (lower is better)."""
    return float(np.mean((np.asarray(p) - np.asarray(bad, dtype=np.float64)) ** 2))


def calibration(p: Array, bad: npt.NDArray[np.int64], bins: int = 5) -> list[dict[str, float]]:
    """Predicted against seen, in bins of equal size: honest probabilities line up."""
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(bad, dtype=np.float64)
    order = np.argsort(p, kind="stable")
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append(
                {
                    "predicted": round(float(p[chunk].mean()), 3),
                    "seen": round(float(y[chunk].mean()), 3),
                    "designs": float(len(chunk)),
                }
            )
    return out


def save(bar: Bar, path: Path = DEFAULT_BAR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bar.to_json(), separators=(",", ":")) + "\n", encoding="utf-8")


@cache
def _load(path: str) -> Bar | None:
    file = Path(path)
    if not file.is_file():
        return None
    return Bar.from_json(json.loads(file.read_text(encoding="utf-8")))


def active_bar(path: Path | None = None) -> Bar | None:
    """The shipped bar, or None when there is none (then nothing is held back)."""
    return _load(str(path or DEFAULT_BAR))


__all__ = [
    "DEFAULT_BAR",
    "MISS_COST",
    "Bar",
    "active_bar",
    "auc",
    "brier",
    "calibration",
    "fit",
    "save",
    "threshold_for",
]
