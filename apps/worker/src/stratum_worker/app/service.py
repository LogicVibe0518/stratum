"""The work itself: brief in, poster out, everything recorded.

This is the same seven steps as the command line pipeline, but writing to Postgres
and object storage instead of a folder. The pure parts - build_spec, retarget,
render_poster, preflight - are imported unchanged, which is the payoff for having
kept them free of I/O: the web app and the CLI cannot drift apart, because there is
only one implementation of the thing that matters.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from stratum_spec import BrandKit, embed, load_template, load_variants, retarget
from stratum_spec import load as load_spec
from stratum_spec.client import ClientRules
from stratum_spec.models import Aspect, ImageLayer, LogoLayer, PosterSpec, TextLayer
from stratum_spec.ops import EditOp, OpError, SetColor, apply_ops
from stratum_spec.templates import Variants

from stratum_worker.app.settings import Settings
from stratum_worker.app.supa import Supa
from stratum_worker.assets import AssetNotFound, AssetStore
from stratum_worker.assets.models import Flag as AssetFlag
from stratum_worker.brand import (
    Check,
    LogoError,
    Suggestion,
    check_palette,
    missing_tokens,
    read_logo,
    starter_kit,
    suggest,
)
from stratum_worker.brand.names import WORDS, colour_name, nearest
from stratum_worker.design import PLACEHOLDER, Copy, decode, is_design, lay_out
from stratum_worker.design.choose import AUTO
from stratum_worker.design.copyfit import best_copy, versions
from stratum_worker.draft import copycheck
from stratum_worker.draft.bindings import binding_for
from stratum_worker.draft.capacity import measure
from stratum_worker.draft.compose import ComposeError, build_spec
from stratum_worker.draft.logo_fit import LogoInk, fit_logo
from stratum_worker.draft.models import Flag, PostCopy
from stratum_worker.draft.pipeline import CollectingTracer, DraftError
from stratum_worker.draft.preflight import (
    check_brief_on_poster,
    check_claims,
    check_copy,
    check_layout,
)
from stratum_worker.edit.actions import Ask, Choice, Restyle, Undo, split, undo_asked
from stratum_worker.edit.context import (
    asked_colours,
    colour_lines,
    describe_layers,
    friendly,
    recent_lines,
    style_menu,
)
from stratum_worker.edit.restyle import RestyleError
from stratum_worker.gateway.errors import (
    InvalidProviderOutput,
    NoProviderAvailable,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.tasks import (
    BackgroundRequest,
    EditParseRequest,
    EditParseResult,
    PostCopyRequest,
    PostCopyResult,
    TaskName,
)
from stratum_worker.photo.check import fitness, upload_check
from stratum_worker.photo.frame import face_flags, frame_photos
from stratum_worker.photo.read import PhotoRead, read_photo
from stratum_worker.photo.subject import matte

OPS = TypeAdapter(list[EditOp])
HEX = re.compile(r"#[0-9a-f]{6}")
# Wide enough that a poster still looks right on a laptop, small enough that four of
# them cross the network in the time one full-size PNG used to.
PREVIEW_WIDTH = 720
PREVIEW_QUALITY = 82


def preview_path(settings: Settings, version_id: str, aspect: str) -> Path:
    shape = aspect.replace(":", "x")
    return settings.asset_cache.parent / "previews" / version_id[:2] / f"{version_id}-{shape}.webp"


def cache_preview(ctx: Context, version_id: str, aspect: str, data: bytes) -> None:
    path = preview_path(ctx.settings, version_id, aspect)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def render_preview(
    ctx: Context, version: dict[str, Any], post: dict[str, Any], client: dict[str, Any], aspect: str
) -> bytes:
    """The picture for one version at one aspect, made now if it is not on disk.

    Cheap enough to do on demand - about forty milliseconds - and the result never
    changes, because the version it came from never changes.
    """
    from stratum_worker.render import render_poster

    path = preview_path(ctx.settings, version["id"], aspect)
    if path.is_file():
        return path.read_bytes()
    spec = load_spec(version["spec"])
    bound = spec.assets()
    asset_paths = {s: await local_path(ctx, s) for s in bound if isinstance(s, str)}
    one = retarget(spec, aspect, file_variants(ctx, post["template_id"]))
    kit = await kit_for(ctx, client, spec.brand_kit_version)
    png = await asyncio.to_thread(
        render_poster, one, kit, assets=asset_paths, root=ctx.settings.repo_root
    )
    data = preview(png)
    cache_preview(ctx, version["id"], aspect, data)
    return data


def preview(png: bytes) -> bytes:
    """A screen-sized WebP of a poster.

    Posters are mostly photograph, which PNG stores badly - about 1.2 MB each, and
    four of those on every tweak is most of what the wait was. WebP at this size is
    nearer 60 KB and indistinguishable at the size it is displayed.
    """
    from PIL import Image

    with Image.open(io.BytesIO(png)) as opened:
        flat = opened.convert("RGB")
    if flat.width > PREVIEW_WIDTH:
        height = round(flat.height * PREVIEW_WIDTH / flat.width)
        flat = flat.resize((PREVIEW_WIDTH, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    flat.save(out, "WEBP", quality=PREVIEW_QUALITY, method=4)
    return out.getvalue()


UNUSABLE = frozenset({"too_blurry", "too_dark", "too_small"})


@dataclass
class Context:
    supa: Supa
    settings: Settings
    gateway: Gateway
    agency_id: str
    user_id: str | None = None

    @property
    def store(self) -> AssetStore:
        return AssetStore(self.settings.asset_cache)


def brand_of(row: dict[str, Any]) -> BrandKit:
    """The client's brand kit as it is NOW - for new drafts only."""
    return BrandKit.model_validate(row["brand"])


async def kit_for(ctx: Context, client: dict[str, Any], version: int) -> BrandKit:
    """The brand kit a poster was made with, whatever the client's kit is today.

    Every version of a kit is kept (the brand_kits table, written by the database),
    and a poster names the version it used. Drawing an old poster with today's kit
    would quietly recolour something the client already approved.
    """
    current = brand_of(client)
    if current.version == version:
        return current
    rows = await ctx.supa.select(
        "brand_kits", client_id=f"eq.{client['id']}", version=f"eq.{version}", select="kit"
    )
    if not rows:
        raise DraftError(
            f"Version {version} of {client['name']}'s brand kit is missing, so this poster "
            f"can't be drawn in the colours it was made with."
        )
    return BrandKit.model_validate(rows[0]["kit"])


def rules_of(row: dict[str, Any]) -> ClientRules:
    return ClientRules.model_validate(row["rules"])


async def local_path(ctx: Context, sha: str) -> str:
    """Where this asset is on this machine, fetching it from storage if it is not.

    The store on disk is a cache in front of object storage, not the record. A server
    that loses its disk, or a second server that never had it, keeps working: the
    first render after a restart pays one download and everything after is local.

    On Railway the API and the worker are separate machines with separate disks, so
    the worker has never ingested a photo someone uploaded: this fallback is the
    normal path there, not the exception.
    """
    try:
        return str(ctx.store.path_for(sha, "full").resolve())
    except AssetNotFound:
        pass  # not ingested on this machine
    path = ctx.store.fetched(sha)
    if not path.is_file():
        data = await ctx.supa.download(f"{ctx.agency_id}/assets/{sha}.png")
        path = await asyncio.to_thread(ctx.store.keep_fetched, sha, data)
    return str(path.resolve())


# A logo's colours depend only on its bytes, and its SHA-256 names those bytes, so
# what is read once is right forever. Each entry is a few dozen floats.
_INKS: dict[str, LogoInk | None] = {}


async def logo_ink(ctx: Context, sha: str) -> LogoInk | None:
    """The colours of one logo file, or None if it cannot be read as a logo."""
    if sha not in _INKS:
        data = Path(await local_path(ctx, sha)).read_bytes()
        try:
            reading = await asyncio.to_thread(read_logo, data)
        except LogoError:
            _INKS[sha] = None
        else:
            _INKS[sha] = LogoInk.from_reading(reading)
    return _INKS[sha]


