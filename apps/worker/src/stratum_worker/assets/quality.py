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
# 100 is the widely used rule of thumb for this exact kernel. On our synthetic scene a
# sharp image scores ~390, a 1px Gaussian blur ~25 and a 12px blur ~2.4.
MIN_SHARPNESS = 100.0

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


def measure(preview: Image.Image, full_size: tuple[int, int]) -> dict[str, float]:
    """Score `preview` (the web variant) and record the full image's smaller side."""
    grey = np.asarray(_on_white(preview).convert("L"), dtype=np.float64)
    return {
        "sharpness": round(laplacian_variance(grey), 3),
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
