"""A small picture of a poster (render_poster(width=...)): the Taste Lab compares
designs by how they look at a glance, so the small picture must be the same poster."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
from stratum_spec import BrandKit, PosterSpec
from stratum_worker.design.lab import LOOK_WIDTH, apart, cielab
from stratum_worker.render import render_poster

REPO = Path(__file__).resolve().parents[3]


def rgb(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0


def test_a_small_render_is_the_same_poster_shrunk(kit: BrandKit, offer: PosterSpec) -> None:
    full = render_poster(offer, kit, root=REPO)
    small = render_poster(offer, kit, root=REPO, width=LOOK_WIDTH)
    width, height = offer.canvas.size
    assert Image.open(io.BytesIO(small)).size == (LOOK_WIDTH, round(LOOK_WIDTH * height / width))
    with Image.open(io.BytesIO(full)) as image:
        shrunk = image.convert("RGB").resize(
            (LOOK_WIDTH, round(LOOK_WIDTH * height / width)), Image.Resampling.BOX
        )
    shrunk_rgb = np.asarray(shrunk, dtype=np.float64) / 255.0
    assert apart(cielab(rgb(small)), cielab(shrunk_rgb)) < 3.0


def test_without_a_width_the_render_is_full_size(kit: BrandKit, offer: PosterSpec) -> None:
    png = render_poster(offer, kit, root=REPO)
    assert Image.open(io.BytesIO(png)).size == offer.canvas.size
