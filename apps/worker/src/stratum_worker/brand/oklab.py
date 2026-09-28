"""OKLab: a colour space where distance matches what people see.

RGB distance is a poor judge of "same colour". Two navies that look identical can be
far apart in RGB, and a yellow and a lime that look different can be close. OKLab
(Björn Ottosson, 2020) is built so that equal distances look like equal differences,
which is what the logo reader needs to decide "these pixels are one brand colour".

Coordinates:
- L: lightness, 0 (black) to 1 (white).
- a, b: the green-red and blue-yellow axes. Near 0 means grey.
- OKLCH is the same space in polar form: C (chroma, how colourful) and h (hue angle).

A difference of about 0.02 is the smallest most people notice.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

Floats = npt.NDArray[np.float64]

# sRGB (linear) -> LMS cone response, then LMS' -> Lab. Ottosson's published matrices.
_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ]
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ]
)
_M2_INV = np.array(
    [
        [1.0, 0.3963377774, 0.2158037573],
        [1.0, -0.1055613458, -0.0638541728],
        [1.0, -0.0894841775, -1.2914855480],
    ]
)
_M1_INV = np.array(
    [
        [4.0767416621, -3.3077115913, 0.2309699292],
        [-1.2684380046, 2.6097574011, -0.3413193965],
        [-0.0041960863, -0.7034186147, 1.7076147010],
    ]
)


def _to_linear(srgb: Floats) -> Floats:
    """Undo the sRGB curve: stored values are gamma-encoded, light adds linearly."""
    out: Floats = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    return out


def _to_srgb(linear: Floats) -> Floats:
    clipped = np.clip(linear, 0.0, None)
    out: Floats = np.where(
        clipped <= 0.0031308, clipped * 12.92, 1.055 * clipped ** (1 / 2.4) - 0.055
    )
    return out


def rgb_to_oklab(rgb: npt.NDArray[np.uint8] | Floats) -> Floats:
    """An (..., 3) array of 0-255 sRGB values to (..., 3) OKLab."""
    srgb = np.asarray(rgb, dtype=np.float64) / 255.0
    lms = _to_linear(srgb) @ _M1.T
    lab: Floats = np.cbrt(lms) @ _M2.T
    return lab


def oklab_to_linear(lab: Floats) -> Floats:
    lms = (np.asarray(lab, dtype=np.float64) @ _M2_INV.T) ** 3
    linear: Floats = lms @ _M1_INV.T
    return linear


def oklab_to_rgb(lab: Floats) -> npt.NDArray[np.uint8]:
    """(..., 3) OKLab to 0-255 sRGB, clipped into range."""
    srgb = _to_srgb(oklab_to_linear(lab))
    out: npt.NDArray[np.uint8] = np.clip(np.round(srgb * 255.0), 0, 255).astype(np.uint8)
    return out


def hex_of(rgb: npt.NDArray[np.uint8] | tuple[int, int, int]) -> str:
    red, green, blue = (int(v) for v in rgb)
    return f"#{red:02x}{green:02x}{blue:02x}"


def rgb_of(hex_colour: str) -> tuple[int, int, int]:
    text = hex_colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def lab_of(hex_colour: str) -> Floats:
    return rgb_to_oklab(np.array(rgb_of(hex_colour), dtype=np.float64))


def lch_of(hex_colour: str) -> tuple[float, float, float]:
    """(lightness, chroma, hue in degrees) of one colour."""
    lightness, a, b = (float(v) for v in lab_of(hex_colour))
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0


def distance(one: str, two: str) -> float:
    """How different two colours look. About 0.02 is the smallest visible step."""
    return float(np.linalg.norm(lab_of(one) - lab_of(two)))


def in_gamut(lab: Floats, tolerance: float = 1e-4) -> bool:
    linear = oklab_to_linear(lab)
    return bool(np.all(linear >= -tolerance) and np.all(linear <= 1 + tolerance))


def from_lch(lightness: float, chroma: float, hue: float) -> str:
    """The colour at this lightness and hue, as colourful as asked or as sRGB allows.

    Not every (L, C, h) exists on a screen: a very light, very saturated blue is not
    displayable. Rather than clip each channel - which shifts the hue, so a derived
    tint of a brand navy could come out purple - chroma is reduced until the colour
    fits, keeping lightness and hue exactly.
    """
    lightness = min(max(lightness, 0.0), 1.0)
    angle = math.radians(hue)

    def at(c: float) -> Floats:
        return np.array([lightness, c * math.cos(angle), c * math.sin(angle)])

    if in_gamut(at(chroma)):
        return hex_of(oklab_to_rgb(at(chroma)))
    low, high = 0.0, chroma
    for _ in range(30):
        middle = (low + high) / 2
        if in_gamut(at(middle)):
            low = middle
        else:
            high = middle
    return hex_of(oklab_to_rgb(at(low)))


__all__ = [
    "distance",
    "from_lch",
    "hex_of",
    "in_gamut",
    "lab_of",
    "lch_of",
    "oklab_to_rgb",
    "rgb_of",
    "rgb_to_oklab",
]
