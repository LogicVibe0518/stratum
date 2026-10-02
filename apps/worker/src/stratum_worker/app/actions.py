"""The tweak ops the service carries out itself (M10c): restyle and undo.

Restyle lays the post out again as a neighbouring design around the words now on the
poster (with any tweaks to them), the same photos and the same logo, then frames the
photos for the new layout. Undo makes the version before the last one the poster
again. Both write a new version: nothing is ever overwritten.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any

from PIL import Image
from stratum_spec import BrandKit, PosterSpec
from stratum_spec import load as load_spec
from stratum_spec.models import ImageLayer, LogoLayer, TextLayer

from stratum_worker.app import service
from stratum_worker.design import decode, is_design
from stratum_worker.design.choose import Material, fill
from stratum_worker.draft.bindings import binding_for
from stratum_worker.edit.restyle import RestyleError, carry_over, said, targets
from stratum_worker.photo.frame import frame_photos

# How many neighbouring designs to try before saying the words do not fit any.
MAX_TRIES = 24


async def photo_sizes(asset_paths: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Each file's pixel size, read from its header only."""

    def size(path: str) -> tuple[int, int]:
        with Image.open(path) as image:
            return image.size

    shas = list(asset_paths)
    sizes = await asyncio.gather(*(asyncio.to_thread(size, asset_paths[sha]) for sha in shas))
    return dict(zip(shas, sizes, strict=True))


async def client_photos(ctx: service.Context, client: dict[str, Any]) -> list[str]:
    """The client's usable photos, best first (the order a draft would use them)."""
    picked, _ = await service.pick_photos(ctx, client["id"], {f"p{i}": "photo" for i in range(50)})
    return list(picked.values())


@dataclass(frozen=True)
class Restyled:
    spec: PosterSpec
    design_id: str
    note: str
    added_photo: bool


def _words(spec: PosterSpec, template_id: str, post: dict[str, Any]) -> dict[str, str]:
    """The words as they are on the poster now - a tweaked headline stays tweaked."""
    words = {key: str(value) for key, value in (post.get("words") or {}).items()}
    for layer_id, field_name in binding_for(template_id).texts.items():
        try:
            layer = spec.layer(layer_id)
        except KeyError:
            continue
        if isinstance(layer, TextLayer):
            words[field_name] = layer.content
    return words


async def restyle(
    ctx: service.Context,
    post: dict[str, Any],
    client: dict[str, Any],
    spec: PosterSpec,
    kit: BrandKit,
    changes: dict[str, str],
    photos: list[str],
) -> Restyled:
    """The same post as the neighbouring design the changes ask for.

    `photos` is the client's usable photos, best first. Raises RestyleError with a
    sentence for the designer when it cannot be done.
    """
    template_id = post["template_id"]
    ids = targets(template_id, changes)
    shown = [
        layer.asset
        for layer in spec.layers
        if isinstance(layer, ImageLayer) and layer.asset is not None
    ]
    real = [sha for sha in shown if sha in photos]
    generated = next((sha for sha in shown if sha not in photos), None)
    family = decode(ids[0]).family
    wanted = list(family.photos.values()).count("photo")
    extra = [sha for sha in photos if sha not in real]
    if wanted > len(real) + len(extra):
        raise RestyleError(
            f'The "{family.title}" design needs {wanted} of {client["name"]}\'s own photos '
            f"and there {'is' if len(photos) == 1 else 'are'} {len(photos)}. Upload "
            f"another, then ask again."
        )
    chosen = (real + extra)[: max(wanted, len(real))]
    logo = next(
        (layer.asset for layer in spec.layers if isinstance(layer, LogoLayer) and layer.asset),
        None,
    )
    if logo is None:
        raise RestyleError("This poster has no logo to carry over, so it cannot be restyled.")
    rows = await ctx.supa.select("assets", sha256=f"eq.{logo}", select="width,height", limit="1")
    width, height = (rows[0].get("width"), rows[0].get("height")) if rows else (0, 0)
    material = Material(
        words=_words(spec, template_id, post),
        kit=kit,
        photos=tuple(chosen),
        background=generated,
        logo=logo,
        logo_aspect=(width / height) if width and height else 0.0,
        ink=await service.logo_ink(ctx, logo),
        reads={},
    )
    root = ctx.settings.repo_root
    aspects = post["aspects"]
    for design_id in ids[:MAX_TRIES]:
        laid = await asyncio.to_thread(fill, design_id, material, aspects, root)
        if laid is not None:
            break
    else:
        raise RestyleError(
            "The words do not fit that style in every size. Shorten the headline or the "
            "offer, or try another style."
        )
    # The old design laid out fresh around the same words and photos: what differs
    # between it and the poster is what the person changed, and is carried over.
    as_drafted = replace(material, photos=tuple(real))
    pristine = await asyncio.to_thread(fill, template_id, as_drafted, aspects, root)
    asset_paths = {
        sha: await service.local_path(ctx, sha)
        for sha in {*chosen, logo, *([generated] if generated else [])}
    }
    reads = await service.photo_reads(laid, asset_paths)
    framed = frame_photos(laid, reads, kit, root, aspects)
    carried = carry_over(spec, pristine, framed, kit, changes)
    notes = [said(template_id, design_id, changes, kit)]
    if design_id != ids[0]:
        notes.append("That is the closest style the words fit.")
    if carried.kept:
        notes.append(f"Kept {_listed(carried.kept)}.")
    notes.extend(carried.dropped)
    return Restyled(
        spec=carried.spec,
        design_id=design_id,
        note=" ".join(notes),
        added_photo=len(chosen) > len(real),
    )


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


