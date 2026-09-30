"""The critic lab (M12a): measurements, damage, the data, and the model file format.

The model itself is trained by a person, in ml/critic/walkthrough.ipynb. What this
file promises:
- each measurement moves the right way when a design is damaged in that way;
- the numpy evaluator reproduces LightGBM exactly, and save_model refuses to write a
  file it cannot reproduce;
- a model file, once one is shipped, still reproduces the scores it carries;
- the splits never share a brand kit or a piece of copy;
- with no model file, scoring falls back to the hand-written rule instead of failing.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest
from stratum_spec import retarget
from stratum_worker.critic import damage
from stratum_worker.critic.data import COPIES, SPLITS, Split, groups, kits
from stratum_worker.critic.features import FEATURES, INDEX, edge_map, measure
from stratum_worker.critic.ranker import DEFAULT_MODEL, Forest, heuristic, save_model, score
from stratum_worker.design import Copy, lay_out
from stratum_worker.design.preview import SAMPLE_COPY, bind, scene

REPO = Path(__file__).resolve().parents[3]
KIT = kits(REPO)["kowalski-plumbing"]


def spec_for(design: str, aspect: str = "4:5"):  # type: ignore[no-untyped-def]
    laid = lay_out(design, KIT, REPO, copy=SAMPLE_COPY)
    assert laid is not None
    return retarget(bind(laid, KIT, SAMPLE_COPY), aspect)


def f(spec, name: str) -> float:  # type: ignore[no-untyped-def]
    return float(measure(spec, KIT, REPO)[INDEX[name]])


# --- measurements respond to the damage they should -------------------------------------


@pytest.mark.parametrize(
    ("operator", "feature", "direction"),
    [
        (damage.collide, "text_overlap", +1),
        (damage.tiny_type, "offer_size", -1),
        (damage.low_contrast, "contrast_min", -1),
        (damage.logo_clash, "logo_text_overlap", +1),
        (damage.crowd_edge, "margin_min", -1),
        (damage.shout, "hierarchy", +1),
        (damage.misalign, "align_misfit", +1),
    ],
)
def test_a_damage_moves_its_measurement(operator, feature: str, direction: int) -> None:  # type: ignore[no-untyped-def]
    good = spec_for("ds.panel.1.0000000")
    broken = operator(good, 3, random.Random(1), KIT)
    assert broken is not None
    assert (f(broken, feature) - f(good, feature)) * direction > 0


def test_dropping_the_scrim_puts_the_words_on_the_photo() -> None:
    good = spec_for("ds.overlay.1.000000")  # bottom, scrim
    broken = damage.strip_backing(good, 3, random.Random(1), KIT)
    assert broken is not None
    assert f(good, "text_on_photo") == 0.0
    assert f(broken, "text_on_photo") == 1.0


def test_busyness_is_measured_from_the_photo_under_the_words() -> None:
    good = spec_for("ds.overlay.1.000000")
    broken = damage.strip_backing(good, 3, random.Random(1), KIT)
    assert broken is not None
    busy = {"a" * 64: edge_map(np.asarray(scene(3)))}
    flat = {"a" * 64: np.zeros((96, 96))}
    assert measure(broken, KIT, REPO, edges=busy)[INDEX["busyness_under_text"]] > 0.01
    assert measure(broken, KIT, REPO, edges=flat)[INDEX["busyness_under_text"]] == 0.0
    # Behind a solid scrim the photo does not count.
    assert measure(good, KIT, REPO, edges=busy)[INDEX["busyness_under_text"]] < 0.2


def test_a_lonely_last_headline_line_is_an_orphan() -> None:
    def orphan(headline: str) -> float:
        copy = Copy(headline, SAMPLE_COPY.offer, SAMPLE_COPY.cta)
        laid = lay_out("ds.panel.1.0000000", KIT, REPO, copy=copy)
        assert laid is not None
        return f(retarget(bind(laid, KIT, copy), "16:9"), "orphan")

    # MEDITATION / CAMP FOR / SELF-DISCOVERY ends on its longest line;
    # WINTER-PROOF / YOUR ROOF IN / A DAY ends on two short words.
    assert orphan("Meditation camp for self-discovery") == 1.0
    assert orphan("Winter-proof your roof in a day") < 0.5


def test_measurements_come_in_the_published_order() -> None:
    row = measure(spec_for("ds.type.1.000000"), KIT, REPO)
    assert row.shape == (len(FEATURES),) and np.isfinite(row).all()


# --- the model file -----------------------------------------------------------------------


def tiny_booster(x: np.ndarray, y: np.ndarray, groups_of: int = 8):  # type: ignore[no-untyped-def]
    lgb = pytest.importorskip("lightgbm")
    return lgb.train(
        {"objective": "lambdarank", "verbosity": -1, "num_leaves": 7, "seed": 0},
        lgb.Dataset(x, y, group=[groups_of] * (len(x) // groups_of), feature_name=list(FEATURES)),
        num_boost_round=30,
    )


def test_the_numpy_evaluator_matches_lightgbm_on_zeros_and_missing_values(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, len(FEATURES)))
    x[rng.random(x.shape) < 0.15] = 0.0
    y = (x[:, 0] + x[:, 3] > 0).astype(int) + (x[:, 5] > 1).astype(int)
    booster = tiny_booster(x, y)
    forest = save_model(booster, 30, tmp_path / "m.json", proof_x=x[:10])
    probe = rng.normal(size=(200, len(FEATURES)))
    probe[rng.random(probe.shape) < 0.2] = 0.0
    probe[rng.random(probe.shape) < 0.05] = np.nan
    np.testing.assert_allclose(forest.predict(probe), booster.predict(probe), atol=1e-9)


def test_a_saved_model_loads_and_carries_its_own_proof(tmp_path: Path) -> None:
    tiny = Split("tiny", ("bloom-and-blade",), (0,))
    data = list(groups(REPO, tiny, designs_per_copy=3, templates=False, seed=1))
    x = np.vstack([g.features for g in data])
    y = np.concatenate([g.labels for g in data])
    lgb = pytest.importorskip("lightgbm")
    booster = lgb.train(
        {"objective": "lambdarank", "verbosity": -1, "num_leaves": 7, "seed": 0},
        lgb.Dataset(x, y, group=[len(g.labels) for g in data], feature_name=list(FEATURES)),
        num_boost_round=20,
    )
    path = tmp_path / "critic.json"
    save_model(booster, 20, path, proof_x=x[:8], meta={"trained_by": "a test"})
    loaded = Forest.load(path)
    stored = json.loads(path.read_text())["meta"]
    assert stored["trained_by"] == "a test" and stored["trees"] == len(loaded.trees) >= 1
    np.testing.assert_allclose(loaded.predict(x[:8]), stored["proof"]["lightgbm"], atol=1e-9)


@pytest.mark.skipif(not DEFAULT_MODEL.is_file(), reason="no model trained yet")
def test_the_shipped_model_still_reproduces_its_proof() -> None:
    data = json.loads(DEFAULT_MODEL.read_text())
    proof = data["meta"]["proof"]
    np.testing.assert_allclose(
        Forest.load(DEFAULT_MODEL).predict(np.asarray(proof["x"])), proof["lightgbm"], atol=1e-9
    )


def test_the_splits_never_share_a_brand_or_a_piece_of_copy() -> None:
    for one, two in [(0, 1), (0, 2), (1, 2)]:
        assert not set(SPLITS[one].kits) & set(SPLITS[two].kits)
        assert not set(SPLITS[one].copies) & set(SPLITS[two].copies)
    assert set().union(*(s.copies for s in SPLITS)) <= set(range(len(COPIES)))


def test_without_a_model_file_scoring_falls_back_to_the_rule(tmp_path: Path) -> None:
    row = measure(spec_for("ds.type.1.000000"), KIT, REPO)[None, :]
    np.testing.assert_allclose(score(row, tmp_path / "missing.json"), heuristic(row))
