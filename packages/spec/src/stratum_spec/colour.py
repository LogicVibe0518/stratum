"""Colour arithmetic, so "is this readable?" is a number and not an opinion.

Everything here follows WCAG 2.2's definitions of relative luminance and contrast
ratio. The thresholds are not ours to invent: 4.5:1 for normal text, 3:1 for large
text, and "large" means 24px or 18.66px bold.
"""

from __future__ import annotations

# WCAG contrast floors. Below these, a real person on a phone in daylight cannot
# read the text - which for a poster is not an accessibility nicety, it is the
# entire job of the poster.
MIN_CONTRAST_NORMAL = 4.5
MIN_CONTRAST_LARGE = 3.0
# WCAG calls text "large" at 24px. Our sizes are fractions of canvas height, and a
# 1080x1350 poster means 24px is about 0.018 - but poster text that small is a
# mistake anyway, so we use a stricter idea of large.
LARGE_TEXT_FRACTION = 0.045


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def rgb(hex_colour: str) -> tuple[float, float, float]:
    text = hex_colour.lstrip("#")
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(hex_colour: str) -> float:
    """WCAG relative luminance: how bright a colour looks, 0 (black) to 1 (white).

    Not the average of R, G and B - human eyes are far more sensitive to green than
    to blue, which is why the weights are 0.2126 / 0.7152 / 0.0722.
    """
    red, green, blue = (_channel(value) for value in rgb(hex_colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(one: str, two: str) -> float:
    """How different two colours look, from 1.0 (identical) to 21.0 (black on white)."""
    first, second = luminance(one), luminance(two)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def blend(top: str, bottom: str, alpha: float) -> str:
    """What a semi-transparent colour actually looks like over something else.

    A brand band at 0.92 opacity is not the brand colour - it is 92% of it mixed with
    whatever is underneath. Checking contrast against the pure colour would pass
    posters that are genuinely hard to read.
    """
    mixed = (
        round(255 * (t * alpha + b * (1 - alpha)))
        for t, b in zip(rgb(top), rgb(bottom), strict=True)
    )
    return "#" + "".join(f"{value:02x}" for value in mixed)


def min_contrast_for(size_fraction: float) -> float:
    return MIN_CONTRAST_LARGE if size_fraction >= LARGE_TEXT_FRACTION else MIN_CONTRAST_NORMAL


__all__ = [
    "LARGE_TEXT_FRACTION",
    "MIN_CONTRAST_LARGE",
    "MIN_CONTRAST_NORMAL",
    "blend",
    "contrast_ratio",
    "luminance",
    "min_contrast_for",
    "rgb",
]
