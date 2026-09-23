from __future__ import annotations

import pytest
from conftest import make_spec
from hypothesis import given
from hypothesis import strategies as st
from stratum_spec import (
    Move,
    NeedsHuman,
    OpError,
    PosterSpec,
    Scale,
    SetColor,
    SetText,
    SetVisible,
    SwapAsset,
    apply_op,
    apply_ops,
)
from stratum_spec.models import TextLayer

ASSET_C = "c" * 64


def test_set_text_returns_a_new_spec(spec: PosterSpec) -> None:
    updated = apply_op(spec, SetText(layer="headline", text="Half price Friday"))
    headline = updated.layer("headline")
    assert isinstance(headline, TextLayer)
    assert headline.content == "Half price Friday"
    original = spec.layer("headline")
    assert isinstance(original, TextLayer)
    assert original.content == "20% off this week"  # input untouched


def test_unknown_layer_is_an_error(spec: PosterSpec) -> None:
    with pytest.raises(OpError, match="unknown layer"):
        apply_op(spec, SetText(layer="nope", text="x"))


def test_wrong_layer_type_is_an_error(spec: PosterSpec) -> None:
    with pytest.raises(OpError, match="not a text layer"):
        apply_op(spec, SetText(layer="bg", text="x"))


def test_colour_on_an_image_layer_is_an_error(spec: PosterSpec) -> None:
    with pytest.raises(OpError, match="no colour"):
        apply_op(spec, SetColor(layer="bg", color="palette.primary"))


def test_asset_swap_needs_an_asset_layer(spec: PosterSpec) -> None:
    with pytest.raises(OpError, match="no asset"):
        apply_op(spec, SwapAsset(layer="headline", asset=ASSET_C))


def test_scale_keeps_the_centre(spec: PosterSpec) -> None:
    before = spec.layer("logo").box
    after = apply_op(spec, Scale(layer="logo", factor=2.0)).layer("logo").box
    assert after.w == pytest.approx(before.w * 2)
    assert (after.x + after.w / 2) == pytest.approx(before.x + before.w / 2)


def test_needs_human_changes_nothing(spec: PosterSpec) -> None:
    assert apply_op(spec, NeedsHuman(reason="make it pop")) == spec


def test_ops_apply_in_order(spec: PosterSpec) -> None:
    updated = apply_ops(
        spec,
        [
            SetText(layer="headline", text="Boiler service GBP 69"),
            Scale(layer="logo", factor=1.3),
            SetVisible(layer="logo", visible=False),
        ],
    )
    assert updated.layer("logo").visible is False


@given(
    factor=st.floats(min_value=0.01, max_value=5.0),
    dx=st.floats(min_value=-1.0, max_value=1.0),
    dy=st.floats(min_value=-1.0, max_value=1.0),
)
def test_any_move_or_scale_leaves_a_valid_spec(factor: float, dx: float, dy: float) -> None:
    """The invariant that matters: ops can never produce an invalid poster."""
    updated = apply_ops(
        make_spec(), [Scale(layer="logo", factor=factor), Move(layer="logo", dx=dx, dy=dy)]
    )
    PosterSpec.model_validate(updated.model_dump())
