"""What a photo is made of, measured once: its faces, what stands out, what is busy.

Three maps on the same small grid (about 64 cells on the longer side), each as
fractions of the photo so they fit any crop and any size:

- faces: from YuNet (`faces.py`). The one thing a crop must never cut.
- salience: where the eye goes, summing to 1. No model: colour that differs from
  the photo's edges and from its average, plus a little of the spectral residual
  (Hou and Zhang, 2007), which picks out detail that breaks the pattern around it.
  Photographers put the subject near the middle, so a soft centre weighting too.
  Good at a cake on a table, weaker at a person in front of a busy street - which
  is why faces are not left to it.
- busy: fine texture, 0 (flat sky) to 1 (brickwork, leaves). Words are hardest to
  read over busy parts; the crop tries to put them over calm ones. Measured at about
  512 pixels and then averaged into the grid: measured on the small grid itself,
  brickwork blurs into flat grey and reads as calm.

`strength` (0..1) says how clearly the salience picks one thing out. A flat photo,
or one evenly busy all over, has no subject to protect: its crop is chosen by the
words and the busyness alone. Two things must both hold for a strong subject: it
differs from the photo's edges by a colour difference people notice, and the
salience is gathered in one area rather than scattered.

The subject (M10f): U^2-Net outlines the thing the photo is of (`subject.py`). When
it finds one clear subject, the salience is mostly that outline (SUBJECT_SHARE),
and the strength is at least as sure as the outline is. So the crop, and the
ranking of designs that uses the crop's cost, keep the plumber clear of the words
even in front of a busy street. With no clear subject, nothing changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image

from stratum_worker.brand.oklab import rgb_to_oklab
from stratum_worker.photo.faces import Face, find_faces
from stratum_worker.photo.subject import Subject, find_subject

Grid = npt.NDArray[np.float64]

GRID = 64
# How far the salience spreads from its own centre, in photo widths (root mean
# square). Spread evenly it is 0.41; a disc on a plain ground 0.12; a cup on a
# table 0.28; random noise 0.30. Measured on test scenes and public-domain photos.
SPREAD_EVEN = 0.38
SPREAD_TIGHT = 0.20
# OKLab distance from the edge colours that counts as clearly different (0.02 is
# about the smallest difference most people notice).
CLEARLY_DIFFERENT = 0.08
# Texture is measured with the photo at this size (longer side, pixels) ...
TEXTURE_SIDE = 512
# ... and a mean luminance gradient of this much per pixel counts as fully busy:
# fine brickwork or leaves score about 0.1-0.2, a clear sky under 0.01.
FULLY_BUSY = 0.08
# With one clear subject, this much of the salience is the subject's outline; the
# rest is the colour-and-detail salience, so a bright sign beside it still counts.
SUBJECT_SHARE = 0.75


@dataclass(frozen=True)
class PhotoRead:
    """One photo, measured. Everything in fractions of the photo, grids row-major."""

    size: tuple[int, int]
    faces: tuple[Face, ...]
    salience: Grid = field(repr=False)
    busy: Grid = field(repr=False)
    strength: float
    subject: Subject | None = field(default=None, repr=False)


def edge_map(image: npt.NDArray[np.uint8], *, size: int = 96) -> Grid:
    """Edge density of a photo, 0..1, on a small grid: how busy each part of it is.

    Gradient magnitude of the luminance, normalised by a high percentile so one
    bright edge does not flatten the rest, then box-blurred so a text line samples
    the neighbourhood it sits in, not single pixels.
    """
    grey = Image.fromarray(image).convert("L")
    grey.thumbnail((size, size))
    lum = np.asarray(grey, dtype=np.float64) / 255.0
    gy, gx = np.gradient(lum)
    magnitude = np.hypot(gx, gy)
    scale = float(np.percentile(magnitude, 99)) or 1.0
    norm: Grid = np.clip(magnitude / scale, 0.0, 1.0)
    padded = np.pad(norm, 2, mode="edge")
    out: Grid = np.zeros_like(norm)
    for dy in range(5):
        for dx in range(5):
            out += padded[dy : dy + norm.shape[0], dx : dx + norm.shape[1]] / 25.0
    return out


def texture(image: Image.Image, shape: tuple[int, int]) -> Grid:
    """Fine texture of a photo, 0 (flat) to 1 (busy), averaged into a grid of `shape`."""
    grey = image.convert("L")
    grey.thumbnail((TEXTURE_SIDE, TEXTURE_SIDE), Image.Resampling.BOX)
    lum = np.asarray(grey, dtype=np.float64) / 255.0
    gy, gx = np.gradient(lum)
    magnitude = np.hypot(gx, gy).astype(np.float32)
    rows, cols = shape
    pooled = Image.fromarray(magnitude, mode="F").resize((cols, rows), Image.Resampling.BOX)
    out: Grid = np.clip(np.asarray(pooled, dtype=np.float64) / FULLY_BUSY, 0.0, 1.0)
    return out


def _box_blur(grid: Grid, radius: int) -> Grid:
    """Mean over a (2r+1)^2 square, edges repeated. Three passes approximate a Gaussian."""
    size = 2 * radius + 1
    padded = np.pad(grid, radius, mode="edge")
    table = np.pad(np.cumsum(np.cumsum(padded, 0), 1), ((1, 0), (1, 0)))
    rows, cols = grid.shape
    out: Grid = (
        table[size : size + rows, size : size + cols]
        - table[:rows, size : size + cols]
        - table[size : size + rows, :cols]
        + table[:rows, :cols]
    ) / (size * size)
    return out


def _smooth(grid: Grid, radius: int) -> Grid:
    for _ in range(3):
        grid = _box_blur(grid, radius)
    return grid


def _spread(grid: Grid) -> Grid:
    low, high = float(grid.min()), float(grid.max())
    out: Grid = (grid - low) / (high - low) if high > low else np.zeros_like(grid)
    return out


def _edge_colours(lab: Grid, count: int = 4) -> Grid:
    """A few colours that stand for the photo's border: usually the background."""
    border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    # Deterministic start: evenly spaced along the border, then a few k-means steps.
    centres = border[np.linspace(0, len(border) - 1, count).astype(int)].copy()
    for _ in range(8):
        nearest = np.linalg.norm(border[:, None] - centres[None], axis=-1).argmin(1)
        for index in range(count):
            members = border[nearest == index]
            if len(members):
                centres[index] = members.mean(0)
    out: Grid = centres
    return out


