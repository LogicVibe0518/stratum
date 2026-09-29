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


def test_a_version_1_poster_upgrades_to_2_unchanged(spec: PosterSpec) -> None:
    # A real stored shape from before schema 2: no plate on the logo.
    data = spec.model_dump(mode="json")
    data["schema_version"] = 1
    for layer in data["layers"]:
        layer.pop("plate", None)
    upgraded = load(data)
    assert upgraded.schema_version == 2
    assert upgraded.model_dump(exclude={"schema_version"}) == spec.model_dump(
        exclude={"schema_version"}
    )
