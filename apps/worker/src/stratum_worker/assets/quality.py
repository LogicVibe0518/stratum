"""Cheap, deterministic photo checks. Flags are advice for a human, never a rejection.

Thresholds are starting points chosen on synthetic images (no real client photos are
allowed in the repo). Recalibrate them on the benchmark photo set before trusting them.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from stratum_worker.assets.models import Flag

# Variance of the 4-neighbour Laplacian (OpenCV's ksize=1 kernel) on 0-255 greyscale,
# measured at web size so the score does not depend on the camera's resolution.
# 100 is the widely used rule of thumb for this exact kernel.
MIN_SHARPNESS = 100.0

# It is read from the sharpest part of the photo, not the whole frame (I-16). The photo
# is cut into a GRID x GRID grid and the score is the SHARPEST_PART percentile of the
# tiles' variances: about the 4th-sharpest of 36 tiles, so a subject covering a tenth
# of the frame counts, while one noisy tile does not. Averaged over the whole frame, a
# sharp face on a soft, out-of-focus background (a normal portrait) was called blurry
# and drafts used it last, saying "ask for a sharper one".
# Checked 13 Oct 2026 on the 24 CC0 starter photos (960 px, kept outside the repo) and
# copies blurred with Pillow's GaussianBlur: every original scores over 130 (the
# whole-frame score flagged 4 of them: 48, 67, 74, 99); radius 1 scores 19-190 (17 of
# 24 flagged); radius 2 scores 22 or less and radius 3 scores 7 or less (all flagged).
GRID = 6
SHARPEST_PART = 90.0

# Mean Rec.601 luma, 0-255. Well-exposed photos sit around 100-130; dim but usable
# indoor shots around 50-90. Below 40 there is so little signal that brightening
# mostly amplifies noise, so a human should ask the client for another photo.
MIN_BRIGHTNESS = 40.0

# Half of our 1080px canvas width: below this even a half-width inset is upscaled.
MIN_DIMENSION_PX = 540

WHITE = (255, 255, 255)


def _on_white(image: Image.Image) -> Image.Image:
    if image.mode != "RGBA":
        return image
    flat = Image.new("RGB", image.size, WHITE)
    flat.paste(image, mask=image.getchannel("A"))
    return flat


def laplacian_variance(grey: NDArray[np.float64]) -> float:
    if grey.shape[0] < 3 or grey.shape[1] < 3:
        return 0.0
    edges = (
        grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:] - 4.0 * grey[1:-1, 1:-1]
    )
    return float(edges.var())


def sharpness(grey: NDArray[np.float64]) -> float:
    """How sharp the sharpest part of a photo is (see SHARPEST_PART)."""
    height, width = grey.shape
    tiles = [
        laplacian_variance(
            grey[
                row * height // GRID : (row + 1) * height // GRID,
                col * width // GRID : (col + 1) * width // GRID,
            ]
        )
        for row in range(GRID)
        for col in range(GRID)
    ]
    return float(np.percentile(tiles, SHARPEST_PART))


def measure(preview: Image.Image, full_size: tuple[int, int]) -> dict[str, float]:
    """Score `preview` (the web variant) and record the full image's smaller side."""
    grey = np.asarray(_on_white(preview).convert("L"), dtype=np.float64)
    return {
        "sharpness": round(sharpness(grey), 3),
        "brightness": round(float(grey.mean()), 3),
        "min_dimension": float(min(full_size)),
    }


def flags_for(quality: dict[str, float]) -> list[Flag]:
    flags: list[Flag] = []
    if quality["sharpness"] < MIN_SHARPNESS:
        flags.append("too_blurry")
    if quality["min_dimension"] < MIN_DIMENSION_PX:
        flags.append("too_small")
    if quality["brightness"] < MIN_BRIGHTNESS:
        flags.append("too_dark")
    return flags