async def undo(
    ctx: service.Context,
    post: dict[str, Any],
    client: dict[str, Any],
    message: str,
    *,
    everything: bool = False,
) -> dict[str, Any]:
    """Make the version before the latest - or, with `everything`, the first draft - the
    poster again, as a new version. Nothing is ever deleted: undo is a step forward."""
    started = time.perf_counter()
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="2"
    )
    if len(versions) < 2:
        return {
            "outcome": "needs_human",
            "why": "There is nothing to undo yet: this is the first version.",
        }
    latest, previous = versions
    if everything:
        # Live, 1 Oct: "undo all changes" went back one step. All means the first draft.
        [previous] = await ctx.supa.select(
            "post_versions", post_id=f"eq.{post['id']}", order="version.asc", limit="1"
        )
    spec = load_spec(previous["spec"])
    bound = spec.assets()
    asset_paths = {s: await service.local_path(ctx, s) for s in bound if isinstance(s, str)}
    kit = await service.kit_for(ctx, client, spec.brand_kit_version)
    template_id = spec.template_id if is_design(spec.template_id) else post["template_id"]
    as_it_was = {**post, "template_id": template_id}
    renders, flags = await service.render_versions(
        ctx, as_it_was, spec, service.file_variants(ctx, template_id), asset_paths, kit
    )
    flags = [*flags, *service.brief_flags(as_it_was, spec)]
    saved = await service.save_version(
        ctx,
        post,
        spec,
        renders,
        flags,
        note=f"undo: back to version {previous['version']}",
        after=latest["version"],
    )
    if template_id != post["template_id"]:
        await ctx.supa.update(
            "posts", {"template_id": template_id, "updated_at": "now()"}, id=f"eq.{post['id']}"
        )
    await ctx.supa.insert(
        "edits",
        {
            "agency_id": ctx.agency_id,
            "post_id": post["id"],
            "from_version": latest["version"],
            "message": message,
            "ops": [{"op": "undo", "all": everything}],
            "outcome": "applied",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "cost_usd": 0,
            "created_by": ctx.user_id,
        },
    )
    version: dict[str, Any] = saved
    version["outcome"] = "applied"
    version["note"] = (
        f"Back to the first draft (version {previous['version']})."
        if everything
        else f"Back to version {previous['version']}."
    )
    version["why"] = version["note"]
    version["seconds"] = round(time.perf_counter() - started, 2)
    return version


__all__ = ["MAX_TRIES", "Restyled", "client_photos", "photo_sizes", "restyle", "undo"]
