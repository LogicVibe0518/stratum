from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import BrandKit, PosterSpec, load_template

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def kit() -> BrandKit:
    data = json.loads((REPO_ROOT / "brandkits" / "sharmas_dhaba.json").read_text(encoding="utf-8"))
    return BrandKit.model_validate(data)


@pytest.fixture
def offer() -> PosterSpec:
    return load_template(REPO_ROOT / "templates" / "offer_4x5.json")
