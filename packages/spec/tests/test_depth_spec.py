"""Words behind a photo's subject (spec schema 6): ImageLayer.subject + TextLayer.behind."""

from __future__ import annotations

import pytest
from conftest import ASSET_A, ASSET_B, make_spec
from pydantic import ValidationError
from stratum_spec import (
    Box,
    ImageLayer,
    PosterSpec,
    TextLayer,
    apply_ops,
    load,
)
from stratum_spec.ops import Move, Pan, SetVisible, SwapAsset, Zoom

MATTE = "c" * 64
OTHER = "d" * 64


def with_depth() -> PosterSpec:
    spec = make_spec()
    layers = [
        layer.model_copy(update={"subject": MATTE})
        if isinstance(layer, ImageLayer)
        else layer.model_copy(update={"behind": "bg"})
        if isinstance(layer, TextLayer)
        else layer
        for layer in spec.layers
    ]
    return PosterSpec.model_validate({**spec.model_dump(), "layers": layers})


def test_words_can_go_behind_a_photo_drawn_below_them() -> None:
    spec = with_depth()
    headline = spec.layer("headline")
    assert isinstance(headline, TextLayer) and headline.behind == "bg"


@pytest.mark.parametrize("behind", ["nothing", "logo", "headline"])
def test_words_cannot_go_behind_anything_but_a_photo_below(behind: str) -> None:
    data = make_spec().model_dump()
    data["layers"][1]["behind"] = behind
    with pytest.raises(ValidationError, match="not a photo drawn below it"):
        PosterSpec.model_validate(data)


def test_words_cannot_go_behind_a_photo_drawn_over_them() -> None:
    data = make_spec().model_dump()
    on_top = ImageLayer(id="top", box=Box(x=0, y=0, w=0.5, h=0.5), asset=OTHER).model_dump()
    data["layers"].append(on_top)
    data["layers"][1]["behind"] = "top"
    with pytest.raises(ValidationError, match="not a photo drawn below it"):
        PosterSpec.model_validate(data)


def test_the_matte_is_one_of_the_posters_assets() -> None:
    assert with_depth().assets() == {ASSET_A, ASSET_B, MATTE}
    assert make_spec().assets() == {ASSET_A, ASSET_B}


def test_a_version_5_poster_upgrades_with_every_word_in_front() -> None:
    data = make_spec().model_dump(mode="json")
    data["schema_version"] = 5
    for layer in data["layers"]:
        layer.pop("behind", None)
        layer.pop("subject", None)
    upgraded = load(data)
    assert upgraded.schema_version == 6
    assert upgraded == make_spec()


def test_swapping_the_photo_drops_its_matte_and_brings_the_words_forward() -> None:
    swapped = apply_ops(with_depth(), [SwapAsset(layer="bg", asset=OTHER)])
    photo, headline = swapped.layer("bg"), swapped.layer("headline")
    assert isinstance(photo, ImageLayer) and photo.asset == OTHER and photo.subject is None
    assert isinstance(headline, TextLayer) and headline.behind is None


@pytest.mark.parametrize(
    "op",
    [
        Pan(layer="bg", dx=0.1, dy=0.0),
        Zoom(layer="bg", factor=1.2),
        Move(layer="headline", dx=0.0, dy=-0.05),
        SetVisible(layer="logo", visible=False),
    ],
    ids=["pan", "zoom", "move-words", "hide-logo"],
)
def test_other_edits_keep_the_depth(op: object) -> None:
    edited = apply_ops(with_depth(), [op])  # type: ignore[list-item]
    photo, headline = edited.layer("bg"), edited.layer("headline")
    assert isinstance(photo, ImageLayer) and photo.subject == MATTE
    assert isinstance(headline, TextLayer) and headline.behind == "bg"
