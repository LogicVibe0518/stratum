"""Colours in words: "#ca9b5a" is "gold", and "green" is near nothing in this brand.

The model that reads a tweak used to see only token names ("palette.secondary"), so
"make the text green" for a brand with no green came back as a technical refusal
(live, 30 Sep). It now sees each brand colour with a plain name, and for every colour
word in the request, which brand colours are nearest. The names are what a designer
would say out loud, worked out from OKLCH (lightness, chroma, hue), not a lookup of
CSS names: a brand's own browns and creams rarely match one exactly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from stratum_worker.brand.oklab import distance, lch_of

# Below this chroma a colour reads as a grey (warm or cool), not as a hue.
NEUTRAL = 0.03
# Two colours this close in OKLab are "the same colour" to a client asking for one.
SAME = 0.09


def _neutral(lightness: float, chroma: float, hue: float) -> str:
    warm = chroma >= 0.012 and 30 <= hue <= 115
    cool = chroma >= 0.012 and 200 <= hue <= 290
    if lightness >= 0.985:
        return "white"
    if lightness >= 0.93:
        return "cream" if warm else "off-white"
    if lightness <= 0.15:
        return "black"
    if lightness <= 0.3:
        return "very dark brown" if warm else "near-black"
    tone = "light grey" if lightness >= 0.8 else "dark grey" if lightness < 0.45 else "grey"
    if warm:
        return f"warm {tone}"
    if cool:
        return f"cool {tone}"
    return tone


def _dark(name: str, lightness: float) -> str:
    return f"dark {name}" if lightness < 0.35 else name


def colour_name(hex_colour: str) -> str:
    """A plain name for a colour, as a designer would say it: "gold", "navy", "cream"."""
    lightness, chroma, hue = lch_of(hex_colour)
    if chroma < NEUTRAL:
        return _neutral(lightness, chroma, hue)
    if lightness >= 0.93 and 60 <= hue <= 115 and chroma < 0.08:
        return "cream"
    if hue >= 345 or hue < 15:
        return "pink" if lightness >= 0.72 else "raspberry"
    if hue < 40:
        if lightness < 0.45:
            return "maroon"
        return "salmon" if lightness >= 0.78 else "red"
    if hue < 65:
        if lightness < 0.55:
            return _dark("brown", lightness)
        return "orange" if chroma >= 0.08 else "tan"
    if hue < 100:
        if lightness < 0.55:
            return _dark("brown", lightness)
        if chroma >= 0.15 and hue < 80:
            return "orange"
        if lightness < 0.8:
            return "gold" if chroma >= 0.07 else "tan"
        return "yellow" if chroma >= 0.12 else "beige"
    if hue < 125:
        if lightness >= 0.8:
            return "yellow" if chroma >= 0.08 else "cream"
        return "olive"
    if hue < 170:
        if hue < 135 and chroma < 0.1 and lightness < 0.6:
            return "olive green"
        if lightness >= 0.8:
            return "light green"
        return "dark green" if lightness < 0.45 else "green"
    if hue < 215:
        if lightness >= 0.8:
            return "turquoise"
        return _dark("teal", lightness)
    if hue < 245:
        return "sky blue" if lightness >= 0.75 else _dark("blue", lightness)
    if hue < 280:
        if lightness < 0.35:
            return "navy"
        if chroma < 0.06:
            return "slate blue"
        return "light blue" if lightness >= 0.8 else "blue"
    if hue < 310:
        if lightness < 0.4:
            return "indigo"
        return "lavender" if lightness >= 0.8 else "purple"
    if lightness >= 0.8:
        return "pink"
    return "purple" if lightness < 0.5 else "magenta"


# What people type, and a typical shade of each. Only used to say which brand colour
# is nearest to what was asked for; nothing here ever reaches a poster.
WORDS: dict[str, str] = {
    "red": "#d62828",
    "maroon": "#800000",
    "burgundy": "#800020",
    "pink": "#f4a6c0",
    "orange": "#f77f00",
    "peach": "#ffcba4",
    "yellow": "#f6d743",
    "gold": "#c9a227",
    "golden": "#c9a227",
    "amber": "#ffbf00",
    "beige": "#e8dcc4",
    "cream": "#f8f1e0",
    "tan": "#d2b48c",
    "brown": "#7b4a2a",
    "olive": "#6b7a2a",
    "green": "#2e9e4f",
    "lime": "#9acd32",
    "mint": "#98e2c6",
    "teal": "#0f8b8d",
    "turquoise": "#40e0d0",
    "cyan": "#00b7c7",
    "blue": "#1f5fbf",
    "navy": "#14274e",
    "sky": "#87ceeb",
    "purple": "#6a3d9a",
    "violet": "#7f4fc9",
    "lavender": "#c3b1e1",
    "magenta": "#c2185b",
    "black": "#111111",
    "white": "#ffffff",
    "grey": "#8a8a8a",
    "gray": "#8a8a8a",
    "silver": "#c0c0c0",
}
_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Nearest:
    """A colour someone asked for, and how the brand can answer it."""

    word: str
    in_brand: str | None  # the brand token that IS this colour, if one is
    nearest: tuple[str, ...]  # the closest brand tokens, closest first


def words_in(message: str) -> list[str]:
    """Colour words in a message, in the order they appear, each once."""
    seen: list[str] = []
    for word in _WORD.findall(message.lower()):
        if word in WORDS and word not in seen:
            seen.append(word)
    return seen


def nearest(word: str, palette: Mapping[str, str], count: int = 2) -> Nearest:
    """Which brand colours answer a request for `word` (a key of WORDS).

    A brand colour whose own name says the word ("dark green" for "green") or that
    sits within SAME of the typical shade IS that colour. Otherwise the brand does
    not have it, and the nearest ones are what to offer instead.
    """
    target = WORDS[word]
    ranked = sorted(palette, key=lambda token: distance(palette[token], target))
    named = [token for token in ranked if word in colour_name(palette[token]).split()]
    close = [token for token in ranked if distance(palette[token], target) <= SAME]
    found = (named or close)[:1]
    return Nearest(word=word, in_brand=found[0] if found else None, nearest=tuple(ranked[:count]))


def named_palette(palette: Mapping[str, str]) -> dict[str, str]:
    """token -> "gold #ca9b5a", for showing a palette to a person or a model."""
    return {token: f"{colour_name(value)} {value}" for token, value in palette.items()}


__all__ = ["WORDS", "Nearest", "colour_name", "named_palette", "nearest", "words_in"]