async def inks_for(ctx: Context, spec: PosterSpec) -> dict[str, LogoInk]:
    shas = {layer.asset for layer in spec.layers if isinstance(layer, LogoLayer) and layer.asset}
    found = {sha: await logo_ink(ctx, sha) for sha in shas}
    return {sha: ink for sha, ink in found.items() if ink is not None}


# Below this on its shorter side a photo cannot fill a poster at all; above it, a
# small or soft photo of the client's real work still beats an invented one.
PHOTO_FLOOR_PX = 320


async def pick_photos(
    ctx: Context, client_id: str, needed: dict[str, str]
) -> tuple[dict[str, str], list[Flag]]:
    """Best photo first, by role. Only a background may be invented.

    Good photos are used first, best first: sharp enough, a clear subject, room for
    words (`photo.check.fitness`, M10f). If there are not enough, photos that are
    small, soft or dark are used next, and the draft says so. A real photo of the
    client's own business beats a generated one even when it is imperfect, and a
    designer can swap it in one click once the client sends a better one. Only
    thumbnails too small to fill a poster at all are never used.
    """
    rows = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client_id}",
        kind="eq.photo",
        select="sha256,flags,quality",
        order="created_at.desc",
    )

    def best(row: dict[str, Any]) -> tuple[float, float]:
        quality = row.get("quality") or {}
        return fitness(quality), float(quality.get("sharpness", 0.0))

    def shorter_side(row: dict[str, Any]) -> float:
        return float((row.get("quality") or {}).get("min_dimension", PHOTO_FLOOR_PX))

    good = sorted(
        (r for r in rows if not (UNUSABLE & set(r.get("flags") or []))),
        key=best,
        reverse=True,
    )
    weak = sorted(
        (
            r
            for r in rows
            if UNUSABLE & set(r.get("flags") or []) and shorter_side(r) >= PHOTO_FLOOR_PX
        ),
        key=best,
        reverse=True,
    )
    chosen = list(zip(needed, good + weak, strict=False))
    assets = {layer: row["sha256"] for layer, row in chosen}

    flags: list[Flag] = []
    used_weak = [row for _, row in chosen if row in weak]
    if used_weak:
        problems = sorted(
            {
                {"too_small": "small", "too_blurry": "soft", "too_dark": "dark"}[flag]
                for row in used_weak
                for flag in UNUSABLE & set(row.get("flags") or [])
            }
        )
        flags.append(
            Flag(
                code="photo_weak",
                message=(
                    f"Used {len(used_weak)} photo(s) that are {' and '.join(problems)}, "
                    f"because nothing better was uploaded. Ask for a larger, sharper one."
                ),
            )
        )
    tiny = len(rows) - len(good) - len(weak)
    if tiny:
        flags.append(
            Flag(
                code="photo_unusable",
                message=f"{tiny} photo(s) were too small to fill a poster, so were not used.",
            )
        )
    return assets, flags


async def ask_for_copy(
    gateway: Gateway, request: PostCopyRequest, *, agency_id: str, second_try: bool = True
) -> tuple[PostCopyResult, int]:
    """The words for one post, and how many calls it took (1 or 2).

    The offer line must say something the headline does not, and no line may use a
    phrase the client's rules ban, even when the brief does (post.copy v3/v4). Asking
    is not enough, so both are checked in code; one second try is told exactly what
    was wrong, and the second is kept only if it fixed more than it broke. Shared by
    drafts and the copy eval, so the eval measures what the product does.
    """
    result = await gateway.run(TaskName.POST_COPY, request, agency_id=agency_id)
    assert isinstance(result, PostCopyResult)
    first = _sent_back(request, result)
    sent_back = copycheck.feedback(
        first, result.headline, result.offer_detail, request.banned_claims
    )
    if not sent_back or not second_try:
        return result, 1
    retry = request.model_copy(update={"feedback": sent_back})
    second = await gateway.run(TaskName.POST_COPY, retry, agency_id=agency_id)
    assert isinstance(second, PostCopyResult)
    # Keep the second only if it fixed more than it broke.
    return (second if len(_sent_back(request, second)) < len(first) else result), 2


def _sent_back(request: PostCopyRequest, result: PostCopyResult) -> list[copycheck.Problem]:
    found = copycheck.problems(
        request.brief,
        result.headline,
        result.offer_detail,
        result.cta,
        banned=request.banned_claims,
    )
    return [one for one in found if one.code in copycheck.SENT_BACK]


async def write_copy(
    ctx: Context, client: dict[str, Any], post: dict[str, Any], template: PosterSpec
) -> tuple[PostCopy, list[dict[str, Any]]]:
    rules = rules_of(client)
    binding = binding_for(post["template_id"])
    limits = binding.limits(measure(template, brand_of(client), root=ctx.settings.repo_root))
    brief = post["brief"]
    if post.get("occasion"):
        brief = f"{brief}\nOccasion: {post['occasion']}"
    request = PostCopyRequest(
        brief=brief,
        client_name=client["name"],
        trade=client.get("trade", ""),
        services=tuple(rules.services),
        areas=tuple(rules.areas),
        allowed_hashtags=tuple(rules.allowed_hashtags),
        max_hashtags=rules.max_hashtags,
        must_include=tuple(rules.must_include),
        banned_claims=tuple(rules.banned_claims),
        photo_description=post.get("photo_note", ""),
        headline_max_chars=limits["headline"],
        offer_max_chars=limits["offer_detail"],
        cta_max_chars=limits["cta"],
    )
    tracer = CollectingTracer(inner=ctx.gateway.tracer)
    gateway = Gateway(router=ctx.gateway.router, budget=ctx.gateway.budget, tracer=tracer)
    result, _ = await ask_for_copy(gateway, request, agency_id=ctx.agency_id)
    words = PostCopy(
        headline=result.headline,
        offer_detail=result.offer_detail,
        cta=result.cta,
        caption=result.caption,
        hashtags=result.hashtags,
        alt_text=result.alt_text,
        # A "short" headline no shorter, or a "long" one no longer, adds nothing.
        headline_short=result.headline_short
        if 0 < len(result.headline_short) < len(result.headline)
        else "",
        headline_long=result.headline_long
        if len(result.headline_long) > len(result.headline)
        else "",
        missing=tuple(one.strip()[:100] for one in result.missing if one.strip())[:3],
    )
    step = tracer.step("write_copy", TaskName.POST_COPY)
    return words, [step.model_dump()]


# Prices, dates and phone numbers in a brief invite an image model to paint them,
# and a generated background must carry no text at all. The words go on the poster
# as text layers; the picture only needs the subject.
_FIGURES = re.compile(r"[0-9£$€%]+")


def background_prompt(client: dict[str, Any], post: dict[str, Any]) -> str:
    """What to paint behind a post when the client has sent no usable photo.

    Built from what the post is about, not just the trade: a meditation centre's
    summer retreat and a plumber's boiler offer need different pictures, and
    "a local trade work setting" fits neither well.
    """
    trade = (client.get("trade") or "").replace("_", " ").strip()
    who = f"{client['name']}, a {trade}" if trade else str(client["name"])
    topic = " ".join(_FIGURES.sub(" ", post.get("brief", "")).split())[:300]
    return (
        f"A clean, realistic photograph for the background of a social media post by "
        f"{who}. The post is about: {topic}. Show a believable scene that fits it. "
        f"Plain and uncluttered, with calm space in the lower half for text. "
        f"Absolutely no words, letters, numbers, logos, signs or watermarks."
    )


