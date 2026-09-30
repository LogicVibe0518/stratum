"""Schema 4: which part of a photo shows is part of the poster.

The crop is chosen when a post is drafted, for each size, so the photo's subject is
not cut off and not under the words. It is data like everything else: an edit that
swaps the photo must not keep framing the new photo by where the old one's subject
stood.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from stratum_spec import (
    Crop,
    ImageLayer,
    LayoutOverride,
    Move,
    PosterSpec,
    SwapAsset,
    apply_op,
    retarget,
)

NEW_PHOTO = "c" * 64


def framed(spec: PosterSpec) -> PosterSpec:
    """The fixture poster with a crop in its base size and a different one for stories."""
    photo = spec.layer("bg")
    assert isinstance(photo, ImageLayer)
    layers = [
        photo.model_copy(update={"crop": Crop(x=0.2, y=0.5)}) if one.id == "bg" else one
        for one in spec.layers
    ]
    return spec.model_copy(
        update={
            "layers": layers,
            "variants": {
                "9:16": {"bg": LayoutOverride(crop=Crop(x=0.8, y=0.4, zoom=1.2))},
                "1:1": {"bg": LayoutOverride(dim=0.1)},
            },
        }
    )


def photo_of(spec: PosterSpec) -> ImageLayer:
    layer = spec.layer("bg")
    assert isinstance(layer, ImageLayer)
    return layer


def test_a_photo_shows_its_centre_unless_told_otherwise(spec: PosterSpec) -> None:
    assert photo_of(spec).crop is None
    assert Crop() == Crop(x=0.5, y=0.5, zoom=1.0)


@pytest.mark.parametrize(
    "fields", [{"x": -0.1}, {"x": 1.1}, {"y": 2.0}, {"zoom": 0.9}, {"zoom": 3.5}]
)
def test_a_crop_outside_the_photo_is_refused(fields: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Crop.model_validate(fields)


def test_each_size_carries_its_own_crop(spec: PosterSpec) -> None:
    poster = framed(spec)
    assert photo_of(retarget(poster, "4:5")).crop == Crop(x=0.2, y=0.5)
    assert photo_of(retarget(poster, "9:16")).crop == Crop(x=0.8, y=0.4, zoom=1.2)
    # A size that sets something else keeps the base size's crop.
    assert photo_of(retarget(poster, "1:1")).crop == Crop(x=0.2, y=0.5)


def test_the_crop_survives_a_save_and_load(spec: PosterSpec) -> None:
    poster = framed(spec)
    again = PosterSpec.model_validate_json(poster.model_dump_json())
    assert again == poster


def test_swapping_the_photo_puts_every_size_back_to_the_centre(spec: PosterSpec) -> None:
    swapped = apply_op(framed(spec), SwapAsset(layer="bg", asset=NEW_PHOTO))
    assert photo_of(swapped).asset == NEW_PHOTO
    assert photo_of(swapped).crop is None
    assert swapped.variants["9:16"]["bg"].crop is None
    # Nothing else about the sizes changes.
    assert swapped.variants["1:1"]["bg"].dim == 0.1


def test_moving_the_photo_keeps_its_crop(spec: PosterSpec) -> None:
    moved = apply_op(framed(spec), Move(layer="bg", dx=0.01, dy=0.0))
    assert photo_of(moved).crop == Crop(x=0.2, y=0.5)
    assert moved.variants["9:16"]["bg"].crop == Crop(x=0.8, y=0.4, zoom=1.2)


def test_swapping_a_logo_is_unaffected(spec: PosterSpec) -> None:
    swapped = apply_op(framed(spec), SwapAsset(layer="logo", asset=NEW_PHOTO))
    assert photo_of(swapped).crop == Crop(x=0.2, y=0.5)
