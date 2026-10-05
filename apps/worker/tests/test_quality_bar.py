"""M15c: the quality bar - the chance a designer would not send a design.

What a reviewer should be able to repeat back:
- the fit finds the measurement that matters, and its probabilities line up with
  what happens (they are honest enough to set a threshold on);
- the threshold follows the stated cost: the dearer a bad design let through, the
  more designs are held for a second look;
- the bar is a small JSON file read with numpy, refused if fitted on other
  measurements, and with no file there is no bar.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from stratum_worker.critic import bar as quality
from stratum_worker.critic.bar import Bar, auc, brier, calibration, fit, threshold_for
from stratum_worker.critic.features import FEATURES, INDEX

N = len(FEATURES)


def made_up(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Designs whose chance of being refused is known: more overlap, more refusals."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, N))
    z = 2.0 * x[:, INDEX["text_overlap"]] - 1.0 * x[:, INDEX["contrast_margin"]] - 0.5
    bad = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(np.int64)
    return x, bad


def test_the_fit_finds_what_matters_and_ranks_unseen_designs() -> None:
    x, bad = made_up(3000)
    bar = fit(x[:2000], bad[:2000])
    names = [name for name, _ in bar.weights()[:2]]
    assert names == ["text_overlap", "contrast_margin"]
    assert dict(bar.weights())["text_overlap"] > 0 > dict(bar.weights())["contrast_margin"]
    p = bar.p_bad(x[2000:])
    assert auc(p, bad[2000:]) > 0.8
    assert brier(p, bad[2000:]) < brier(np.full(1000, bad[:2000].mean()), bad[2000:])


def test_its_probabilities_line_up_with_what_happens() -> None:
    x, bad = made_up(6000, seed=1)
    bar = fit(x[:3000], bad[:3000])
    for one in calibration(bar.p_bad(x[3000:]), bad[3000:], bins=5):
        assert abs(one["predicted"] - one["seen"]) < 0.08, one


def test_designers_answers_can_weigh_more() -> None:
    x, bad = made_up(400, seed=2)
    light = fit(x, bad, np.ones(400))
    heavy = fit(x, bad, np.where(np.arange(400) < 200, 3.0, 1.0))
    assert not np.allclose(light.coef, heavy.coef)


def test_the_threshold_follows_the_cost_of_letting_a_bad_design_through() -> None:
    x, bad = made_up(2000, seed=3)
    p = fit(x, bad).p_bad(x)
    cheap, dear = threshold_for(p, bad, miss_cost=1.0), threshold_for(p, bad, miss_cost=5.0)
    assert dear < cheap
    assert (p > dear).sum() > (p > cheap).sum()  # more held for a second look


def test_one_side_only_cannot_be_learned() -> None:
    x, _ = made_up(50)
    with pytest.raises(ValueError, match="both sides"):
        fit(x, np.zeros(50, dtype=np.int64))


def test_auc_and_brier_on_known_cases() -> None:
    bad = np.array([0, 0, 1, 1])
    assert auc(np.array([0.1, 0.2, 0.8, 0.9]), bad) == 1.0
    assert auc(np.array([0.9, 0.8, 0.2, 0.1]), bad) == 0.0
    assert auc(np.full(4, 0.5), bad) == 0.5
    assert brier(bad.astype(float), bad) == 0.0


def test_a_missing_measurement_counts_as_the_average() -> None:
    x, bad = made_up(500, seed=4)
    bar = fit(x, bad)
    unknown = np.full((1, N), np.nan)
    assert bar.p_bad(unknown)[0] == pytest.approx(bar.p_bad(bar.mean[None, :])[0])


def test_the_bar_is_a_file_read_with_numpy(tmp_path: Path) -> None:
    x, bad = made_up(500, seed=5)
    bar = fit(x, bad).with_threshold(0.4)
    path = tmp_path / "bar.json"
    quality.save(Bar(bar.mean, bar.scale, bar.coef, bar.bias, 0.4, meta={"answers": 9}), path)
    loaded = quality.active_bar(path)
    assert loaded is not None and loaded.threshold == 0.4 and loaded.meta == {"answers": 9}
    np.testing.assert_allclose(loaded.p_bad(x[:20]), bar.p_bad(x[:20]))
    assert loaded.below(x[:20]).tolist() == (bar.p_bad(x[:20]) > 0.4).tolist()


def test_a_bar_fitted_on_other_measurements_is_refused() -> None:
    data = fit(*made_up(200)).to_json()
    data["meta"]["features"] = ["something", "else"]
    with pytest.raises(ValueError, match="different measurements"):
        Bar.from_json(data)


def test_no_file_means_no_bar(tmp_path: Path) -> None:
    assert quality.active_bar(tmp_path / "nothing.json") is None


def test_a_shipped_bar_loads_and_its_threshold_is_a_probability() -> None:
    shipped = quality.active_bar()
    if shipped is None:
        pytest.skip("no bar shipped yet: nothing is held back")
    assert 0.0 < shipped.threshold < 1.0
    assert shipped.p_bad(np.zeros((1, N))).shape == (1,)
