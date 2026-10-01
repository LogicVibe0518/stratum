"""Pan, zoom and dim: the photo inside its frame, changed by a sentence or a slider.

"Move the photo up so his head shows", "zoom in on the van", "darken the photo so the
words read". Each changes the crop or the dim of every size the same way, never the
photo itself, and a size framed on its own keeps its own framing, moved by the same
amount.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError
from stratum_spec import (
    Crop,
    Dim,
    EditOp,
    ImageLayer,
    LayoutOverride,
    OpError,
    Pan,
    PosterSpec,
    Zoom,
    apply_op,
    retarget,
)

OPS: TypeAdapter[list[EditOp]] = TypeAdapter(list[EditOp])


def framed(spec: PosterSpec) -> PosterSpec:
    photo = spec.layer("bg")
    layers = [
        photo.model_copy(update={"crop": Crop(x=0.4, y=0.5)}) if one.id == "bg" else one
        for one in spec.layers
    ]
    return spec.model_copy(
        update={
            "layers": layers,
            "variants": {
                "9:16": {"bg": LayoutOverride(crop=Crop(x=0.6, y=0.3, zoom=1.2), dim=0.2)},
                "1:1": {"bg": LayoutOverride(opacity=0.9)},
            },
        }
    )


def photo(spec: PosterSpec, aspect: str = "4:5") -> ImageLayer:
    layer = retarget(spec, aspect).layer("bg")  # type: ignore[arg-type]
    assert isinstance(layer, ImageLayer)
    return layer


def test_pan_moves_the_picture_so_the_window_moves_the_other_way(spec: PosterSpec) -> None:
    moved = apply_op(framed(spec), Pan(layer="bg", dx=0.1, dy=-0.2))
    crop = photo(moved).crop
    assert crop is not None
    assert (crop.x, crop.y, crop.zoom) == (pytest.approx(0.3), pytest.approx(0.7), 1.0)


def test_pan_moves_every_size_by_the_same_amount(spec: PosterSpec) -> None:
    moved = apply_op(framed(spec), Pan(layer="bg", dx=0.1, dy=0.0))
    story = photo(moved, "9:16").crop
    assert story is not None
    assert story.x == pytest.approx(0.5) and story.y == pytest.approx(0.3)
    assert story.zoom == 1.2
    # A size without its own crop follows the base size.
    assert photo(moved, "1:1").crop == photo(moved).crop


def test_pan_stops_at_the_edge_of_the_photo(spec: PosterSpec) -> None:
    moved = apply_op(framed(spec), Pan(layer="bg", dx=-1.0, dy=1.0))
    assert photo(moved).crop == Crop(x=1.0, y=0.0)


def test_a_photo_never_framed_pans_from_its_centre(spec: PosterSpec) -> None:
    moved = apply_op(spec, Pan(layer="bg", dx=0.0, dy=0.25))
    assert photo(moved).crop == Crop(x=0.5, y=0.25)


def test_zoom_multiplies_and_stays_within_one_and_three(spec: PosterSpec) -> None:
    poster = framed(spec)
    closer = apply_op(poster, Zoom(layer="bg", factor=1.5))
    crop = photo(closer).crop
    story = photo(closer, "9:16").crop
    assert crop is not None and crop.zoom == pytest.approx(1.5)
    assert story is not None and story.zoom == pytest.approx(1.8)
    assert photo(apply_op(poster, Zoom(layer="bg", factor=0.5))).crop == Crop(x=0.4, y=0.5)
    widest = photo(apply_op(closer, Zoom(layer="bg", factor=3.0)), "9:16").crop
    assert widest is not None and widest.zoom == 3.0


def test_dim_sets_every_size_including_one_dimmed_on_its_own(spec: PosterSpec) -> None:
    darker = apply_op(framed(spec), Dim(layer="bg", amount=0.35))
    assert photo(darker).dim == 0.35
    assert photo(darker, "9:16").dim == 0.35
    assert darker.variants["1:1"]["bg"].dim is None  # follows the base size


@pytest.mark.parametrize(
    "op",
    [
        Pan(layer="headline", dx=0.1, dy=0.0),
        Zoom(layer="logo", factor=1.2),
        Dim(layer="logo", amount=0.2),
    ],
)
def test_only_photos_pan_zoom_or_dim(spec: PosterSpec, op: EditOp) -> None:
    with pytest.raises(OpError, match="is not a photo"):
        apply_op(spec, op)


def test_a_photo_shown_whole_cannot_be_panned(spec: PosterSpec) -> None:
    whole = spec.model_copy(
        update={
            "layers": [
                one.model_copy(update={"fit": "contain"}) if one.id == "bg" else one
                for one in spec.layers
            ]
        }
    )
    with pytest.raises(OpError, match="shows the whole photo"):
        apply_op(whole, Pan(layer="bg", dx=0.1, dy=0.0))


def test_the_input_is_never_changed(spec: PosterSpec) -> None:
    poster = framed(spec)
    before = poster.model_dump()
    apply_op(poster, Pan(layer="bg", dx=0.2, dy=0.2))
    apply_op(poster, Zoom(layer="bg", factor=2.0))
    apply_op(poster, Dim(layer="bg", amount=0.5))
    assert poster.model_dump() == before


def test_the_new_ops_arrive_as_json_like_every_other(spec: PosterSpec) -> None:
    ops = OPS.validate_python(
        [
            {"op": "pan", "layer": "bg", "dx": 0, "dy": 0.1},
            {"op": "zoom", "layer": "bg", "factor": 1.3},
            {"op": "dim", "layer": "bg", "amount": 0.3},
        ]
    )
    assert [type(op) for op in ops] == [Pan, Zoom, Dim]


@pytest.mark.parametrize(
    "raw",
    [
        {"op": "pan", "layer": "bg", "dx": 1.5, "dy": 0},
        {"op": "zoom", "layer": "bg", "factor": 0},
        {"op": "zoom", "layer": "bg", "factor": 4},
        {"op": "dim", "layer": "bg", "amount": 0.95},
        {"op": "dim", "layer": "bg", "amount": -0.1},
    ],
)
def test_out_of_range_values_are_refused(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OPS.validate_python([raw])