async def make_background(
    ctx: Context, client: dict[str, Any], post: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    request = BackgroundRequest(prompt=background_prompt(client, post), aspect=post["aspects"][0])
    tracer = CollectingTracer(inner=ctx.gateway.tracer)
    gateway = Gateway(router=ctx.gateway.router, budget=ctx.gateway.budget, tracer=tracer)
    result = await gateway.run(TaskName.BACKGROUND_CREATE, request, agency_id=ctx.agency_id)
    png = getattr(result, "image_png", None)
    if not isinstance(png, bytes):
        raise DraftError("the background service returned something that was not an image")
    sha = await store_image(ctx, client["id"], png, kind="generated")
    return sha, [tracer.step("make_background", TaskName.BACKGROUND_CREATE).model_dump()]


def _checked(web: Path) -> tuple[dict[str, float], list[AssetFlag]]:
    from PIL import Image

    with Image.open(web) as image:
        return upload_check(image)


async def store_image(ctx: Context, client_id: str | None, data: bytes, *, kind: str) -> str:
    """Normalise, hash, keep locally, push to storage, record the row.

    A photo is also checked for its subject (M10f): the numbers the photo picker ranks
    by, and a flag when the subject runs off the top of the photo.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    try:
        meta = ctx.store.put(temp, source="generated" if kind == "generated" else "client")
    finally:
        temp.unlink(missing_ok=True)
    quality, flags = dict(meta.quality), list(meta.flags)
    if kind == "photo":
        numbers, found = await asyncio.to_thread(_checked, ctx.store.path_for(meta.sha256, "web"))
        quality |= numbers
        flags += [flag for flag in found if flag not in flags]
    path = f"{ctx.agency_id}/assets/{meta.sha256}.png"
    await ctx.supa.upload(path, ctx.store.path_for(meta.sha256, "full").read_bytes(), "image/png")
    await ctx.supa.insert(
        "assets",
        {
            "agency_id": ctx.agency_id,
            "client_id": client_id,
            "sha256": meta.sha256,
            "kind": kind,
            "width": meta.width,
            "height": meta.height,
            "flags": flags,
            "quality": quality,
            "storage_path": path,
        },
        upsert=True,
        on_conflict="agency_id,sha256",
    )
    return meta.sha256


async def photo_reads(spec: PosterSpec, asset_paths: dict[str, str]) -> dict[str, PhotoRead]:
    """Each photo on the poster measured once: faces, what stands out, what is busy.

    Cached per file inside read_photo, so framing a draft and checking it read each
    photo once. The face model is CPU work, so it runs in a thread.
    """
    shas = sorted(
        {
            layer.asset
            for layer in spec.layers
            if isinstance(layer, ImageLayer)
            and layer.fit == "cover"
            and layer.asset is not None
            and layer.asset in asset_paths
        }
    )
    reads = await asyncio.gather(*(asyncio.to_thread(read_photo, asset_paths[sha]) for sha in shas))
    return dict(zip(shas, reads, strict=True))


# A matte depends only on the photo's pixels and is stored by content, so one per
# photo per agency is made in a process's lifetime: (agency, photo) -> matte.
_MATTES: dict[tuple[str, str], str] = {}


def _matte_png(path: str) -> bytes:
    from PIL import Image

    with Image.open(path) as image:
        made = matte(image)
    out = io.BytesIO()
    made.save(out, "PNG")
    return out.getvalue()


async def matte_for(ctx: Context, photo: str) -> str:
    """The subject matte of one photo as an asset of its own (M10g): kept on this
    machine and in storage beside the photo, so any machine can draw it. No asset
    row: it is not a photo anyone chose, and it is never offered as one."""
    import tempfile

    key = (ctx.agency_id, photo)
    if key in _MATTES:
        return _MATTES[key]
    data = await asyncio.to_thread(_matte_png, await local_path(ctx, photo))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    try:
        meta = ctx.store.put(temp, source="generated")
    finally:
        temp.unlink(missing_ok=True)
    await ctx.supa.upload(
        f"{ctx.agency_id}/assets/{meta.sha256}.png",
        ctx.store.path_for(meta.sha256, "full").read_bytes(),
        "image/png",
    )
    _MATTES[key] = meta.sha256
    return meta.sha256


async def with_mattes(ctx: Context, spec: PosterSpec, asset_paths: dict[str, str]) -> PosterSpec:
    """Every photo with words behind its subject gets the subject's matte (schema 6),
    and `asset_paths` learns where it is. Before a poster is drawn or kept: a poster
    saying "behind" without a matte is drawn with the words in front."""
    wanted = {
        layer.behind for layer in spec.layers if isinstance(layer, TextLayer) and layer.behind
    }
    layers = []
    for layer in spec.layers:
        if (
            isinstance(layer, ImageLayer)
            and layer.id in wanted
            and layer.asset is not None
            and layer.subject is None
        ):
            layer = layer.model_copy(update={"subject": await matte_for(ctx, layer.asset)})
        if isinstance(layer, ImageLayer) and layer.subject and layer.subject not in asset_paths:
            asset_paths[layer.subject] = await local_path(ctx, layer.subject)
        layers.append(layer)
    return spec.model_copy(update={"layers": layers}) if wanted else spec


def swapped_photos(before: PosterSpec, after: PosterSpec) -> set[str]:
    """Photo layers whose photo an edit replaced."""
    old = {layer.id: getattr(layer, "asset", None) for layer in before.layers}
    return {
        layer.id
        for layer in after.layers
        if isinstance(layer, ImageLayer) and layer.asset != old.get(layer.id)
    }


async def reframe(
    ctx: Context,
    before: PosterSpec,
    after: PosterSpec,
    asset_paths: dict[str, str],
    kit: BrandKit,
    aspects: list[Aspect],
) -> PosterSpec:
    """Frame the photos an edit swapped in. Every other crop stays as the client saw it:
    a tweak to the words must not quietly move the photo."""
    swapped = swapped_photos(before, after)
    if not swapped:
        return after
    reads = await photo_reads(after, asset_paths)
    return frame_photos(after, reads, kit, ctx.settings.repo_root, aspects, only=swapped)


async def render_versions(
    ctx: Context,
    post: dict[str, Any],
    spec: PosterSpec,
    variants: dict[str, Any],
    asset_paths: dict[str, str],
    kit: BrandKit,
) -> tuple[list[dict[str, Any]], list[Flag]]:
    from stratum_worker.render import render_poster

    laid_out = {aspect: retarget(spec, aspect, variants) for aspect in post["aspects"]}
    inks = await inks_for(ctx, spec)
    reads = await photo_reads(spec, asset_paths)

    def draw(aspect: str) -> dict[str, Any]:
        one = laid_out[aspect]
        png = render_poster(one, kit, assets=asset_paths, root=ctx.settings.repo_root)
        width, height = one.canvas.size
        return {"aspect": aspect, "png": png, "width": width, "height": height}

    # Four renders are four independent pieces of CPU work, and rendering is a sync
    # function, so each goes to a thread rather than queueing behind the last.
    out = list(await asyncio.gather(*(asyncio.to_thread(draw, a) for a in laid_out)))
    for drawn in out:
        drawn["preview"] = preview(drawn["png"])
    root = ctx.settings.repo_root
    return out, per_size(
        {
            aspect: check_layout(one, kit, root=root, logos=inks)
            + face_flags(one, reads, kit, root)
            for aspect, one in laid_out.items()
        }
    )


def per_size(found: dict[str, tuple[Flag, ...]]) -> list[Flag]:
    """One flag per problem, saying which sizes have it when not all of them do.

    Checked size by size, the same problem came back once per size: "To check" showed
    two identical lines about the logo and no way to tell which post they meant.
    """
    sizes: dict[Flag, list[str]] = {}
    for aspect, flags in found.items():
        for flag in flags:
            sizes.setdefault(flag, []).append(aspect)
    merged: list[Flag] = []
    for flag, aspects in sizes.items():
        if len(aspects) < len(found):
            named = " and ".join(aspects) if len(aspects) < 3 else ", ".join(aspects)
            flag = flag.model_copy(update={"message": f"{flag.message} ({named} only)"})
        merged.append(flag)
    return merged


async def save_version(
    ctx: Context,
    post: dict[str, Any],
    spec: PosterSpec,
    renders: list[dict[str, Any]],
    flags: list[Flag],
    note: str,
    after: int | None = None,
) -> dict[str, Any]:
    """Write the next version of a poster.

    `after` is the version number the caller already looked up. Passing it saves a
    round trip to another continent to ask a question we were just told the answer to.
    """
    if after is None:
        existing = await ctx.supa.select(
            "post_versions",
            post_id=f"eq.{post['id']}",
            select="version",
            order="version.desc",
            limit="1",
        )
        after = existing[0]["version"] if existing else 0
    version = after + 1
    rows = await ctx.supa.insert(
        "post_versions",
        {
            "agency_id": ctx.agency_id,
            "post_id": post["id"],
            "version": version,
            "spec": spec.model_dump(mode="json"),
            "note": note,
            "created_by": ctx.user_id,
        },
    )
    saved = rows[0]

    # Previews are NOT uploaded anywhere. Profiling one "make the logo bigger" said
    # 4.7 of its 5.8 seconds went on pushing four preview images to storage - to keep
    # something the renderer reproduces in forty milliseconds from a spec we already
    # store. So the spec is the record and the picture is derived from it, served by
    # /previews/{version}/{aspect}.webp and cached on disk. A version never changes,
    # so its picture never changes, so that cache never needs invalidating.
    for render in renders:
        cache_preview(ctx, saved["id"], render["aspect"], render["preview"])

    # Renders and flags describe the same version and neither waits on the other.
    writes = []
    if renders:
        writes.append(
            ctx.supa.insert(
                "renders",
                [
                    {
                        "agency_id": ctx.agency_id,
                        "post_version_id": saved["id"],
                        "aspect": render["aspect"],
                        "width": render["width"],
                        "height": render["height"],
                        "bytes": len(render["preview"]),
                        "storage_path": f"preview:{saved['id']}:{render['aspect']}",
                    }
                    for render in renders
                ],
            )
        )
    if flags:
        writes.append(
            ctx.supa.insert(
                "flags",
                [
                    {
                        "agency_id": ctx.agency_id,
                        "post_version_id": saved["id"],
                        "code": flag.code,
                        "message": flag.message,
                        "severity": flag.severity,
                    }
                    for flag in flags
                ],
            )
        )
    if writes:
        await asyncio.gather(*writes)
    return saved


def file_variants(ctx: Context, template_id: str) -> Variants:
    """The template file's per-size layout, for posters made before they carried
    their own (spec schema 3). A design-space poster always carries its own."""
    path = ctx.settings.templates_dir / f"{template_id}.json"
    return load_variants(path) if path.is_file() else {}


def template_for(
    ctx: Context,
    template_id: str,
    kit: BrandKit,
    *,
    copy: Copy = PLACEHOLDER,
    logo_aspect: float = 0.0,
) -> PosterSpec:
    """The design to fill, carrying its layout for every size.

    A hand-made template comes from its file. A design-space design is laid out
    here, around `copy` measured with this brand's fonts.
    """
    if is_design(template_id):
        laid = lay_out(template_id, kit, ctx.settings.repo_root, copy=copy, logo_aspect=logo_aspect)
        if laid is None:
            raise DraftError(
                f"These words do not fit the {decode(template_id).title} design in every "
                f"size. Shorten the headline or the offer, or pick another design."
            )
        return laid
    path = ctx.settings.templates_dir / f"{template_id}.json"
    return embed(load_template(path), load_variants(path))


def missing_photos_error(client: dict[str, Any], roles: dict[str, str], usable: int) -> DraftError:
    """Why a design that needs the client's own photos cannot be made yet."""
    if {"before", "after"} & set(roles.values()):
        return DraftError(
            f"This design shows real work, so it needs {len(roles)} real photos "
            f"({', '.join(roles)}) and has {usable} usable. Upload them for "
            f"{client['name']}, or pick a design that can use a generated background."
        )
    return DraftError(
        f"This design shows {len(roles)} of {client['name']}'s own photos and has "
        f"{usable} usable. Upload more, or pick a design with one photo."
    )


async def draft(ctx: Context, post: dict[str, Any], client: dict[str, Any]) -> dict[str, Any]:
    """Steps 1-7, recorded as they go."""
    if post["template_id"] == AUTO:
        # Let Stratum choose: many designs ranked, three different ones kept (M12b).
        from stratum_worker.app.options import draft_options

        return await draft_options(ctx, post, client)
    started = time.perf_counter()
    binding = binding_for(post["template_id"])
    kit = brand_of(client)
    template = template_for(ctx, post["template_id"], kit)

    words, steps = await write_copy(ctx, client, post, template)

    assets, flags = await pick_photos(ctx, client["id"], binding.photos)
    missing = [layer for layer in binding.photos if layer not in assets]
    unfillable = [layer for layer in missing if binding.photos[layer] != "background"]
    if unfillable:
        # Never invented: a generated "after" photo would show work that was never
        # done, which is a false advertising claim, not a design shortcut.
        raise missing_photos_error(client, binding.photos, len(assets))
    if missing:
        # One background per post, painted for what this post is about. At about
        # $0.006 each, a picture that fits beats reusing one made for another post.
        sha, more = await make_background(ctx, client, post)
        steps += more
        for layer in missing:
            assets[layer] = sha
        flags.append(
            Flag(
                code="generated_background",
                message=(
                    "No usable photo, so the background was generated. Swap in a "
                    "real one when they send it."
                ),
            )
        )

    # The newest logo: uploading a new one is how a client's logo is replaced.
    logos = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client['id']}",
        kind="eq.logo",
        select="sha256,width,height",
        order="created_at.desc",
        limit="1",
    )
    if binding.logo:
        if not logos:
            raise DraftError(
                f"{client['name']} has no logo yet. Upload one - we will not invent branding."
            )
        assets[binding.logo] = logos[0]["sha256"]

    printed = words
    if is_design(post["template_id"]):
        # Laid out again around the real words, and the logo's real shape, so the
        # offer sits one gap under the headline's last line and the logo's box is
        # the logo - with the headline version this design sets best (M10e).
        width, height = (logos[0].get("width"), logos[0].get("height")) if logos else (0, 0)
        found = best_copy(
            post["template_id"],
            versions(words.model_dump(mode="json")),
            kit,
            ctx.settings.repo_root,
            logo_aspect=(width / height) if width and height else 0.0,
        )
        if found is None:
            raise DraftError(
                f"These words do not fit the {decode(post['template_id']).title} design in "
                f"every size. Shorten the headline or the offer, or pick another design."
            )
        chosen, template = found
        printed = words.model_copy(update={"headline": chosen.headline})
    texts = {layer: getattr(printed, field) for layer, field in binding.texts.items()}
    texts.update(binding.fixed or {})
    spec = build_spec(template, kit, texts, assets)
    variants: Variants = {}  # the spec carries its own sizes now
    if binding.logo:
        # The template cannot know this client's logo colours; this is where a navy
        # logo headed for a navy band gets a panel behind it instead.
        ink = await logo_ink(ctx, assets[binding.logo])
        if ink is not None:
            spec = fit_logo(spec, kit, ink, variants, post["aspects"])

    asset_paths = {sha: await local_path(ctx, sha) for sha in set(assets.values())}
    # Which part of each photo shows, per size: faces whole and clear of the words,
    # the words over the calmest part. Chosen once, here, and stored in the poster.
    reads = await photo_reads(spec, asset_paths)
    spec = frame_photos(spec, reads, kit, ctx.settings.repo_root, post["aspects"])
    spec = await with_mattes(ctx, spec, asset_paths)
    renders, layout_flags = await render_versions(ctx, post, spec, variants, asset_paths, kit)
    flags.extend(layout_flags)
    flags.extend(check_copy(words, rules_of(client), post["brief"]))
    flags.extend(brief_flags({**post, "words": words.model_dump(mode="json")}, spec))

    version = await save_version(ctx, post, spec, renders, flags, note="first draft")
    cost = sum(float(step.get("cost_usd", 0)) for step in steps)
    for step in steps:
        await ctx.supa.insert("steps", {"agency_id": ctx.agency_id, "post_id": post["id"], **step})
    await ctx.supa.update(
        "posts",
        {"words": words.model_dump(mode="json"), "cost_usd": cost, "updated_at": "now()"},
        id=f"eq.{post['id']}",
    )
    version["seconds"] = round(time.perf_counter() - started, 2)
    return version


