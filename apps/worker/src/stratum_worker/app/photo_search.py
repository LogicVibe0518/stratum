"""The client's photos by what is in them (M17a).

Each photo is read by the image tower once, and the vector is kept with the photo's
row (`assets.quality`), next to the other numbers measured at upload: `embedding`
(packed, `photo/understand.py`), `seen_by` (which model made it), and the photo's
type with its probability when the model has a probe. A vector made by another model
is made again, so changing the model needs no migration and no clean-up.

The vectors are made in the worker, the first time a draft or a tweak needs them,
never in the upload request: the API never loads the model.

A search embeds the designer's description (a few milliseconds), takes one cosine per
photo and SigLIP's probability for the best. At or above the model's line the best
photo is used; below it, the designer is asked, with the closest two to choose from.
A client has tens to hundreds of photos, so a matrix product is all the index needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from stratum_worker.photo import understand
from stratum_worker.photo.understand import PhotoModel, Vector, pack, unpack

if TYPE_CHECKING:
    from stratum_worker.app.service import Context

# A graphic (a flyer, a screenshot, a logo) is never a poster's photo when the model is
# this sure it is one.
GRAPHIC = "graphic"


@dataclass(frozen=True)
class Library:
    """A client's photos as the model sees them, newest first."""

    shas: tuple[str, ...]
    vectors: Vector  # one row per photo
    types: tuple[str | None, ...]


@dataclass(frozen=True)
class Match:
    sha: str
    probability: float


@dataclass(frozen=True)
class Found:
    description: str
    matches: tuple[Match, ...]  # best first
    line: float

    @property
    def sure(self) -> bool:
        return bool(self.matches) and self.matches[0].probability >= self.line


def _known(quality: dict[str, Any], model: PhotoModel) -> bool:
    return quality.get("seen_by") == model.id and isinstance(quality.get("embedding"), str)


def _embed(model: PhotoModel, paths: Sequence[str]) -> Vector:
    images = []
    for path in paths:
        with Image.open(path) as image:
            image.load()
            images.append(image.copy())
    return model.embed_photos(images)


async def library(ctx: Context, client_id: str, model: PhotoModel) -> Library:
    """Every photo of the client with its vector, making the ones not made yet."""
    from stratum_worker.app.service import local_path  # the service imports this module

    rows = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client_id}",
        kind="eq.photo",
        select="id,sha256,quality",
        order="created_at.desc",
    )
    missing = [row for row in rows if not _known(row.get("quality") or {}, model)]
    if missing:
        paths = [await local_path(ctx, row["sha256"]) for row in missing]
        made = await asyncio.to_thread(_embed, model, paths)
        types = model.photo_types(made)
        updates = []
        for row, vector, (kind, sure) in zip(missing, made, types, strict=True):
            quality = {
                **(row.get("quality") or {}),
                "seen_by": model.id,
                "embedding": pack(vector),
                "photo_type": kind,
                "photo_type_p": sure,
            }
            row["quality"] = quality
            updates.append(ctx.supa.update("assets", {"quality": quality}, id=f"eq.{row['id']}"))
        await asyncio.gather(*updates)
    if not rows:
        return Library((), np.zeros((0, 0), dtype=np.float32), ())
    return Library(
        shas=tuple(row["sha256"] for row in rows),
        vectors=np.stack([unpack(row["quality"]["embedding"]) for row in rows]),
        types=tuple(row["quality"].get("photo_type") for row in rows),
    )


def search(model: PhotoModel, photos: Library, description: str, top: int = 2) -> Found:
    if not photos.shas:
        return Found(description, (), model.line)
    cosines = photos.vectors @ model.embed_query(description)
    probabilities = model.probability(cosines)
    order = np.argsort(-cosines, kind="stable")[:top]
    return Found(
        description,
        tuple(Match(photos.shas[i], round(float(probabilities[i]), 4)) for i in order),
        model.line,
    )


async def find(ctx: Context, client_id: str, description: str) -> Found | None:
    """The client's photos closest to a description; None when this build has no model."""
    model = understand.installed()
    if model is None:
        return None
    return search(model, await library(ctx, client_id, model), description)


def summary(photos: Library) -> str:
    """The photos by type, for the chat model: "2 people working, 3 place, 1 unsure"."""
    counts: dict[str, int] = {}
    for kind in photos.types:
        name = (kind or "unsure").replace("_", " ")
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))


__all__ = ["GRAPHIC", "Found", "Library", "Match", "find", "library", "search", "summary"]
