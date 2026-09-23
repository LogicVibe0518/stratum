from __future__ import annotations

import pytest
from pydantic import ValidationError
from stratum_spec import SCHEMA_VERSION, Box, Canvas, ImageLayer, PosterSpec, TextLayer


def test_schema_version_is_positive_int() -> None:
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_canvas_size_follows_aspect() -> None:
    assert Canvas(aspect="1:1", base_width=1080).size == (1080, 1080)
    assert Canvas(aspect="4:5", base_width=1080).size == (1080, 1350)
    assert Canvas(aspect="9:16", base_width=1080).size == (1080, 1920)


def test_layer_ids_must_be_unique() -> None:
    box = Box(x=0.0, y=0.0, w=1.0, h=1.0)
    with pytest.raises(ValidationError, match="unique"):
        PosterSpec(
            template_id="t",
            layers=[ImageLayer(id="bg", box=box), ImageLayer(id="bg", box=box)],
        )


def test_colour_must_be_a_brand_token(spec: PosterSpec) -> None:
    with pytest.raises(ValidationError):
        TextLayer(
            id="headline",
            box=Box(x=0.0, y=0.0, w=1.0, h=0.1),
            content="hi",
            color="#ff0000",  # raw hex is rejected on purpose
        )


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ImageLayer(id="bg", box=Box(x=0.0, y=0.0, w=1.0, h=1.0), sneaky=True)


def test_spec_is_immutable(spec: PosterSpec) -> None:
    with pytest.raises(ValidationError):
        spec.layers[1].content = "changed"  # type: ignore[misc]


def test_text_size_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="min_size"):
        TextLayer(
            id="t",
            box=Box(x=0.0, y=0.0, w=1.0, h=0.1),
            content="hi",
            min_size=0.2,
            max_size=0.1,
        )