def brief_flags(post: dict[str, Any], spec: PosterSpec) -> list[Flag]:
    """The brief's offer and dates, if the poster as it now stands does not show them.

    Run on every new version - draft, tweak and quick change - so the flag stays until
    the poster says what the brief asked for, and goes as soon as it does.
    """
    shown = "\n".join(
        one.content for one in spec.layers if isinstance(one, TextLayer) and one.visible
    )
    words = post.get("words") or {}
    has_offer_line = "offer_detail" in binding_for(post["template_id"]).texts.values()
    return [
        *check_brief_on_poster(
            post["brief"],
            shown,
            str(words.get("caption", "")),
            has_offer_line=has_offer_line,
        ),
        *repeated_lines(spec),
    ]


def repeated_lines(spec: PosterSpec) -> list[Flag]:
    """The offer line, as the poster now shows it, saying the headline again. Checked
    on every version, so the flag goes as soon as either line is changed."""
    shown = {
        one.id: one.content for one in spec.layers if isinstance(one, TextLayer) and one.visible
    }
    headline, offer = shown.get("headline", ""), shown.get("offer", "")
    if not headline or not offer or copycheck.echo(headline, offer) <= copycheck.ECHO_MAX:
        return []
    return [
        Flag(
            code="copy_repeats",
            message=(
                "The offer line says the same as the headline. Give it a second detail: "
                "a price, a date, what is included, or the area."
            ),
        )
    ]


