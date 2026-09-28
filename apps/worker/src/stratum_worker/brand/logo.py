"""Read the brand colours out of a logo file.

What a person does when they eyeball a logo, written down:
1. Look only at the logo, not what it sits on. Transparent pixels are ignored; so is
   a plain white or black background around it. A coloured background that fills
   the file is kept, because then it is a badge, and the badge IS the brand colour.
2. Ignore the edges. Where two colours meet, anti-aliasing makes pixels that are a
   blend of both. There are thousands of them in any logo, and none is a brand
   colour. Only pixels in a flat patch (every neighbour nearly the same) are counted.
3. Group what is left into colours a person would call "the same" - measured in
   OKLab, where distance matches what the eye sees - and report each group with its
   share of the logo.
4. Report each group as a colour that is really in the file. A flat logo fill is one
   exact value, and a designer expects to see #ff6a00 back, not the average of
   #ff6a00 and its JPEG noise.

No model is involved. The same file always gives the same answer, and every step can
be checked by hand.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

from stratum_worker.brand.oklab import Floats, hex_of, oklab_to_rgb, rgb_to_oklab

# Longest side analysed. A logo's colours do not change with its size, and 400px keeps
# the whole read under ~50ms. Box averaging leaves flat fills exactly as they were.
MAX_SIDE = 400
# Alpha at or above this is "solid". Anything less is a soft edge or a shadow.
SOLID = 250
# A patch is flat when no channel varies by more than this across 3x3 pixels. The
# looser step allows for JPEG noise in files that were saved badly.
FLAT = 10
FLAT_LOOSE = 24
# Two groups closer than this in OKLab are one colour. 0.02 is the smallest visible
# difference; 0.05 is "a person would call these the same colour".
SAME = 0.05
MAX_COLOURS = 8
# Groups smaller than this are specks, stray edge pixels or noise.
MIN_SHARE = 0.02
# Chroma above this reads as a colour rather than a grey. A slate blue sits at ~0.04,
# pure red at 0.26.
COLOURFUL = 0.035
# A plain background: this much of the border within this distance of one grey.
BORDER_AGREES = 0.6
BORDER_SAME = 0.04
BACKGROUND_REMOVE = 0.06

Tone = Literal["colour", "dark", "light"]


class LogoError(ValueError):
    """The file has no readable logo in it."""


@dataclass(frozen=True)
class Swatch:
    hex: str
    share: float
    lightness: float
    chroma: float
    hue: float

    @property
    def colourful(self) -> bool:
        return self.chroma >= COLOURFUL


@dataclass(frozen=True)
class LogoReading:
    swatches: tuple[Swatch, ...]
    tone: Tone
    background: str | None
    notes: tuple[str, ...]

    @property
    def colourful(self) -> tuple[Swatch, ...]:
        return tuple(s for s in self.swatches if s.colourful)


def _swatch(hex_colour: str, lab: Floats, share: float) -> Swatch:
    lightness, a, b = (float(v) for v in lab)
    return Swatch(
        hex=hex_colour,
        share=round(share, 4),
        lightness=round(lightness, 4),
        chroma=round(math.hypot(a, b), 4),
        hue=round(math.degrees(math.atan2(b, a)) % 360.0, 2),
    )


def _load(data: bytes | Image.Image) -> npt.NDArray[np.uint8]:
    if isinstance(data, Image.Image):
        image = data
    else:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:
            raise LogoError("That file is not an image we can read.") from exc
    rgba = image.convert("RGBA")
    factor = math.ceil(max(rgba.size) / MAX_SIDE)
    if factor > 1:
        rgba = rgba.reduce(factor)
    return np.asarray(rgba, dtype=np.uint8)


def _neighbourhood_range(pixels: npt.NDArray[np.uint8]) -> npt.NDArray[np.int16]:
    """For every pixel, the largest change in any channel across its 3x3 patch."""
    padded = np.pad(pixels.astype(np.int16), ((1, 1), (1, 1), (0, 0)), mode="edge")
    windows = sliding_window_view(padded, (3, 3), axis=(0, 1))
    spread: npt.NDArray[np.int16] = (windows.max(axis=(-2, -1)) - windows.min(axis=(-2, -1))).max(
        axis=-1
    )
    return spread


def _plain_background(pixels: npt.NDArray[np.uint8], lab: Floats) -> Floats | None:
    """The colour of a plain grey, white or black border, if the file has one."""
    height, width = pixels.shape[:2]
    ring = np.zeros((height, width), dtype=bool)
    edge = max(1, min(height, width) // 100)
    ring[:edge, :] = ring[-edge:, :] = ring[:, :edge] = ring[:, -edge:] = True
    border = lab[ring]
    middle = np.median(border, axis=0)
    agree = float((np.linalg.norm(border - middle, axis=1) < BORDER_SAME).mean())
    if agree < BORDER_AGREES or math.hypot(middle[1], middle[2]) >= COLOURFUL:
        return None
    found: Floats = middle
    return found


def _cluster(colours: Floats, weights: Floats) -> tuple[npt.NDArray[np.intp], Floats]:
    """Weighted k-means in OKLab with no randomness.

    Seeds are chosen farthest-first: the most common colour, then repeatedly the
    colour that is most common AND least like any seed so far. Seeding stops when
    everything left is within SAME of a seed, so the number of colours is found, not
    guessed. Then ordinary k-means refines the groups, and any two that drifted
    within SAME of each other are merged.
    """
    seeds = [int(np.argmax(weights))]
    nearest = np.linalg.norm(colours - colours[seeds[0]], axis=1)
    while len(seeds) < MAX_COLOURS:
        score = weights * nearest**2
        pick = int(np.argmax(score))
        if nearest[pick] < SAME:
            break
        seeds.append(pick)
        nearest = np.minimum(nearest, np.linalg.norm(colours - colours[pick], axis=1))
    centres: Floats = colours[seeds].copy()

    labels = np.zeros(len(colours), dtype=np.intp)
    for _ in range(30):
        gaps = np.linalg.norm(colours[:, None, :] - centres[None, :, :], axis=2)
        new = np.argmin(gaps, axis=1)
        for index in range(len(centres)):
            member = new == index
            if member.any():
                centres[index] = np.average(colours[member], axis=0, weights=weights[member])
        if np.array_equal(new, labels):
            break
        labels = new

    while len(centres) > 1:
        gaps = np.linalg.norm(centres[:, None, :] - centres[None, :, :], axis=2)
        np.fill_diagonal(gaps, np.inf)
        first, second = np.unravel_index(int(np.argmin(gaps)), gaps.shape)
        if gaps[first, second] >= SAME:
            break
        keep, drop = min(first, second), max(first, second)
        labels[labels == drop] = keep
        labels[labels > drop] -= 1
        centres = np.delete(centres, drop, axis=0)
        member = labels == keep
        centres[keep] = np.average(colours[member], axis=0, weights=weights[member])
    return labels, centres


def read_logo(data: bytes | Image.Image) -> LogoReading:
    """The colours in a logo, biggest share first, with what was ignored and why."""
    pixels = _load(data)
    rgb = pixels[..., :3]
    alpha = pixels[..., 3]
    lab = rgb_to_oklab(rgb)
    notes: list[str] = []

    transparent = float((alpha < SOLID).mean()) > 0.02
    solid = alpha >= SOLID
    if int(solid.sum()) < 50:
        # Hairline logos can have no fully solid pixel once scaled; half-solid will do.
        solid = alpha >= 128
    background: str | None = None
    if transparent:
        visible = solid
    else:
        plain = _plain_background(pixels, lab)
        if plain is None:
            visible = solid
            notes.append(
                "The logo fills its whole file with colour, so that colour is read as "
                "part of the brand."
            )
        else:
            background = hex_of(oklab_to_rgb(plain))
            visible = solid & (np.linalg.norm(lab - plain, axis=-1) > BACKGROUND_REMOVE)
            notes.append(f"The plain {background} background around the logo was ignored.")
    if int(visible.sum()) < 50:
        raise LogoError("The file is blank or almost entirely transparent.")

    # A pixel is inside a flat patch when it and all 8 neighbours are visible and
    # nearly the same colour. Thin script logos can have almost no such pixels, so
    # the rule loosens rather than reading nothing.
    padded = np.pad(visible, 1, mode="constant", constant_values=False)
    whole = sliding_window_view(padded, (3, 3)).all(axis=(-2, -1))
    spread = _neighbourhood_range(pixels)
    counted = whole & (spread <= FLAT)
    enough = max(40, int(0.1 * visible.sum()))
    if counted.sum() < enough:
        counted = whole & (spread <= FLAT_LOOSE)
    if counted.sum() < enough:
        counted = visible
        notes.append("The logo is mostly thin lines, so its colours are approximate.")

    packed = (
        rgb[counted].astype(np.uint32) @ np.array([1 << 16, 1 << 8, 1], dtype=np.uint32)
    ).astype(np.uint32)
    unique, counts = np.unique(packed, return_counts=True)
    colours_rgb = np.stack([(unique >> 16) & 255, (unique >> 8) & 255, unique & 255], axis=1)
    colours = rgb_to_oklab(colours_rgb)
    weights = counts.astype(np.float64)
    labels, centres = _cluster(colours, weights)

    # Colours are FOUND from flat pixels, but their shares are MEASURED over every
    # visible pixel. Flat pixels undercount thin strokes (a 3px line has almost no
    # flat interior once scaled), so share-by-flat-pixels would depend on file size.
    # Share-by-area does not. Pixels far from every colour - blends at the boundary
    # between two colours - are left out rather than credited to either.
    everywhere = lab[visible]
    gaps = np.linalg.norm(everywhere[:, None, :] - centres[None, :, :], axis=2)
    nearest = np.argmin(gaps, axis=1)
    close = gaps.min(axis=1) < 2 * SAME
    area = np.bincount(nearest[close], minlength=len(centres)).astype(np.float64)
    total = max(float(area.sum()), 1.0)

    found: list[Swatch] = []
    for index, centre in enumerate(centres):
        member = labels == index
        weight = float(weights[member].sum())
        share = float(area[index]) / total
        if share < MIN_SHARE or weight == 0:
            continue
        heaviest = int(np.argmax(np.where(member, weights, -1.0)))
        if weights[heaviest] >= 0.25 * weight:
            # A flat fill: one exact value dominates, so that value is the colour.
            exact = colours_rgb[heaviest].astype(np.uint8)
            found.append(_swatch(hex_of(exact), colours[heaviest], share))
        else:
            found.append(_swatch(hex_of(oklab_to_rgb(centre)), centre, share))
    found.sort(key=lambda s: (-s.share, s.hex))

    colourful = [s for s in found if s.colourful and s.share >= 0.05]
    tone: Tone
    if colourful:
        tone = "colour"
    else:
        mean = sum(s.lightness * s.share for s in found) / max(sum(s.share for s in found), 1e-9)
        tone = "dark" if mean < 0.6 else "light"
    if len(found) >= 6 and found[0].share < 0.3:
        notes.append(
            "The logo has many colours or a gradient. The main ones are listed; check "
            "them against the brand guide if there is one."
        )
    return LogoReading(swatches=tuple(found), tone=tone, background=background, notes=tuple(notes))


__all__ = ["LogoError", "LogoReading", "Swatch", "read_logo"]
