"""The top three in the product (M12b): draft three options, list them, pick one.

A post whose design is `best_three` is drafted by laying out many designs around its
words and photos and keeping three different ones (design/choose.py). Version 1 is
the ranking's best, so everything that shows "the poster" keeps working; the three
options are stored beside it (stratum.post_options) and the designer can switch to
any of them. Picking writes the next version and a designer_pick row in the
decision log saying what was shown, where, and how likely each option was to be
shown - the label taste is learned from later (M15).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import numpy as np
from PIL import Image
from stratum_spec import load as load_spec

from stratum_worker.app import service
from stratum_worker.critic.ranker import model_name
from stratum_worker.design import decode, repair
from stratum_worker.design.choose import AUTO, Material, NothingFits, candidates, choose, seed_for
from stratum_worker.draft.models import Flag
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.draft.preflight import check_copy
from stratum_worker.photo.read import edge_map, read_photo

# The copy is written once, before any design is chosen, to the character budget of
# this design: an overlay, whose words have the least room of the one-photo families.
# A design the words then do not fit is simply not a candidate.
REFERENCE = "ds.overlay.1.000000"
NAMES = ("A", "B", "C")


def title_of(design_id: str) -> str:
    try:
        return decode(design_id).title
    except KeyError:
        return design_id


def _edges(path: str) -> np.ndarray:
    with Image.open(path) as image:
        return edge_map(np.asarray(image.convert("RGB"), dtype=np.uint8))


async def material_for(
    ctx: service.Context,
    client: dict[str, Any],
    words: dict[str, Any],
    kit: Any,
    photos: tuple[str, ...],
    background: str | None,
) -> tuple[Material, dict[str, str]]:
    """Everything a design is filled with, read once: the newest logo, each photo
    measured (faces, salience, busyness), local files. Shared by the top three and the
    Taste Lab, so both lay designs out around exactly the same material."""
    logos = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client['id']}",
        kind="eq.logo",
        select="sha256,width,height",
        order="created_at.desc",
        limit="1",
    )
    if not logos:
        raise DraftError(
            f"{client['name']} has no logo yet. Upload one - we will not invent branding."
        )
    logo = str(logos[0]["sha256"])
    width, height = logos[0].get("width"), logos[0].get("height")

    shown = [*photos, *([background] if background else [])]
    asset_paths = {sha: await service.local_path(ctx, sha) for sha in {*shown, logo}}
    reads = {sha: await asyncio.to_thread(read_photo, asset_paths[sha]) for sha in shown}
    edges = {sha: await asyncio.to_thread(_edges, asset_paths[sha]) for sha in shown}
    material = Material(
        words={key: str(value) for key, value in words.items() if isinstance(value, str)},
        kit=kit,
        photos=photos,
        background=background,
        logo=logo,
        logo_aspect=(width / height) if width and height else 0.0,
        ink=await service.logo_ink(ctx, logo),
        reads=reads,
        edges=edges,
    )
    return material, asset_paths


async def draft_options(
    ctx: service.Context, post: dict[str, Any], client: dict[str, Any]
) -> dict[str, Any]:
    """Steps 1-7 for a post that lets Stratum choose: one version and three options."""
    started = time.perf_counter()
    root = ctx.settings.repo_root
    kit = service.brand_of(client)
    reference = service.template_for(ctx, REFERENCE, kit)
    as_reference = {**post, "template_id": REFERENCE}
    words, steps = await service.write_copy(ctx, client, as_reference, reference)

    picked, flags = await service.pick_photos(ctx, client["id"], {"a": "photo", "b": "photo"})
    photos = tuple(picked[key] for key in ("a", "b") if key in picked)
    background: str | None = None
    if not photos:
        background, more = await service.make_background(ctx, client, post)
        steps += more
        flags.append(
            Flag(
                code="generated_background",
                message=(
                    "No usable photo, so the background was generated. Swap in a "
                    "real one when they send it."
                ),
            )
        )

    material, asset_paths = await material_for(
        ctx, client, words.model_dump(mode="json"), kit, photos, background
    )
    seed = seed_for(str(post["id"]))
    try:
        ranked = await asyncio.to_thread(candidates, material, post["aspects"], root, seed=seed)
    except NothingFits as exc:
        raise DraftError(str(exc)) from exc
    # The quality bar (M15c): designs good enough to send go first; if none is, the
    # repair steps run and, failing those, the post is flagged for a closer look.
    outcome = await asyncio.to_thread(
        repair.improve, material, post["aspects"], root, seed=seed, ranked=ranked
    )
    ranked = outcome.ranked
    if outcome.flag is not None:
        flags.append(outcome.flag)
    options = choose(ranked, seed=seed)
    specs = [await service.with_mattes(ctx, o.candidate.spec, asset_paths) for o in options]

    batch = str(uuid.uuid4())
    rows = await ctx.supa.insert(
        "post_options",
        [
            {
                "agency_id": ctx.agency_id,
                "post_id": post["id"],
                "batch": batch,
                "slot": option.slot,
                "position": option.position,
                "design_id": option.candidate.design_id,
                "spec": spec.model_dump(mode="json"),
                "score": round(option.candidate.score, 6),
                "propensity": option.propensity,
                "explored": option.explored,
                "detail": {
                    **option.candidate.parts,
                    "family": option.candidate.family,
                    "candidates": len(ranked),
                    "critic_model": model_name(),
                    **({"repairs": [one.step for one in outcome.tried]} if outcome.tried else {}),
                },
            }
            for option, spec in zip(options, specs, strict=True)
        ],
    )

    best, spec = next((o, one) for o, one in zip(options, specs, strict=True) if o.slot == 1)
    chosen = {**post, "template_id": best.candidate.design_id}
    renders, layout_flags = await service.render_versions(ctx, chosen, spec, {}, asset_paths, kit)
    flags.extend(layout_flags)
    flags.extend(check_copy(words, service.rules_of(client), post["brief"]))
    flags.extend(service.brief_flags({**chosen, "words": words.model_dump(mode="json")}, spec))
    version = await service.save_version(
        ctx,
        post,
        spec,
        renders,
        flags,
        note=f"first draft: the best of three ({title_of(best.candidate.design_id)})",
    )
    cost = sum(float(step.get("cost_usd", 0)) for step in steps)
    for step in steps:
        await ctx.supa.insert("steps", {"agency_id": ctx.agency_id, "post_id": post["id"], **step})
    await ctx.supa.update(
        "posts",
        {
            "words": words.model_dump(mode="json"),
            "cost_usd": cost,
            # From here on the post IS this design: tweaks, quick changes and the
            # brief check read its bindings.
            "template_id": best.candidate.design_id,
            "updated_at": "now()",
        },
        id=f"eq.{post['id']}",
    )
    version["seconds"] = round(time.perf_counter() - started, 2)
    version["options"] = [summary(row) for row in sorted(rows, key=lambda row: row["position"])]
    return version


def summary(row: dict[str, Any]) -> dict[str, Any]:
    """What the page needs to show one option."""
    return {
        "id": row["id"],
        "name": NAMES[int(row["position"])],
        "position": row["position"],
        "design_id": row["design_id"],
        "title": title_of(str(row["design_id"])),
        "preview": f"/previews/options/{row['id']}/{{shape}}.webp",
    }


async def latest_options(ctx: service.Context, post_id: str) -> list[dict[str, Any]]:
    """The newest set of options for a post, in the order they are shown."""
    newest = await ctx.supa.select(
        "post_options", post_id=f"eq.{post_id}", select="batch", order="created_at.desc", limit="1"
    )
    if not newest:
        return []
    rows = await ctx.supa.select(
        "post_options",
        post_id=f"eq.{post_id}",
        batch=f"eq.{newest[0]['batch']}",
        select="id,batch,slot,position,design_id,score,propensity,explored",
        order="position.asc",
    )
    return [summary(row) for row in rows]


async def pick_option(
    ctx: service.Context, post: dict[str, Any], client: dict[str, Any], option_id: str
) -> dict[str, Any]:
    """The designer chose this option: make it the poster, and record the choice."""
    rows = await ctx.supa.select(
        "post_options", id=f"eq.{option_id}", post_id=f"eq.{post['id']}", select="*"
    )
    if not rows:
        raise DraftError("That option does not belong to this post.")
    option = rows[0]
    shown = await ctx.supa.select(
        "post_options",
        batch=f"eq.{option['batch']}",
        select="id,design_id,slot,position,propensity,explored,score",
        order="position.asc",
    )
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    spec = load_spec(option["spec"])
    same = bool(versions) and load_spec(versions[0]["spec"]) == spec
    if same:
        # Picking what is already the poster is still a choice worth recording, but
        # it is not a new version.
        version = versions[0]
    else:
        bound = spec.assets()
        asset_paths = {s: await service.local_path(ctx, s) for s in bound if isinstance(s, str)}
        kit = await service.kit_for(ctx, client, spec.brand_kit_version)
        chosen = {**post, "template_id": option["design_id"]}
        renders, flags = await service.render_versions(ctx, chosen, spec, {}, asset_paths, kit)
        flags = [*flags, *service.brief_flags(chosen, spec)]
        name = NAMES[int(option["position"])]
        version = await service.save_version(
            ctx,
            post,
            spec,
            renders,
            flags,
            note=f"picked option {name} ({title_of(str(option['design_id']))})",
            after=versions[0]["version"] if versions else None,
        )
    await ctx.supa.update(
        "posts",
        {"template_id": option["design_id"], "updated_at": "now()"},
        id=f"eq.{post['id']}",
    )
    # The same pick sent twice (a double click, a retried request) is one decision.
    already = await ctx.supa.select(
        "decision_log",
        post_id=f"eq.{post['id']}",
        version_id=f"eq.{version['id']}",
        kind="eq.designer_pick",
        select="id",
        limit="1",
    )
    if already:
        return version
    await ctx.supa.insert(
        "decision_log",
        {
            "agency_id": ctx.agency_id,
            "client_id": client["id"],
            "post_id": post["id"],
            "version_id": version["id"],
            "kind": "designer_pick",
            "actor": ctx.user_id,
            "detail": {
                "what": "top_three",
                "batch": option["batch"],
                "picked": option["id"],
                "design_id": option["design_id"],
                "slot": option["slot"],
                "position": option["position"],
                "shown": shown,
            },
        },
    )
    return version


__all__ = [
    "AUTO",
    "REFERENCE",
    "draft_options",
    "latest_options",
    "material_for",
    "pick_option",
    "summary",
]
