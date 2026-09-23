from __future__ import annotations

import pytest
from stratum_spec import SCHEMA_VERSION, PosterSpec, UnknownSchemaVersion, load, upgrade


def test_current_version_passes_through(spec: PosterSpec) -> None:
    data = spec.model_dump()
    assert upgrade(data)["schema_version"] == SCHEMA_VERSION
    assert load(data) == spec


def test_future_version_is_refused(spec: PosterSpec) -> None:
    data = spec.model_dump()
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(UnknownSchemaVersion):
        upgrade(data)