NOTHING_CHANGED = (
    "That would not change anything on this poster, so nothing was saved. Say which "
    "part to change, for example 'make the logo smaller'."
)


def why_unread(exc: NoProviderAvailable) -> str:
    """What a designer is told when no model could read their change."""
    cause = exc.__cause__
    if isinstance(cause, ProviderTimeout | InvalidProviderOutput):
        return (
            "That was too much to work out in one go. Try it as two or three shorter "
            "changes, one per message."
        )
    if isinstance(cause, ProviderUnavailable | ProviderRateLimited):
        return (
            "The AI that reads changes is not answering right now. Try again in a "
            "minute; the instant changes above still work."
        )
    return (
        "The AI that reads changes is not available right now. The instant changes "
        "above still work."
    )


_OP_PROBLEMS: tuple[tuple[str, str], ...] = (
    ("unknown layer", "This design has no {part}."),
    ("is not a text layer", "The {part} has no words to change."),
    ("has no colour", "The {part} has no colour of its own to change."),
    ("is not a photo", "The {part} is not a photo, so it cannot be moved, zoomed or darkened."),
    ("shows the whole photo", "The {part} shows the whole photo, so there is nothing to move."),
    ("is locked", "The {part} is locked in this design."),
    ("holds no asset", "The {part} has no picture to swap."),
)
_LAYER_IN_ERROR = re.compile(r"layer '([^']+)'")


def plain_op_error(exc: OpError) -> str:
    """An op that could not be applied, said to a designer rather than a developer."""
    text = str(exc)
    found = _LAYER_IN_ERROR.search(text)
    part = friendly(found.group(1)) if found else "part of the poster"
    for marker, sentence in _OP_PROBLEMS:
        if marker in text:
            return sentence.format(part=part)
    return "That change could not be made to this design. Try saying it another way."


def _asking(question: str, choices: list[Choice]) -> dict[str, Any]:
    """A question back to the designer, with changes they can click (UI brief C7).

    `why` says the same in one line, for a page that does not show choices yet.
    """
    tweaks = [choice.label for choice in choices if choice.kind == "tweak"]
    why = question
    if tweaks:
        why += " You could say: " + " or ".join(f"'{label}'" for label in tweaks) + "."
    return {
        "outcome": "needs_human",
        "why": why,
        "question": question,
        "choices": [choice.model_dump() for choice in choices],
    }


def off_brand_colour(
    ops: list[EditOp], kit: BrandKit, client: dict[str, Any]
) -> dict[str, Any] | None:
    """A colour change to a colour the brand does not have: a question, never a crash.

    The model is told the brand's colours by name and asked to ask; this is the guard
    for when it does not. "palette.green" passes the spec's pattern, and would only
    fail at render time, as an error no designer can act on.
    """
    for op in ops:
        if not isinstance(op, SetColor):
            continue
        token = op.color.removeprefix("palette.")
        if token in kit.palette:
            continue
        word = token.replace("_", " ")
        names = list(dict.fromkeys(colour_name(value) for value in kit.palette.values()))
        question = (
            f"{word.capitalize()} isn't one of {client['name']}'s brand colours "
            f"({', '.join(names)})."
        )
        near = nearest(word, kit.palette).nearest if word in WORDS else ()
        part = friendly(op.layer)
        choices = [Choice(label=f"Make the {part} {colour_name(kit.palette[one])}") for one in near]
        choices.append(Choice(label=f"Add {word} to the brand colours", kind="brand"))
        return _asking(question, choices)
    return None


async def tweak(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], message: str
) -> dict[str, Any]:
    """Plain English in, a new version out - with no model touching the poster.

    The model only ever returns ops, which are validated before anything happens:
    spec ops into real EditOp objects, and the three the service carries out itself
    (restyle, ask, undo) into their own models. An op it invents, a layer that does
    not exist or a colour the brand does not have is refused here; the worst a
    confused or hostile instruction can do is nothing.
    """
    from stratum_worker.app import actions  # restyle and undo use this module

    typed = undo_asked(message)
    if typed is not None:
        return await actions.undo(ctx, post, client, message, everything=typed.all)
    started = time.perf_counter()
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    if not versions:
        raise DraftError("this post has no draft yet")
    latest = versions[0]
    spec = load_spec(latest["spec"])
    # An edit keeps the colours the poster was made with. Moving a post onto a newer
    # brand kit is its own decision, not a side effect of "make the logo bigger".
    kit = await kit_for(ctx, client, spec.brand_kit_version)

    # The model sees the poster as a designer would describe it (M10c): each layer
    # with where it is, its colour by name and, for text, how many characters it
    # holds - a model asked to "shorten the headline" otherwise has no idea what
    # short means here. Plus the brand's colours by name, the nearest brand colours
    # to any colour the message names, this design's style settings, and the last
    # few changes.
    fits = measure(spec, kit, root=ctx.settings.repo_root)
    bound = spec.assets()
    asset_paths = {sha: await local_path(ctx, sha) for sha in bound if isinstance(sha, str)}
    sizes, photos, recent = await asyncio.gather(
        actions.photo_sizes(asset_paths),
        actions.client_photos(ctx, client),
        ctx.supa.select(
            "edits",
            post_id=f"eq.{post['id']}",
            select="message,outcome",
            order="created_at.desc",
            limit="5",
        ),
    )
    request = EditParseRequest(
        message=message,
        layers=describe_layers(spec, kit, fits, sizes),
        palette=[f"palette.{name}" for name in kit.palette],
        colours=colour_lines(spec, kit),
        asked=asked_colours(message, kit),
        design=style_menu(post["template_id"], kit, len(photos)),
        recent=recent_lines(list(reversed(recent))),
    )
    tracer = CollectingTracer(inner=ctx.gateway.tracer)
    gateway = Gateway(router=ctx.gateway.router, budget=ctx.gateway.budget, tracer=tracer)

    async def record(outcome: str, ops: list[dict[str, Any]], cost_usd: float) -> None:
        await ctx.supa.insert(
            "edits",
            {
                "agency_id": ctx.agency_id,
                "post_id": post["id"],
                "from_version": latest["version"],
                "message": message,
                "ops": ops,
                "outcome": outcome,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cost_usd": cost_usd,
                "created_by": ctx.user_id,
            },
        )

    try:
        result = await gateway.run(TaskName.EDIT_PARSE, request, agency_id=ctx.agency_id)
    except NoProviderAvailable as exc:
        # The poster is untouched and the designer is told what to try, in a
        # sentence - not "edit.parse: every provider failed" and a 503.
        await record("failed", [], sum(one.cost_usd for one in tracer.records))
        return {"outcome": "failed", "why": why_unread(exc)}
    assert isinstance(result, EditParseResult)
    step = tracer.step("edit_parse", TaskName.EDIT_PARSE)
    raw = [dict(one) for one in result.ops]

    try:
        raw_ops, service_ops = split(raw)
        ops = OPS.validate_python(raw_ops)
    except ValidationError:
        await record("failed", raw, step.cost_usd)
        return {
            "outcome": "failed",
            "why": "I could not turn that into a change. Try saying it another way.",
        }
    asks = [one for one in service_ops if isinstance(one, Ask)]
    if asks:
        await record("needs_human", raw, step.cost_usd)
        return _asking(asks[0].question, asks[0].choices)
    restyles = [one for one in service_ops if isinstance(one, Restyle)]
    undos = [one for one in service_ops if isinstance(one, Undo)]
    if undos and not ops and not restyles:
        return await actions.undo(ctx, post, client, message, everything=undos[0].all)
    if not ops and not restyles:
        await record("needs_human", raw, step.cost_usd)
        return {
            "outcome": "needs_human",
            "why": result.needs_human or "I could not tell what to change.",
        }
    off_brand = off_brand_colour(ops, kit, client)
    if off_brand is not None:
        await record("needs_human", raw, step.cost_usd)
        return off_brand

    return await commit_change(
        ctx,
        post,
        client,
        latest=latest,
        spec=spec,
        kit=kit,
        ops=ops,
        changes=_merged(restyles),
        photos=photos,
        message=message,
        record=lambda outcome: record(outcome, raw, step.cost_usd),
        notes=[result.note[:300]] if result.note else [],
        unable=[result.needs_human[:300]] if result.needs_human else [],
        unchanged=result.needs_human or NOTHING_CHANGED,
        started=started,
        step=step,
    )


