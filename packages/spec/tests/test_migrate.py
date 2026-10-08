from __future__ import annotations

import pytest
from stratum_spec import (
    SCHEMA_VERSION,
    ImageLayer,
    PosterSpec,
    TextLayer,
    UnknownSchemaVersion,
    load,
    upgrade,
)


def as_approved(spec: PosterSpec) -> PosterSpec:
    """The spec as an old poster comes out of the upgrader: greedy wrapping kept."""
    return spec.model_copy(
        update={
            "layers": [
                layer.model_copy(update={"wrap": "greedy"})
                if isinstance(layer, TextLayer)
                else layer
                for layer in spec.layers
            ]
        }
    )


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
        layer.pop("crop", None)
        layer.pop("wrap", None)
    upgraded = load(data)
    assert upgraded.schema_version == SCHEMA_VERSION == 7
    assert upgraded.model_dump(exclude={"schema_version"}) == as_approved(spec).model_dump(
        exclude={"schema_version"}
    )


def test_a_version_2_poster_upgrades_to_3_carrying_no_layout_of_its_own(
    spec: PosterSpec,
) -> None:
    # A real stored shape from schema 2: no "variants" key at all.
    data = spec.model_dump(mode="json")
    data["schema_version"] = 2
    data.pop("variants")
    for layer in data["layers"]:
        layer.pop("crop", None)
        layer.pop("wrap", None)
    upgraded = load(data)
    assert upgraded.schema_version == SCHEMA_VERSION
    assert upgraded.variants == {}
    assert upgraded.model_dump(exclude={"schema_version"}) == as_approved(spec).model_dump(
        exclude={"schema_version"}
    )


def test_a_version_3_poster_upgrades_to_4_showing_the_centre_of_every_photo(
    spec: PosterSpec,
) -> None:
    # A real stored shape from schema 3: image layers and size overrides with no "crop".
    data = spec.model_dump(mode="json")
    data["schema_version"] = 3
    for layer in data["layers"]:
        layer.pop("crop", None)
    data["variants"] = {"9:16": {"bg": {"dim": 0.2}}}
    upgraded = load(data)
    assert upgraded.schema_version == SCHEMA_VERSION
    photo = upgraded.layer("bg")
    assert isinstance(photo, ImageLayer)
    assert photo.crop is None
    assert upgraded.variants["9:16"]["bg"].crop is None


def test_a_version_4_poster_keeps_the_wrapping_it_was_approved_with(spec: PosterSpec) -> None:
    # A real stored shape from schema 4: text layers with no "wrap" key.
    data = spec.model_dump(mode="json")
    data["schema_version"] = 4
    for layer in data["layers"]:
        layer.pop("wrap", None)
    upgraded = load(data)
    assert upgraded.schema_version == SCHEMA_VERSION
    headline = upgraded.layer("headline")
    assert isinstance(headline, TextLayer) and headline.wrap == "greedy"
    assert upgraded.layer("bg") == spec.layer("bg")  # nothing else changes


def test_new_text_breaks_by_meaning(spec: PosterSpec) -> None:
    headline = spec.layer("headline")
    assert isinstance(headline, TextLayer) and headline.wrap == "balanced"
