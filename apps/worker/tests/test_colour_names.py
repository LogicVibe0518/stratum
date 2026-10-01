"""Colours in words (M10c): what a designer would call each brand colour.

Live, 30 Sep: "change the colour of the text to green" on a poster for a brand with
no green came back as "needs a palette token". The model only ever saw token names.
"""

from __future__ import annotations

import pytest
from stratum_worker.brand.names import colour_name, named_palette, nearest, words_in

# The live pilot client's palette (a meditation centre: browns, gold and cream).
WARM = {
    "text": "#251e14",
    "muted": "#7b7061",
    "primary": "#6b5941",
    "surface": "#fcf6ed",
    "secondary": "#ca9b5a",
    "text_on_dark": "#ffffff",
    "text_on_primary": "#ffffff",
}
# The fictional test plumber.
PLUMBER = {
    "primary": "#12264a",
    "secondary": "#ff6a00",
    "surface": "#f2f4f7",
    "text": "#12264a",
    "text_on_primary": "#ffffff",
    "muted": "#5b6b82",
    "text_on_dark": "#ffffff",
}


@pytest.mark.parametrize(
    ("hex_colour", "name"),
    [
        ("#251e14", "very dark brown"),
        ("#7b7061", "warm grey"),
        ("#6b5941", "brown"),
        ("#fcf6ed", "cream"),
        ("#ca9b5a", "gold"),
        ("#ffffff", "white"),
        ("#12264a", "navy"),
        ("#ff6a00", "orange"),
        ("#f2f4f7", "off-white"),
        ("#5b6b82", "slate blue"),
    ],
)
def test_brand_colours_get_the_names_a_designer_would_use(hex_colour: str, name: str) -> None:
    assert colour_name(hex_colour) == name


@pytest.mark.parametrize(
    ("hex_colour", "name"),
    [
        ("#d62828", "red"),
        ("#2e9e4f", "green"),
        ("#008000", "green"),
        ("#1f5fbf", "blue"),
        ("#f6d743", "yellow"),
        ("#6a3d9a", "purple"),
        ("#f4a6c0", "pink"),
        ("#000000", "black"),
        ("#808080", "grey"),
        ("#0f8b8d", "teal"),
        ("#800000", "maroon"),
    ],
)
def test_plain_colours_get_plain_names(hex_colour: str, name: str) -> None:
    assert colour_name(hex_colour) == name


def test_green_is_not_in_a_brown_and_gold_brand() -> None:
    found = nearest("green", WARM)
    assert found.in_brand is None
    assert len(found.nearest) == 2


@pytest.mark.parametrize(
    ("word", "token"),
    [("gold", "secondary"), ("brown", "primary"), ("cream", "surface"), ("black", "text")],
)
def test_a_colour_the_brand_has_is_found_by_its_name(word: str, token: str) -> None:
    assert nearest(word, WARM).in_brand == token


def test_orange_and_navy_are_found_in_the_plumbers_brand() -> None:
    assert nearest("orange", PLUMBER).in_brand == "secondary"
    assert nearest("navy", PLUMBER).in_brand in {"primary", "text"}
    assert nearest("green", PLUMBER).in_brand is None


def test_colour_words_are_found_once_each_in_order() -> None:
    assert words_in("Make the TEXT green, the panel gold - green again!") == ["green", "gold"]
    assert words_in("make the logo bigger") == []


def test_a_palette_is_shown_with_names_and_values() -> None:
    assert named_palette(WARM)["secondary"] == "gold #ca9b5a"
