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


def test_a_version_1_poster_upgrades_to_the_current_schema_unchanged(spec: PosterSpec) -> None:
    # A real stored shape from before schema 2: no plate on the logo, no variants.
    data = spec.model_dump(mode="json")
    data["schema_version"] = 1
    data.pop("variants", None)
    for layer in data["layers"]:
        layer.pop("plate", None)
    upgraded = load(data)
    assert upgraded.schema_version == SCHEMA_VERSION == 3
    assert upgraded.model_dump(exclude={"schema_version"}) == spec.model_dump(
        exclude={"schema_version"}
    )


def test_a_version_2_poster_upgrades_to_3_carrying_no_layout_of_its_own(
    spec: PosterSpec,
) -> None:
    # A real stored shape from schema 2: no "variants" key at all.
    data = spec.model_dump(mode="json")
    data["schema_version"] = 2
    data.pop("variants")
    upgraded = load(data)
    assert upgraded.schema_version == 3
    assert upgraded.variants == {}
    assert upgraded.model_dump(exclude={"schema_version"}) == spec.model_dump(
        exclude={"schema_version"}
    )