def _merged(restyles: list[Restyle]) -> dict[str, str] | None:
    if not restyles:
        return None
    changes: dict[str, str] = {}
    for one in restyles:
        changes.update(one.changes)
    return changes


Record = Callable[[str], Awaitable[None]]


async def commit_change(
    ctx: Context,
    post: dict[str, Any],
    client: dict[str, Any],
    *,
    latest: dict[str, Any],
    spec: PosterSpec,
    kit: BrandKit,
    ops: list[EditOp],
    changes: dict[str, str] | None,
    photos: list[str],
    message: str,
    record: Record,
    notes: list[str],
    unable: list[str],
    unchanged: str = NOTHING_CHANGED,
    started: float,
    step: Any = None,
    authorised: str = "",
) -> dict[str, Any]:
    """Make a change the next version: a typed tweak's ops, or a control panel's.

    Restyles first (the neighbouring design, with the person's earlier changes kept),
    then the ops, then the usual: frame swapped photos, render every size, check the
    layout, the claims and the brief, save, and record. `record(outcome)` writes the
    edits row; `step` is the model's trace step, None when no model was asked.
    `authorised` is extra human-written text the claims check may trust (words typed
    into the panel).
    """
    from stratum_worker.app import actions  # restyle uses this module

    notes, unable = list(notes), list(unable)
    base, template_id = spec, post["template_id"]
    restyled = None
    if changes:
        try:
            restyled = await actions.restyle(ctx, post, client, spec, kit, changes, photos)
        except RestyleError as exc:
            if not ops:
                await record("needs_human")
                return {"outcome": "needs_human", "why": str(exc)}
            unable.append(str(exc))
        else:
            base, template_id = restyled.spec, restyled.design_id
            if not notes:
                notes.append(restyled.note)
            if restyled.added_photo:
                notes.append(f"Added another of {client['name']}'s photos for the second space.")

    try:
        changed = apply_ops(base, ops)
    except OpError as exc:
        await record("failed")
        return {"outcome": "failed", "why": plain_op_error(exc)}
    if changed.model_dump() == spec.model_dump():
        # Nothing moved, so there is no new version and it is not called "applied":
        # a designer who asked for a second photo was told it was done, and it wasn't.
        await record("needs_human")
        return {"outcome": "needs_human", "why": unchanged}

    shaped = {**post, "template_id": template_id}
    variants = file_variants(ctx, template_id)
    shas = {getattr(layer, "asset", None) for layer in changed.layers}
    asset_paths = {sha: await local_path(ctx, sha) for sha in shas if isinstance(sha, str)}
    changed = await reframe(ctx, base, changed, asset_paths, kit, post["aspects"])
    changed = await with_mattes(ctx, changed, asset_paths)
    renders, flags = await render_versions(ctx, shaped, changed, variants, asset_paths, kit)

    # An edit can change the words on the poster, so the claims are checked again -
    # against everything a HUMAN has written about this post. A designer asking for
    # "50% rather than 20%" has authorised that number; flagging their own words back
    # at them would teach everyone to ignore the check that catches a model quietly
    # changing a price.
    history = await ctx.supa.select(
        "edits", post_id=f"eq.{post['id']}", select="message", order="created_at"
    )
    trusted = " ".join(
        [post["brief"], post.get("occasion", ""), message, authorised]
        + [row["message"] for row in history]
    )
    on_poster = "\n".join(one.content for one in changed.layers if isinstance(one, TextLayer))
    flags = list(flags) + list(check_claims(on_poster, rules_of(client), trusted))
    flags += brief_flags(shaped, changed)

    saved, _ = await asyncio.gather(
        save_version(
            ctx, post, changed, renders, flags, note=message[:200], after=latest["version"]
        ),
        record("applied"),
    )
    version: dict[str, Any] = saved
    writes = []
    if step is not None:
        writes.append(
            ctx.supa.insert(
                "steps", {"agency_id": ctx.agency_id, "post_id": post["id"], **step.model_dump()}
            )
        )
    if restyled is not None:
        # From here on the post IS the new design: quick changes, the brief check and
        # the next tweak's style settings read it. And a designer choosing a style is
        # a taste label, like a pick (M15).
        writes.append(
            ctx.supa.update(
                "posts", {"template_id": template_id, "updated_at": "now()"}, id=f"eq.{post['id']}"
            )
        )
        writes.append(
            ctx.supa.insert(
                "decision_log",
                {
                    "agency_id": ctx.agency_id,
                    "client_id": client["id"],
                    "post_id": post["id"],
                    "version_id": saved["id"],
                    "kind": "edit_request",
                    "actor": ctx.user_id,
                    "detail": {
                        "what": "restyle",
                        "from": post["template_id"],
                        "to": template_id,
                        "changes": changes,
                    },
                },
            )
        )
    if writes:
        await asyncio.gather(*writes)
    version["outcome"] = "applied"
    # What was done when the request had to be read into, and what could not be done:
    # an inferred change is never a silent one, and a partial one says what is left.
    # `why` carries both in one line for a page that shows only that (until C7).
    version["note"] = " ".join(notes)
    version["unable"] = " ".join(unable)
    version["why"] = " ".join([*notes, *unable])
    version["seconds"] = round(time.perf_counter() - started, 2)
    return version


# --- the control panel (M10d): by hand, no model ---------------------------------------


class PosterChanged(Exception):
    """The poster has a newer version than the one the change was made on."""


EDITOR_OPS = frozenset(
    {"set_text", "set_color", "move", "scale", "set_visible", "pan", "zoom", "dim"}
)


def editor_note(ops: list[EditOp], changes: dict[str, str] | None, kit: BrandKit) -> str:
    """What a change made in the control panel did, for History: "Headline gold; photo
    darker". Made here from the ops, not taken from the page."""
    from stratum_spec.ops import Dim, Move, Pan, Scale, SetColor, SetText, SetVisible, Zoom

    said: list[str] = ["new style"] if changes else []
    for op in ops:
        part = friendly(op.layer) if hasattr(op, "layer") else ""
        if isinstance(op, SetText):
            said.append(f"new {part} words")
        elif isinstance(op, SetColor):
            value = kit.palette.get(op.color.removeprefix("palette."), "")
            said.append(f"{part} {colour_name(value) if value else 'colour'}")
        elif isinstance(op, Move):
            said.append(f"moved the {part}")
        elif isinstance(op, Scale):
            said.append(f"{'bigger' if op.factor > 1 else 'smaller'} {part}")
        elif isinstance(op, SetVisible):
            said.append(f"{'showed' if op.visible else 'hid'} the {part}")
        elif isinstance(op, Pan):
            said.append(f"moved the {part} in its frame")
        elif isinstance(op, Zoom):
            said.append(f"zoomed the {part} {'in' if op.factor > 1 else 'out'}")
        elif isinstance(op, Dim):
            said.append(f"{part} darkness {round(op.amount * 100)}%")
    text = "; ".join(dict.fromkeys(said)) or "no change"
    return f"Editor: {text}"[:200]


