"""What the model reading a tweak is told (M10c), and that the prompt carries it.

Each layer by where it is and its colour in words; each brand colour by name and what
uses it; the nearest brand colours to a colour the message names; this design's
style settings; the last few changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import Box, BrandKit, Crop, ImageLayer, ShapeLayer, TextLayer
from stratum_worker.design import lay_out
from stratum_worker.edit.context import (
    asked_colours,
    colour_lines,
    describe_layers,
    layer_line,
    recent_lines,
    style_menu,
    where,
)
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.tasks import EditParseRequest, TaskName

REPO = Path(__file__).resolve().parents[3]
PANEL = "ds.panel.1.0010110"
WARM = {
    "text": "#251e14",
    "muted": "#7b7061",
    "primary": "#6b5941",
    "surface": "#fcf6ed",
    "secondary": "#ca9b5a",
    "text_on_dark": "#ffffff",
    "text_on_primary": "#ffffff",
}


@pytest.fixture
def kit() -> BrandKit:
    plumber = json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text())
    return BrandKit.model_validate({**plumber, "palette": WARM})


@pytest.mark.parametrize(
    ("box", "said"),
    [
        (Box(x=0, y=0, w=1, h=1), "the whole poster"),
        (Box(x=0, y=0, w=1, h=0.42), "the top 42%"),
        (Box(x=0, y=0.58, w=1, h=0.42), "the bottom 42%"),
        (Box(x=0, y=0, w=0.58, h=1), "the left 58%"),
        (Box(x=0.06, y=0.06, w=0.2, h=0.08), "top left"),
        (Box(x=0.08, y=0.7, w=0.84, h=0.1), "bottom centre"),
        (Box(x=0.7, y=0.45, w=0.2, h=0.1), "middle right"),
        (Box(x=0.4, y=0.45, w=0.2, h=0.1), "the centre"),
    ],
)
def test_where_a_layer_is_in_words(box: Box, said: str) -> None:
    assert where(box) == said


def test_a_text_layer_says_where_its_colour_and_its_room(kit: BrandKit) -> None:
    layer = TextLayer(
        id="headline",
        box=Box(x=0.06, y=0.64, w=0.88, h=0.14),
        content="Summer meditation camp",
        font="font.display",
        color="palette.text",
    )
    line = layer_line(layer, kit, (1080, 1350), fits=40)
    assert line == (
        "text (headline), bottom centre, very dark brown (palette.text), "
        "fits 40 characters, font display: Summer meditation camp"
    )


def test_a_shape_says_its_colour_by_name(kit: BrandKit) -> None:
    panel = ShapeLayer(id="panel", box=Box(x=0, y=0.58, w=1, h=0.42), fill="palette.surface")
    assert layer_line(panel, kit, (1080, 1350)) == (
        "shape (colour panel), the bottom 42%, cream (palette.surface)"
    )


def test_a_photo_says_which_way_it_can_move(kit: BrandKit) -> None:
    photo = ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=0.58), asset="a" * 64)
    # A tall photo in a wide box: only up and down.
    assert "can move up or down" in layer_line(photo, kit, (1080, 1350), photo_size=(800, 1200))
    # A wide photo in the same box: only left and right.
    assert "can move left or right" in layer_line(photo, kit, (1080, 1350), photo_size=(3000, 1000))
    zoomed = photo.model_copy(update={"crop": Crop(zoom=1.4), "dim": 0.3})
    line = layer_line(zoomed, kit, (1080, 1350), photo_size=(3000, 1000))
    assert "zoomed 1.40x, can move any way" in line and "darkened 0.30" in line


def test_a_hidden_layer_says_so(kit: BrandKit) -> None:
    panel = ShapeLayer(
        id="panel", box=Box(x=0, y=0, w=1, h=0.4), fill="palette.surface", visible=False
    )
    assert layer_line(panel, kit, (1080, 1350)).endswith(", hidden")


def test_brand_colours_are_named_with_what_uses_them(kit: BrandKit) -> None:
    spec = lay_out(PANEL, kit, REPO)
    assert spec is not None
    lines = colour_lines(spec, kit)
    assert lines["palette.surface"].startswith("cream #fcf6ed; used by the colour panel")
    assert lines["palette.secondary"] == "gold #ca9b5a"  # this design has no accent line
    assert "headline" in lines["palette.text"]


def test_a_colour_the_brand_lacks_is_said_with_the_nearest_ones(kit: BrandKit) -> None:
    [green] = asked_colours("change the colour of the text to green", kit)
    assert green.startswith("green: NOT a brand colour; the nearest brand colours are palette.")
    [gold] = asked_colours("make the headline gold", kit)
    assert gold == "gold: the brand's gold is palette.secondary"
    assert asked_colours("make the logo bigger", kit) == []


def test_the_style_menu_names_settings_values_and_brand_colours(kit: BrandKit) -> None:
    menu = style_menu(PANEL, kit, photos=1)
    assert 'This poster is the "Photo and panel" design.' in menu
    assert "- fill: now surface; can be primary | dark." in menu
    assert "primary = brown, surface = cream, dark = very dark brown" in menu
    assert "- side: now end; can be start." in menu
    assert "- type: Words only" in menu
    assert (
        "- pair: Two photos. NOT available: needs 2 of the client's photos and there are 1" in menu
    )
    assert "NOT available" not in style_menu(PANEL, kit, photos=2)


def test_a_fixed_template_has_no_style_menu(kit: BrandKit) -> None:
    assert "no style settings" in style_menu("full_bleed", kit, photos=3)


def test_recent_changes_are_newest_last_and_short() -> None:
    rows = [{"message": f"change {i}", "outcome": "applied"} for i in range(8)]
    assert recent_lines(rows) == [f"change {i} -> applied" for i in range(3, 8)]


def test_every_layer_of_a_real_design_is_described(kit: BrandKit) -> None:
    spec = lay_out(PANEL, kit, REPO)
    assert spec is not None
    described = describe_layers(spec, kit, {"headline": 30}, {})
    assert set(described) == {layer.id for layer in spec.layers}
    assert all(len(line) <= 240 for line in described.values())


def test_the_prompt_carries_every_part(kit: BrandKit) -> None:
    request = EditParseRequest(
        message="make the text green",
        layers={"headline": "text (headline), bottom centre"},
        colours={"palette.secondary": "gold #ca9b5a"},
        asked=["green: NOT a brand colour"],
        design=style_menu(PANEL, kit, photos=1),
        recent=["make the logo bigger -> applied"],
    )
    rendered = render(TaskName.EDIT_PARSE, request)
    for part in (
        "palette.secondary: gold #ca9b5a",
        "green: NOT a brand colour",
        "Photo and panel",
        "make the logo bigger -> applied",
        "make the text green",
    ):
        assert part in rendered.user
    assert '"op": "restyle"' in rendered.system and '"op": "ask"' in rendered.system
    assert "@v7" in rendered.id
