"""M15c: which critic the product scores with.

What a reviewer should be able to repeat back:
- without a switch file the product uses critic v1, as before;
- the switch names a model in the same folder, and the walkthrough writes it only
  for a model that is there; a switch naming anything else is a broken release and
  fails loudly instead of quietly scoring with something else;
- whatever is shipped, the switch names a model that loads with today's features.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from stratum_worker.critic import ranker
from stratum_worker.critic.features import FEATURES
from stratum_worker.critic.ranker import (
    ACTIVE,
    DEFAULT_MODEL,
    Forest,
    active_model,
    write_active,
)


def test_without_a_switch_the_product_uses_v1(tmp_path: Path) -> None:
    assert active_model(tmp_path / "ACTIVE") == DEFAULT_MODEL


def test_the_switch_names_a_model_next_to_it(tmp_path: Path) -> None:
    shutil.copy(DEFAULT_MODEL, tmp_path / "critic-v2.json")
    switch = write_active("critic-v2.json", tmp_path / "ACTIVE")
    assert switch.read_text() == "critic-v2.json\n"
    assert active_model(switch) == tmp_path / "critic-v2.json"


@pytest.mark.parametrize("name", ["critic-v9.json", "../critic-v1.json", ""])
def test_a_switch_naming_anything_else_fails_loudly(tmp_path: Path, name: str) -> None:
    switch = tmp_path / "ACTIVE"
    switch.write_text(name + "\n")
    with pytest.raises(ValueError, match="not a model"):
        active_model(switch)
    with pytest.raises(ValueError, match="not a model"):
        write_active(name or "missing.json", switch)


def test_scoring_uses_the_model_the_switch_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = np.random.default_rng(0).normal(size=(5, len(FEATURES)))
    v1 = ranker.score(x, DEFAULT_MODEL)
    shutil.copy(DEFAULT_MODEL, tmp_path / "critic-v2.json")
    write_active("critic-v2.json", tmp_path / "ACTIVE")
    monkeypatch.setattr(ranker, "ACTIVE", tmp_path / "ACTIVE")
    ranker._active.cache_clear()
    try:
        assert ranker._active() == tmp_path / "critic-v2.json"
        assert ranker.model_name() == "critic-v2"
        np.testing.assert_allclose(ranker.score(x), v1)  # same trees, read from v2's path
    finally:
        ranker._active.cache_clear()


def test_whatever_is_shipped_loads_with_todays_features_and_proves_itself() -> None:
    path = active_model()
    assert path == DEFAULT_MODEL or ACTIVE.is_file()
    forest = Forest.load(path)
    assert forest.features == FEATURES and forest.trees
    proof = json.loads(path.read_text())["meta"]["proof"]
    np.testing.assert_allclose(forest.predict(np.asarray(proof["x"])), proof["lightgbm"], atol=1e-9)
