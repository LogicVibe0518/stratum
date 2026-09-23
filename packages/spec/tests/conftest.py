from __future__ import annotations

import pytest
from stratum_spec import Box, Canvas, ImageLayer, LogoLayer, PosterSpec, TextLayer

ASSET_A = "a" * 64
ASSET_B = "b" * 64


def make_spec() -> PosterSpec:
    """A small, realistic poster used across the spec tests."""
    return PosterSpec(
        template_id="offer_basic",
        canvas=Canvas(aspect="4:5"),
        layers=[
            ImageLayer(id="bg", box=Box(x=0.0, y=0.0, w=1.0, h=1.0), asset=ASSET_A),
            TextLayer(
                id="headline",
                box=Box(x=0.08, y=0.62, w=0.84, h=0.14),
                content="20% off this week",
                font="font.display",
                color="palette.text",
            ),
            LogoLayer(id="logo", box=Box(x=0.72, y=0.06, w=0.2, h=0.1), asset=ASSET_B),
        ],
    )


@pytest.fixture
def spec() -> PosterSpec:
    return make_spec()