async def edit(
    ctx: Context,
    post: dict[str, Any],
    client: dict[str, Any],
    *,
    version: int,
    raw_ops: list[dict[str, Any]],
    restyle: dict[str, str] | None,
) -> dict[str, Any]:
    """A change made by hand in the control panel: instant, free, no model.

    The same validation and the same commit as a typed tweak; only the source of the
    ops differs. `version` is the version the person was looking at: if the poster
    has moved on (another tab, another person), nothing is changed and they are told,
    which also makes a double click one change.
    """
    from stratum_worker.app import actions

    started = time.perf_counter()
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    if not versions:
        raise DraftError("this post has no draft yet")
    latest = versions[0]
    if latest["version"] != version:
        raise PosterChanged(
            f"The poster is on version {latest['version']} now, not {version}. Reload to "
            f"see it, then make the change again."
        )
    unknown = sorted({str(one.get("op")) for one in raw_ops} - EDITOR_OPS)
    if unknown:
        raise DraftError(f"The editor cannot make that change ({', '.join(unknown)}).")
    if not raw_ops and not restyle:
        raise DraftError("Nothing to change.")
    try:
        ops = OPS.validate_python(raw_ops)
        changes = Restyle(changes=restyle).changes if restyle else None
    except ValidationError as exc:
        raise DraftError("That change is outside what the editor allows.") from exc
    spec = load_spec(latest["spec"])
    kit = await kit_for(ctx, client, spec.brand_kit_version)
    off_brand = off_brand_colour(ops, kit, client)
    if off_brand is not None:
        raise DraftError(off_brand["question"])
    photos = await actions.client_photos(ctx, client) if changes else []
    message = editor_note(ops, changes, kit)
    logged = [*raw_ops, *([{"op": "restyle", "changes": changes}] if changes else [])]
    typed = " ".join(str(one.get("text", "")) for one in raw_ops if one.get("op") == "set_text")

    async def record(outcome: str) -> None:
        await ctx.supa.insert(
            "edits",
            {
                "agency_id": ctx.agency_id,
                "post_id": post["id"],
                "from_version": latest["version"],
                "message": message,
                "ops": logged,
                "outcome": outcome,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cost_usd": 0,
                "created_by": ctx.user_id,
            },
        )

    return await commit_change(
        ctx,
        post,
        client,
        latest=latest,
        spec=spec,
        kit=kit,
        ops=ops,
        changes=changes,
        photos=photos,
        message=message,
        record=record,
        notes=[],
        unable=[],
        started=started,
        authorised=typed,
    )


async def inspect_post(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], aspect: str
) -> dict[str, Any]:
    """Everything the control panel draws for one size of the latest version."""
    from stratum_worker.app import actions
    from stratum_worker.edit.inspect import inspect, style_options

    if aspect not in post["aspects"]:
        raise DraftError(f"This post has no {aspect} size.")
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    if not versions:
        raise DraftError("this post has no draft yet")
    latest = versions[0]
    spec = load_spec(latest["spec"])
    kit = await kit_for(ctx, client, spec.brand_kit_version)
    one = retarget(spec, aspect, file_variants(ctx, post["template_id"]))
    bound = one.assets()
    asset_paths = {sha: await local_path(ctx, sha) for sha in bound if isinstance(sha, str)}
    sizes, photos = await asyncio.gather(
        actions.photo_sizes(asset_paths), actions.client_photos(ctx, client)
    )
    root = ctx.settings.repo_root
    fits = measure(one, kit, root=root)
    panel = await asyncio.to_thread(inspect, one, kit, root, fits=fits, photo_sizes=sizes)
    return {
        "version": latest["version"],
        "version_id": latest["id"],
        **panel,
        "style": style_options(post["template_id"], kit, len(photos)),
    }


OPEN_IN_CANVA = """How to finish these posts in Canva

1. In Canva, choose Create a design, then Import file.
2. Pick a .pptx file from this folder. Each post size has its own file.
3. Every headline, shape, photo and the logo arrives as its own layer, so you can
   move, restyle or replace any of them. Text stays text.

The caption and hashtags are in the -caption.txt file, and in the notes under each
design. The .png files are the finished posts exactly as the client approved them.
"""


@dataclass(frozen=True)
class _Prepared:
    spec: PosterSpec
    variants: Variants
    kit: BrandKit
    assets: dict[str, str]


async def _prepare(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], version_id: str
) -> _Prepared:
    """What both exports need: the version's spec, its template variants, the brand
    kit and a local file for every asset it uses."""
    versions = await ctx.supa.select("post_versions", id=f"eq.{version_id}", select="spec")
    if not versions:
        raise DraftError("that version is gone")
    spec = load_spec(versions[0]["spec"])
    variants = file_variants(ctx, post["template_id"])
    bound = spec.assets()
    assets = {s: await local_path(ctx, s) for s in bound if isinstance(s, str)}
    kit = await kit_for(ctx, client, spec.brand_kit_version)
    return _Prepared(spec, variants, kit, assets)


def caption_text(post: dict[str, Any]) -> str:
    words = post.get("words") or {}
    caption = str(words.get("caption", ""))
    tags = " ".join(words.get("hashtags") or [])
    return f"{caption}\n\n{tags}".strip()


def _deck(ready: _Prepared, ctx: Context, post: dict[str, Any], aspect: str) -> bytes:
    from stratum_worker.export import export_pptx

    one = retarget(ready.spec, aspect, ready.variants)
    return export_pptx(
        one,
        ready.kit,
        assets=ready.assets,
        root=ctx.settings.repo_root,
        title=f"{post['slug']} {aspect}",
        notes=caption_text(post),
    )


async def export_deck(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], version_id: str, aspect: str
) -> bytes:
    """One post at one size as an editable PowerPoint file, for opening in Canva."""
    if aspect not in post["aspects"]:
        raise DraftError(f"this post has no {aspect} version")
    ready = await _prepare(ctx, post, client, version_id)
    return await asyncio.to_thread(_deck, ready, ctx, post, aspect)