def _spectral_residual(lum: Grid) -> Grid:
    spectrum = np.fft.fft2(lum)
    log_amplitude = np.log(np.abs(spectrum) + 1e-9)
    residual = log_amplitude - _box_blur(log_amplitude, 1)
    back = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
    return _smooth(back, 1)


def _distinctness(image: Image.Image) -> tuple[Grid, float]:
    """Raw salience before any weighting, and how different the most different part
    of the photo is from its edges (OKLab distance, 95th percentile)."""
    small = image.convert("RGB")
    small.thumbnail((GRID, GRID), Image.Resampling.BOX)
    lab = rgb_to_oklab(np.asarray(small, dtype=np.uint8))
    smooth = np.stack([_smooth(lab[..., channel], 1) for channel in range(3)], -1)
    from_mean = np.linalg.norm(smooth - lab.reshape(-1, 3).mean(0), axis=-1)
    edges = _edge_colours(lab)
    from_edges = np.linalg.norm(smooth[..., None, :] - edges[None, None], axis=-1).min(-1)
    detail = _spectral_residual(lab[..., 0])
    raw = 0.35 * _spread(from_mean) + 0.45 * _spread(from_edges) + 0.2 * _spread(detail)
    return raw, float(np.percentile(from_edges, 95))


def salience(image: Image.Image) -> Grid:
    """Where the eye goes, on a grid of about GRID cells on the longer side; sums to 1."""
    raw, _ = _distinctness(image)
    return _weighted(raw)


def _weighted(raw: Grid) -> Grid:
    rows, cols = raw.shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    centre = np.exp(-(((xx + 0.5) / cols - 0.5) ** 2 + ((yy + 0.5) / rows - 0.5) ** 2) / 0.405)
    weighted = _smooth(raw * (0.5 + 0.5 * centre), 1)
    # Only what stands out above the photo's own average counts as a subject.
    above = np.clip(weighted - weighted.mean(), 0.0, None)
    total = float(above.sum())
    out: Grid = above / total if total > 0 else np.full_like(above, 1.0 / above.size)
    return out


def spread(sal: Grid) -> float:
    """Root-mean-square distance of the salience from its own centre, in photo widths."""
    rows, cols = sal.shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    x, y = (xx + 0.5) / cols, (yy + 0.5) / rows
    mean_x, mean_y = float((sal * x).sum()), float((sal * y).sum())
    return float(np.sqrt((sal * (x - mean_x) ** 2).sum() + (sal * (y - mean_y) ** 2).sum()))


def strength(sal: Grid, difference: float) -> float:
    """How clearly one thing stands out, 0..1: gathered in one place AND different."""
    gathered = (SPREAD_EVEN - spread(sal)) / (SPREAD_EVEN - SPREAD_TIGHT)
    different = difference / CLEARLY_DIFFERENT
    return float(np.clip(gathered, 0.0, 1.0) * np.clip(different, 0.0, 1.0))


def with_subject(sal: Grid, sure: float, subject: Subject) -> tuple[Grid, float]:
    """Salience and strength once the subject's outline is known (see the module doc)."""
    if not subject.clear:
        return sal, sure
    outline = subject.on(sal.shape)
    total = float(outline.sum())
    if total <= 0:
        return sal, sure
    mixed: Grid = SUBJECT_SHARE * outline / total + (1.0 - SUBJECT_SHARE) * sal
    return mixed, max(sure, float(np.clip(subject.separation, 0.0, 1.0)))


def read_image(image: Image.Image) -> PhotoRead:
    """Measure one photo. Pure: the same pixels always give the same read."""
    rgb = image.convert("RGB")
    raw, difference = _distinctness(rgb)
    sal = _weighted(raw)
    subject = find_subject(rgb)
    sal, sure = with_subject(sal, strength(sal, difference), subject)
    busy = texture(rgb, sal.shape)
    return PhotoRead(
        size=rgb.size,
        faces=find_faces(rgb),
        salience=sal,
        busy=busy,
        strength=sure,
        subject=subject,
    )


@lru_cache(maxsize=64)
def _read_cached(path: str, mtime_ns: int, length: int) -> PhotoRead:
    with Image.open(path) as image:
        return read_image(image)


def read_photo(path: str | Path) -> PhotoRead:
    """Measure the photo at `path`, once per file: asset files are named by content,
    so the same path, size and time always hold the same pixels."""
    stat = Path(path).stat()
    return _read_cached(str(path), stat.st_mtime_ns, stat.st_size)


__all__ = [
    "GRID",
    "PhotoRead",
    "edge_map",
    "read_image",
    "read_photo",
    "salience",
    "spread",
    "texture",
    "with_subject",
]