async def bundle(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], version_id: str
) -> bytes:
    """Everything a person needs to post or keep editing, in one zip.

    For each size: the finished PNG, and a PowerPoint file with every layer
    editable, for finishing in Canva. Plus the caption, the alt text and a short
    note on opening the files in Canva.

    Rebuilt from the stored spec rather than downloaded from storage. What is kept in
    storage is a small preview for the screen, because a designer nudging a logo
    should not wait on four megabytes crossing the network each time. The spec is
    versioned and the renderer is deterministic, so the PNG produced here is exactly
    the poster that preview was made from.
    """
    from stratum_worker.render import render_poster

    ready = await _prepare(ctx, post, client, version_id)

    def draw(aspect: str) -> tuple[str, bytes, bytes]:
        one = retarget(ready.spec, aspect, ready.variants)
        png = render_poster(one, ready.kit, assets=ready.assets, root=ctx.settings.repo_root)
        return aspect, png, _deck(ready, ctx, post, aspect)

    drawn = await asyncio.gather(*(asyncio.to_thread(draw, aspect) for aspect in post["aspects"]))
    slug = post["slug"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for aspect, png, deck in drawn:
            shape = aspect.replace(":", "x")
            archive.writestr(f"{slug}-{shape}.png", png)
            archive.writestr(f"{slug}-{shape}.pptx", deck)
        archive.writestr(f"{slug}-caption.txt", caption_text(post) + "\n")
        words = post.get("words") or {}
        if words.get("alt_text"):
            archive.writestr(f"{slug}-alt-text.txt", words["alt_text"] + "\n")
        archive.writestr("OPEN-IN-CANVA.txt", OPEN_IN_CANVA)
    return buffer.getvalue()


__all__ = [
    "BrandConflict",
    "ComposeError",
    "Context",
    "DraftError",
    "PosterChanged",
    "brand_suggestion",
    "bundle",
    "draft",
    "edit",
    "export_deck",
    "inspect_post",
    "kit_for",
    "save_brand",
    "starter_kit",
    "store_image",
    "tweak",
]


# --- the free path ----------------------------------------------------------------
#
# Everything above involves a model. These do not: they are edit ops built in code
# from a button press, so the only work is a re-render. This is where the claim
# "changes are instant and free" is actually cashed - a designer nudging a logo
# should never wait on a language model, and should never be charged for it.

QUICK: dict[str, str] = {
    "logo_bigger": "Make the logo bigger",
    "logo_smaller": "Make the logo smaller",
    "headline_bigger": "Make the headline bigger",
    "headline_smaller": "Make the headline smaller",
    "hide_logo": "Hide the logo",
    "show_logo": "Show the logo",
    "next_photo": "Use the next photo",
}


async def quick(
    ctx: Context, post: dict[str, Any], client: dict[str, Any], action: str
) -> dict[str, Any]:
    from stratum_spec.ops import Scale, SetVisible, SwapAsset

    if action not in QUICK:
        raise DraftError(f"unknown action {action!r}")
    started = time.perf_counter()
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    if not versions:
        raise DraftError("this post has no draft yet")
    latest = versions[0]
    spec = load_spec(latest["spec"])
    ids = {layer.id for layer in spec.layers}

    ops: list[Any] = []
    if action in ("logo_bigger", "logo_smaller") and "logo" in ids:
        ops.append(Scale(layer="logo", factor=1.18 if action == "logo_bigger" else 0.85))
    elif action in ("headline_bigger", "headline_smaller") and "headline" in ids:
        ops.append(Scale(layer="headline", factor=1.12 if action.endswith("bigger") else 0.9))
    elif action in ("hide_logo", "show_logo") and "logo" in ids:
        visible = action == "show_logo"
        ops.append(SetVisible(layer="logo", visible=visible))
        # A logo with its own panel had its chip switched off for a reason: bringing
        # the logo back must not bring back the chip it was unreadable on.
        logo = spec.layer("logo")
        has_plate = isinstance(logo, LogoLayer) and logo.plate is not None
        if "logo_chip" in ids and not (visible and has_plate):
            ops.append(SetVisible(layer="logo_chip", visible=visible))
    elif action == "next_photo":
        photos = await ctx.supa.select(
            "assets",
            client_id=f"eq.{client['id']}",
            kind="eq.photo",
            select="sha256",
            order="created_at.desc",
        )
        shas = [row["sha256"] for row in photos]
        layer = next(
            (one for one in spec.layers if one.type == "image" and getattr(one, "asset", None)),
            None,
        )
        if layer is None or len(shas) < 2:
            raise DraftError("there is no other photo to switch to yet")
        current = getattr(layer, "asset", None)
        following = shas[(shas.index(current) + 1) % len(shas)] if current in shas else shas[0]
        ops.append(SwapAsset(layer=layer.id, asset=following))
    if not ops:
        raise DraftError("that design has nothing to change that way")

    changed = apply_ops(spec, ops)
    bound = changed.assets()
    asset_paths = {s: await local_path(ctx, s) for s in bound if isinstance(s, str)}
    kit = await kit_for(ctx, client, changed.brand_kit_version)
    changed = await reframe(ctx, spec, changed, asset_paths, kit, post["aspects"])
    changed = await with_mattes(ctx, changed, asset_paths)
    renders, flags = await render_versions(
        ctx, post, changed, file_variants(ctx, post["template_id"]), asset_paths, kit
    )
    flags = [*flags, *brief_flags(post, changed)]
    saved, _ = await asyncio.gather(
        save_version(
            ctx, post, changed, renders, flags, note=QUICK[action], after=latest["version"]
        ),
        ctx.supa.insert(
            "edits",
            {
                "agency_id": ctx.agency_id,
                "post_id": post["id"],
                "from_version": latest["version"],
                "message": QUICK[action],
                "ops": [op.model_dump(mode="json") for op in ops],
                "outcome": "applied",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cost_usd": 0,
                "created_by": ctx.user_id,
            },
        ),
    )
    version: dict[str, Any] = saved
    version["outcome"] = "applied"
    version["seconds"] = round(time.perf_counter() - started, 2)
    return version


# --- brand kits -------------------------------------------------------------------
#
# Reading a logo and saving a palette are both quick (tens of milliseconds, one
# write), so they run in the request rather than through the queue.


class BrandConflict(Exception):
    """Someone saved this client's brand kit after the caller last read it."""


async def newest_logo(ctx: Context, client_id: str) -> str | None:
    rows = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client_id}",
        kind="eq.logo",
        select="sha256",
        order="created_at.desc",
        limit="1",
    )
    return str(rows[0]["sha256"]) if rows else None


async def brand_suggestion(
    ctx: Context, client: dict[str, Any], sha: str | None = None
) -> Suggestion:
    """A palette read from the client's logo - the newest one unless told which.

    Nothing is saved. A designer sees the suggestion, changes what they disagree
    with, and saves; a model never decides a client's colours.
    """
    if sha is None:
        sha = await newest_logo(ctx, client["id"])
        if sha is None:
            raise DraftError(f"{client['name']} has no logo yet. Upload one first.")
    else:
        found = await ctx.supa.select(
            "assets", client_id=f"eq.{client['id']}", sha256=f"eq.{sha}", kind="eq.logo"
        )
        if not found:
            raise DraftError(f"That file is not one of {client['name']}'s logos.")
    data = Path(await local_path(ctx, sha)).read_bytes()
    try:
        reading = await asyncio.to_thread(read_logo, data)
    except LogoError as exc:
        raise DraftError(str(exc)) from exc
    return replace(suggest(reading, brand_of(client)), logo=sha)


def _palette_problems(palette: dict[str, str]) -> list[str]:
    problems = [f"{token} is missing" for token in missing_tokens(palette)]
    for token, value in palette.items():
        if not isinstance(value, str) or not HEX.fullmatch(value):
            problems.append(f"{token} must be a colour like #12264a")
    return problems


async def save_brand(
    ctx: Context,
    client: dict[str, Any],
    palette: dict[str, str],
    *,
    expected_version: int,
    suggested: dict[str, str] | None = None,
) -> tuple[BrandKit, tuple[Check, ...]]:
    """Save new colours as the client's next brand-kit version.

    `expected_version` is the version the designer was looking at. If someone saved
    since, nothing is written and BrandConflict says so: the second designer would
    otherwise silently undo the first one's colours.

    The database numbers the new version and keeps the old one (see the
    brand_kit_history migration). Earlier posters keep their colours; posts drafted
    from now on use these.
    """
    palette = {token: value.lower() for token, value in palette.items()}
    problems = _palette_problems(palette)
    if problems:
        raise DraftError("The palette can't be saved: " + "; ".join(problems) + ".")
    current = brand_of(client)
    if current.version != expected_version:
        raise BrandConflict(_CONFLICT)
    kit = current.model_copy(update={"palette": {**current.palette, **palette}})
    rows = await ctx.supa.update(
        "clients",
        {"brand": kit.model_dump(mode="json")},
        id=f"eq.{client['id']}",
        **{"brand->>version": f"eq.{expected_version}"},
    )
    if not rows:
        raise BrandConflict(_CONFLICT)
    saved = BrandKit.model_validate(rows[0]["brand"])
    if suggested is not None and ctx.user_id:
        # Which suggested colours a designer keeps, and which they change, is how a
        # better suggester gets trained later. Recorded now, because a decision not
        # recorded when it happened can never be recovered.
        changed = sorted(t for t in palette if suggested.get(t, "").lower() != palette[t])
        await ctx.supa.insert(
            "decision_log",
            {
                "agency_id": ctx.agency_id,
                "client_id": client["id"],
                "kind": "suggestion_accepted",
                "actor": ctx.user_id,
                "detail": {
                    "what": "brand_palette",
                    "version": saved.version,
                    "suggested": suggested,
                    "saved": palette,
                    "changed": changed,
                },
            },
        )
    return saved, check_palette(saved.palette)


_CONFLICT = (
    "Someone else changed this brand kit since you opened it. Reload to see their "
    "colours, then make your change again."
)
