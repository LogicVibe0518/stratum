"""Stratum's HTTP API, and the page a designer actually uses.

Authentication is Supabase's, and authorisation is the database's. Every request
carries the caller's own token, every query runs as that user, and row-level security
decides what they may see. This process holds no credential that can cross an agency
boundary, so there is no "admin path" to get wrong.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from stratum_spec import SCHEMA_VERSION, BrandKit
from stratum_spec.models import Aspect
from stratum_worker.app import options, reviews, service
from stratum_worker.app.settings import Settings, settings
from stratum_worker.app.supa import Supa, SupabaseError, close_pool
from stratum_worker.assets.errors import (
    AssetError,
    FileTooLarge,
    ImageTooLarge,
    UnsupportedFileType,
)
from stratum_worker.brand import Check, Suggestion, check_palette, is_starter
from stratum_worker.design import PRESETS, is_design
from stratum_worker.design.choose import AUTO
from stratum_worker.draft.bindings import BINDINGS
from stratum_worker.draft.compose import ComposeError
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.errors import NoProviderAvailable
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import build_router
from stratum_worker.gateway.tasks import TaskName
from stratum_worker.gateway.tracing import tracer_from_env
from stratum_worker.queue import jobs

SERVICE_ROLE_KEY = "SUPABASE_SERVICE_ROLE_KEY"


def forget_service_role_key() -> None:
    """Drop the service-role key from this process if it is present.

    The key belongs to the queue worker (.env.worker), never to the API. If it
    reached this process anyway - an old .env, a shell that exported it - it is
    removed before anything else runs, so no code path here can ever pick it up.

    Be clear about what this buys. On one laptop the same user can read both env
    files, so this is hygiene, not a security boundary. It BECOMES a boundary when
    the API and the worker deploy as two services with separate environment config:
    the API's environment then simply never contains the key. Calling this at
    import keeps the API honest about that today, and
    test_service_key_is_not_in_the_api_environment keeps the call here. Do not
    "simplify" it away.
    """
    os.environ.pop(SERVICE_ROLE_KEY, None)


forget_service_role_key()

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    missing = [task for task, names in provider_coverage().items() if not names]
    if missing:
        logging.getLogger("stratum").warning(
            "no AI provider is configured for: %s. Drafting and plain-English tweaks "
            "will fail until an API key is set - see .env.example.",
            ", ".join(missing),
        )
    yield
    await close_pool()


app = FastAPI(title="Stratum", version="1.0.0", lifespan=lifespan)

_gateway: Gateway | None = None


def gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        config = load_config()
        _gateway = Gateway(router=build_router(config), tracer=tracer_from_env())
    return _gateway


def config() -> Settings:
    return settings()


async def supa(
    authorization: Annotated[str, Header()] = "",
    conf: Settings = Depends(config),
) -> Supa:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "sign in first")
    if not conf.configured:
        raise HTTPException(503, "the server has no Supabase configuration")
    return Supa(
        base_rest=conf.rest_url,
        base_storage=conf.storage_url,
        api_key=conf.supabase_key,
        access_token=token,
        bucket=conf.bucket,
    )


async def context(
    client: Supa = Depends(supa), conf: Settings = Depends(config)
) -> service.Context:
    me = await client.rpc("me")
    if not isinstance(me, dict) or not me.get("agency_id"):
        raise HTTPException(403, "you are not in an agency yet")
    return service.Context(
        supa=client,
        settings=conf,
        gateway=gateway(),
        agency_id=str(me["agency_id"]),
        user_id=str(me.get("user_id") or "") or None,
    )


@app.exception_handler(SupabaseError)
async def _supabase_error(_: Any, exc: SupabaseError) -> Response:
    # 401/403 from the database means row-level security refused. That is the system
    # working, so it is passed through rather than dressed up as a server error.
    status = exc.status if exc.status in (401, 403, 404, 409) else 502
    return Response(content=exc.detail[:300], status_code=status, media_type="text/plain")


@app.exception_handler(DraftError)
@app.exception_handler(ComposeError)
async def _draft_error(_: Any, exc: Exception) -> Response:
    return Response(content=str(exc)[:500], status_code=422, media_type="text/plain")


@app.exception_handler(service.BrandConflict)
async def _brand_conflict(_: Any, exc: service.BrandConflict) -> Response:
    return Response(content=str(exc)[:300], status_code=409, media_type="text/plain")


@app.exception_handler(NoProviderAvailable)
async def _no_provider(_: Any, exc: NoProviderAvailable) -> Response:
    """503, not 500: the server is fine, its configuration is not."""
    return Response(content=str(exc)[:400], status_code=503, media_type="text/plain")


def provider_coverage() -> dict[str, list[str]]:
    """Which AI tasks actually have something configured to serve them."""
    router = gateway().router
    return {
        str(task): sorted(
            name for name, provider in router.providers.items() if provider.supports(task)
        )
        for task in TaskName
    }


@app.get("/healthz")
def healthz() -> dict[str, object]:
    coverage = provider_coverage()
    missing = sorted(task for task, names in coverage.items() if not names)
    return {
        "status": "degraded" if missing else "ok",
        "spec_schema_version": SCHEMA_VERSION,
        "templates": sorted([*BINDINGS, *PRESETS]),
        "supabase": settings().configured,
        "ai_tasks": coverage,
        # Named rather than implied: a server with no providers used to start
        # cheerfully and only admit it when somebody typed a tweak.
        "unserved_tasks": missing,
    }


@app.get("/api/config")
def public_config(conf: Settings = Depends(config)) -> dict[str, str]:
    """What the browser needs to sign in. The publishable key is meant to be public."""
    return {"url": conf.supabase_url, "key": conf.supabase_key}


class Onboard(BaseModel):
    agency_name: str = Field(min_length=1, max_length=200)


@app.post("/api/onboard")
async def onboard(body: Onboard, client: Supa = Depends(supa)) -> dict[str, Any]:
    agency_id = await client.rpc("onboard", {"agency_name": body.agency_name})
    return {"agency_id": agency_id}


@app.get("/api/me")
async def me(client: Supa = Depends(supa)) -> Any:
    return await client.rpc("me")


# --- clients ----------------------------------------------------------------------


class NewClient(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    trade: str = ""
    # Leave out to start with plain charcoal-and-grey colours until the logo is read.
    brand: dict[str, Any] | None = None
    rules: dict[str, Any]


@app.get("/api/clients")
async def list_clients(ctx: service.Context = Depends(context)) -> list[dict[str, Any]]:
    return await ctx.supa.select(
        "clients", archived="eq.false", select="id,slug,name,trade,created_at", order="name"
    )


@app.post("/api/clients")
async def create_client(body: NewClient, ctx: service.Context = Depends(context)) -> dict[str, Any]:
    if body.brand is not None:
        try:
            BrandKit.model_validate(body.brand)
        except ValidationError as exc:
            raise HTTPException(422, "that brand kit is not usable: " + str(exc)[:200]) from exc
        rows = await ctx.supa.insert(
            "clients",
            {"agency_id": ctx.agency_id, **body.model_dump()},
            upsert=True,
            on_conflict="agency_id,slug",
        )
        return rows[0]
    # Without a brand kit, making a client that already exists opens it. It must not
    # reset their colours to the starter kit because someone typed the name again.
    existing = await ctx.supa.select("clients", slug=f"eq.{body.slug}")
    if existing:
        return existing[0]
    starter = service.starter_kit(body.slug).model_dump(mode="json")
    rows = await ctx.supa.insert(
        "clients", {"agency_id": ctx.agency_id, **body.model_dump(), "brand": starter}
    )
    return rows[0]


@app.get("/api/clients/{client_id}/assets")
async def list_assets(
    client_id: str, ctx: service.Context = Depends(context)
) -> list[dict[str, Any]]:
    rows = await ctx.supa.select(
        "assets",
        client_id=f"eq.{client_id}",
        select="id,sha256,kind,width,height,flags,storage_path",
    )
    for row in rows:
        row["url"] = await ctx.supa.signed_url(row["storage_path"], 3600)
    return rows


@app.post("/api/clients/{client_id}/assets")
async def upload_asset(
    client_id: str,
    file: UploadFile,
    kind: Annotated[str, Form()] = "photo",
    ctx: service.Context = Depends(context),
) -> dict[str, Any]:
    if kind not in ("photo", "logo"):
        raise HTTPException(400, "kind must be photo or logo")
    name = file.filename or "That file"
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} is over the 25 MB limit.")
    try:
        sha = await service.store_image(ctx, client_id, data, kind=kind)
    except AssetError as exc:
        # Intake's own messages name a temporary file; say it with the name the
        # person actually chose, and say what to do next.
        raise HTTPException(*_upload_refusal(name, exc)) from exc
    rows = await ctx.supa.select("assets", sha256=f"eq.{sha}", select="*")
    return rows[0] if rows else {"sha256": sha}


MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _upload_refusal(name: str, exc: AssetError) -> tuple[int, str]:
    if isinstance(exc, UnsupportedFileType):
        if "HEIC" in str(exc):
            return 415, f"{name} is an iPhone HEIC photo. Export it as JPEG, then upload that."
        return 415, f"{name} isn't a JPEG, PNG, WebP or AVIF image."
    if isinstance(exc, FileTooLarge):
        return 413, f"{name} is over the 25 MB limit."
    if isinstance(exc, ImageTooLarge):
        return 413, f"{name} is bigger than 8000 x 8000 pixels. Resize it and try again."
    return 422, f"{name} looks damaged and won't open. Save it again as a JPEG and retry."


# --- brand kits -------------------------------------------------------------------


class BrandSave(BaseModel):
    palette: dict[str, str]
    # The version the designer was looking at. If someone saved since, this is a 409.
    version: int = Field(ge=1)
    # The suggestion they started from, if any, so what they changed is recorded.
    suggested: dict[str, str] | None = None


def _checks(checks: tuple[Check, ...]) -> list[dict[str, Any]]:
    return [{"what": c.what, "ratio": c.ratio, "needs": c.needs, "ok": c.ok} for c in checks]


def _suggestion(made: Suggestion) -> dict[str, Any]:
    return {
        "kit": made.kit.model_dump(mode="json"),
        "tokens": {
            token: {"hex": c.hex, "source": c.source, "why": c.why}
            for token, c in made.tokens.items()
        },
        "checks": _checks(made.checks),
        "notes": list(made.notes),
        "swatches": [{"hex": s.hex, "share": s.share} for s in made.reading.swatches],
        "tone": made.reading.tone,
        "logo": made.logo,
    }


async def _client(ctx: service.Context, client_id: str) -> dict[str, Any]:
    rows = await ctx.supa.select("clients", id=f"eq.{client_id}")
    if not rows:
        raise HTTPException(404, "no such client")
    return rows[0]


@app.get("/api/clients/{client_id}/brand")
async def get_brand(client_id: str, ctx: service.Context = Depends(context)) -> dict[str, Any]:
    """The client's colours now, how readable they are, and earlier versions."""
    client = await _client(ctx, client_id)
    kit = service.brand_of(client)
    history = await ctx.supa.select(
        "brand_kits",
        client_id=f"eq.{client_id}",
        select="version,created_at",
        order="version.desc",
        limit="20",
    )
    return {
        "kit": kit.model_dump(mode="json"),
        "starter": is_starter(kit),
        "checks": _checks(check_palette(kit.palette)),
        "history": history,
    }


@app.get("/api/clients/{client_id}/brand/suggestion")
async def suggest_brand(
    client_id: str, logo: str | None = None, ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    """Colours read from the newest logo (or the one named). Nothing is saved."""
    client = await _client(ctx, client_id)
    return _suggestion(await service.brand_suggestion(ctx, client, logo))


@app.put("/api/clients/{client_id}/brand")
async def save_brand(
    client_id: str, body: BrandSave, ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    """Save a palette as the client's next brand-kit version. Old posters keep theirs."""
    client = await _client(ctx, client_id)
    kit, checks = await service.save_brand(
        ctx, client, body.palette, expected_version=body.version, suggested=body.suggested
    )
    return {"kit": kit.model_dump(mode="json"), "checks": _checks(checks)}


# --- posts ------------------------------------------------------------------------


class NewPost(BaseModel):
    client_id: str
    brief: str = Field(min_length=1, max_length=2000)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,78}$")
    template_id: str = "offer_4x5"
    aspects: list[str] = Field(default_factory=lambda: ["4:5", "1:1", "9:16", "16:9"])
    photo_note: str = ""
    occasion: str = ""


class Tweak(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


async def _post_and_client(
    ctx: service.Context, post_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    posts = await ctx.supa.select("posts", id=f"eq.{post_id}", select="*")
    if not posts:
        raise HTTPException(404, "no such post")
    clients = await ctx.supa.select("clients", id=f"eq.{posts[0]['client_id']}", select="*")
    if not clients:
        raise HTTPException(404, "no such client")
    return posts[0], clients[0]


@app.get("/api/posts")
async def list_posts(
    client_id: str | None = None, ctx: service.Context = Depends(context)
) -> list[dict[str, Any]]:
    filters = {
        "select": "id,slug,brief,template_id,status,cost_usd,created_at,client_id",
        "order": "created_at.desc",
    }
    if client_id:
        filters["client_id"] = f"eq.{client_id}"
    return await ctx.supa.select("posts", **filters)


@app.post("/api/posts")
async def create_post(
    body: NewPost, queue: bool = False, ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    if (
        body.template_id != AUTO
        and body.template_id not in BINDINGS
        and not is_design(body.template_id)
    ):
        raise HTTPException(400, f"unknown design {body.template_id!r}")
    body = await _own_slug(body, ctx)
    if queue:
        return await _enqueue_post(body, ctx)
    rows = await ctx.supa.insert(
        "posts",
        {
            "agency_id": ctx.agency_id,
            "client_id": body.client_id,
            "slug": body.slug,
            "brief": body.brief,
            "template_id": body.template_id,
            "aspects": body.aspects,
            "photo_note": body.photo_note,
            "occasion": body.occasion,
            "created_by": ctx.user_id,
        },
    )
    post = rows[0]
    clients = await ctx.supa.select("clients", id=f"eq.{body.client_id}", select="*")
    if not clients:
        raise HTTPException(404, "no such client")
    version = await service.draft(ctx, post, clients[0])
    return {"post": post, "version": version}


def _same_request(post: dict[str, Any], body: NewPost) -> bool:
    return (
        str(post.get("brief", "")).strip() == body.brief.strip()
        and post.get("template_id") == body.template_id
        and list(post.get("aspects") or []) == list(body.aspects)
    )


async def _own_slug(body: NewPost, ctx: service.Context) -> NewPost:
    """A different post gets its own short name, even when it opens the same way.

    The page names a post from the first four words of its brief, and the queue's
    idempotency key is built from that name. So a second post starting "an enriching
    journey of" - a new design, a new brief - quietly opened the first one (live,
    29 Sep). The same request sent twice still lands on the same post: that is what
    the key is for. Anything else gets the next free name: "...-2", "...-3".
    """
    base = body.slug[:74]  # the pattern allows 79 characters; leave room for "-99"
    for n in range(1, 100):
        slug = body.slug if n == 1 else f"{base}-{n}"
        rows = await ctx.supa.select(
            "posts",
            client_id=f"eq.{body.client_id}",
            slug=f"eq.{slug}",
            select="brief,template_id,aspects",
        )
        if not rows or _same_request(rows[0], body):
            return body if slug == body.slug else body.model_copy(update={"slug": slug})
    raise HTTPException(409, f"Too many posts start '{body.slug}'. Give this one a short name.")


async def _enqueue_post(body: NewPost, ctx: service.Context) -> dict[str, Any]:
    """Create the post and queue its draft. Returns at once; a worker does the rest.

    Idempotent on client + slug: sending the same post twice returns the same job -
    and if that job has given up, sending it again is how you retry it. The unique
    (client_id, slug) on posts is what settles a race between two identical requests:
    the loser gets a 409 from the database and returns the winner's job instead.
    """
    key = jobs.idempotency_key(body.client_id, body.slug)
    existing = await jobs.find(ctx.supa, ctx.agency_id, key)
    if existing is not None:
        # The same post twice returns the same job - unless that job gave up, in
        # which case sending it again is a retry and it goes back in the queue. The
        # alternative is that "upload a logo and make the post again" can never work,
        # because the key is taken and the answer is always the old failure.
        if jobs.describe(existing, datetime.now(UTC))["status"] == "failed":
            await jobs.requeue(ctx.supa, str(existing["id"]))
        return {"job_id": existing["id"]}
    clients = await ctx.supa.select("clients", id=f"eq.{body.client_id}", select="id")
    if not clients:
        raise HTTPException(404, "no such client")
    try:
        rows = await ctx.supa.insert(
            "posts",
            {
                "agency_id": ctx.agency_id,
                "client_id": body.client_id,
                "slug": body.slug,
                "brief": body.brief,
                "template_id": body.template_id,
                "aspects": body.aspects,
                "photo_note": body.photo_note,
                "occasion": body.occasion,
                "created_by": ctx.user_id,
            },
        )
        post_id = str(rows[0]["id"])
    except SupabaseError as exc:
        if exc.status != 409:
            raise
        raced = await jobs.find(ctx.supa, ctx.agency_id, key)
        if raced is not None:
            return {"job_id": raced["id"]}
        # The post exists but its job does not: an earlier request died between the
        # two inserts. Queue it now - unless that post was already drafted inline,
        # in which case the slug really is taken.
        same = await ctx.supa.select(
            "posts", client_id=f"eq.{body.client_id}", slug=f"eq.{body.slug}", select="id"
        )
        drafted = same and await ctx.supa.select(
            "post_versions", post_id=f"eq.{same[0]['id']}", select="id", limit="1"
        )
        if not same or drafted:
            raise
        post_id = str(same[0]["id"])
    job = await jobs.enqueue(
        ctx.supa,
        agency_id=ctx.agency_id,
        kind=jobs.DRAFT_POST,
        # No requester in the payload: the database stamps jobs.requested_by from the
        # caller's token, so it cannot name someone else (migration 20260927174950).
        payload={"post_id": post_id},
        key=key,
    )
    return {"job_id": job["id"]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, ctx: service.Context = Depends(context)) -> dict[str, Any]:
    """Status, attempts, last error and result. Row-level security scopes the lookup."""
    rows = await ctx.supa.select("jobs", id=f"eq.{job_id}", select="*")
    if not rows:
        raise HTTPException(404, "no such job")
    return jobs.describe(rows[0], datetime.now(UTC))


@app.get("/api/posts/{post_id}")
async def get_post(post_id: str, ctx: service.Context = Depends(context)) -> dict[str, Any]:
    post, client = await _post_and_client(ctx, post_id)
    versions, edits, choices = await asyncio.gather(
        ctx.supa.select(
            "post_versions",
            post_id=f"eq.{post_id}",
            select="id,version,note,created_at",
            order="version.desc",
        ),
        ctx.supa.select(
            "edits",
            post_id=f"eq.{post_id}",
            select="message,outcome,latency_ms,created_at",
            order="created_at.desc",
            limit="20",
        ),
        # The three options of a "best three" draft (M12b); empty otherwise.
        options.latest_options(ctx, post_id),
    )
    latest = versions[0] if versions else None
    renders: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    if latest:
        # These three do not depend on each other, and each is a round trip to
        # another continent. Sequentially they were most of what this page cost.
        renders, flags = await asyncio.gather(
            ctx.supa.select("renders", post_version_id=f"eq.{latest['id']}", order="aspect"),
            ctx.supa.select("flags", post_version_id=f"eq.{latest['id']}"),
        )
        for row in renders:
            row["url"] = f"/previews/{latest['id']}/{row['aspect'].replace(':', 'x')}.webp"
    return {
        "post": post,
        "client": {"id": client["id"], "name": client["name"], "slug": client["slug"]},
        "versions": versions,
        "latest": latest,
        "renders": renders,
        "flags": flags,
        "edits": edits,
        "options": choices,
    }


@app.post("/api/posts/{post_id}/tweak")
async def tweak_post(
    post_id: str, body: Tweak, ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    post, client = await _post_and_client(ctx, post_id)
    return await service.tweak(ctx, post, client, body.message)


@app.get("/api/quick")
def quick_actions() -> dict[str, str]:
    """Changes that need no model at all - one render and done."""
    return service.QUICK


@app.post("/api/posts/{post_id}/quick")
async def quick_change(
    post_id: str,
    action: Annotated[str, Form()],
    ctx: service.Context = Depends(context),
) -> dict[str, Any]:
    post, client = await _post_and_client(ctx, post_id)
    return await service.quick(ctx, post, client, action)


@app.post("/api/posts/{post_id}/status")
async def set_status(
    post_id: str, status: Annotated[str, Form()], ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    if status not in ("draft", "review", "approved", "archived"):
        raise HTTPException(400, "unknown status")
    rows = await ctx.supa.update("posts", {"status": status}, id=f"eq.{post_id}")
    return rows[0] if rows else {}


@app.get("/api/posts/{post_id}/download")
async def download(post_id: str, ctx: service.Context = Depends(context)) -> Response:
    post, client = await _post_and_client(ctx, post_id)
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post_id}", select="id", order="version.desc", limit="1"
    )
    if not versions:
        raise HTTPException(404, "nothing drafted yet")
    data = await service.bundle(ctx, post, client, versions[0]["id"])
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{post["slug"]}.zip"'},
    )


PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@app.get("/api/posts/{post_id}/canva.pptx")
async def canva_deck(
    post_id: str, aspect: Aspect = "4:5", ctx: service.Context = Depends(context)
) -> Response:
    """The latest version at one size, as a PowerPoint file with every layer editable.

    Canva imports it with each headline, shape, photo and the logo as its own layer.
    """
    post, client = await _post_and_client(ctx, post_id)
    if aspect not in post["aspects"]:
        raise HTTPException(404, f"this post has no {aspect} version")
    versions = await ctx.supa.select(
        "post_versions", post_id=f"eq.{post_id}", select="id", order="version.desc", limit="1"
    )
    if not versions:
        raise HTTPException(404, "nothing drafted yet")
    data = await service.export_deck(ctx, post, client, versions[0]["id"], aspect)
    name = f"{post['slug']}-{aspect.replace(':', 'x')}.pptx"
    return Response(
        content=data,
        media_type=PPTX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/previews/{version_id}/{shape}.webp")
async def preview_image(
    version_id: str, shape: str, ctx: service.Context = Depends(context)
) -> Response:
    """The picture of one version. Rendered on demand, cached forever.

    A version never changes once written, so neither does its picture - which is why
    this can be marked immutable and never revalidated. Row-level security still
    applies: the version lookup runs as the caller, so another agency's id returns
    nothing rather than someone else's poster.
    """
    aspect = shape.replace("x", ":")
    versions = await ctx.supa.select(
        "post_versions", id=f"eq.{version_id}", select="id,spec,post_id"
    )
    if not versions:
        raise HTTPException(404, "no such version")
    post, client = await _post_and_client(ctx, versions[0]["post_id"])
    data = await service.render_preview(ctx, versions[0], post, client, aspect)
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/designs")
def designs() -> list[str]:
    """ "Best three" first - Stratum lays out many designs and keeps three different
    ones (M12b) - then hand-made templates and named designs from the design space."""
    return [AUTO, *sorted([*BINDINGS, *PRESETS])]


@app.get("/api/posts/{post_id}/options")
async def post_options(post_id: str, ctx: service.Context = Depends(context)) -> list[Any]:
    """The three options of the post's newest draft, in the order they are shown.
    Empty for a post drafted from one chosen design."""
    await _post_and_client(ctx, post_id)
    return await options.latest_options(ctx, post_id)


@app.post("/api/posts/{post_id}/options/{option_id}/pick")
async def pick_option(
    post_id: str, option_id: str, ctx: service.Context = Depends(context)
) -> dict[str, Any]:
    """Make this option the poster (a new version), and record the choice. Picking the
    same option again changes nothing and records nothing new."""
    post, client = await _post_and_client(ctx, post_id)
    return await options.pick_option(ctx, post, client, option_id)


@app.get("/previews/options/{option_id}/{shape}.webp")
async def option_preview(
    option_id: str, shape: str, ctx: service.Context = Depends(context)
) -> Response:
    """The picture of one option. Like a version, an option never changes once
    written, so its picture is cached for good. Row-level security scopes the lookup."""
    rows = await ctx.supa.select("post_options", id=f"eq.{option_id}", select="id,spec,post_id")
    if not rows:
        raise HTTPException(404, "no such option")
    post, client = await _post_and_client(ctx, rows[0]["post_id"])
    data = await service.render_preview(ctx, rows[0], post, client, shape.replace("x", ":"))
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- client review links ----------------------------------------------------------
#
# Making a link runs as the designer, so row-level security decides which posts they
# may send. Opening one and deciding run as the anonymous role, through the two
# database functions in migration 20260928125400; nothing here can read a table on
# the client's behalf.

# The token travels in the URL. no-referrer stops the browser handing it to any other
# site the page talks to; no-store keeps it out of shared caches; noindex keeps a
# forwarded link out of search engines.
PRIVATE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
LINK_FAILED = "This review link has expired or is not valid. Ask your designer for a new one."


class NewReview(BaseModel):
    client_id: str
    post_ids: list[str] = Field(min_length=1, max_length=reviews.MAX_POSTS)
    days: int = Field(default=reviews.DEFAULT_DAYS, ge=reviews.MIN_DAYS, le=reviews.MAX_DAYS)
    title: str = Field(default="", max_length=200)


class Pin(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    # The format the client was looking at when they tapped, e.g. "4:5".
    aspect: str | None = Field(default=None, max_length=8)


class Decision(BaseModel):
    item_id: str
    verdict: Literal["approved", "changes"]
    comment: str = Field(default="", max_length=2000)
    pin: Pin | None = None


def anonymous(conf: Settings = Depends(config)) -> Supa:
    """A client with no user token: the database sees the anonymous role."""
    if not conf.configured:
        raise HTTPException(503, "the server has no Supabase configuration")
    return Supa(
        base_rest=conf.rest_url,
        base_storage=conf.storage_url,
        api_key=conf.supabase_key,
        access_token=conf.supabase_key,
        bucket=conf.bucket,
    )


@app.post("/api/reviews")
async def create_review(body: NewReview, ctx: service.Context = Depends(context)) -> Any:
    try:
        return await reviews.create_review(
            ctx,
            client_id=body.client_id,
            post_ids=body.post_ids,
            days=body.days,
            title=body.title,
        )
    except reviews.ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/reviews")
async def list_reviews(client_id: str, ctx: service.Context = Depends(context)) -> Any:
    """This client's review links, newest first, each marked open, expired or revoked."""
    return await reviews.list_reviews(ctx.supa, client_id)


@app.post("/api/reviews/{review_id}/revoke")
async def revoke_review(review_id: str, ctx: service.Context = Depends(context)) -> Any:
    if not await reviews.revoke(ctx.supa, review_id):
        raise HTTPException(404, "no such review, or it was already revoked")
    return {"revoked": True}


@app.get("/api/posts/{post_id}/feedback")
async def post_feedback(post_id: str, ctx: service.Context = Depends(context)) -> Any:
    """Every verdict clients have given on this post, newest first."""
    items = await ctx.supa.select(
        "review_items", post_id=f"eq.{post_id}", select="id,review_id,version_id"
    )
    found: list[dict[str, Any]] = []
    for item in items:
        decisions = await ctx.supa.select(
            "review_decisions",
            review_item_id=f"eq.{item['id']}",
            select="verdict,comment,pin,created_at",
            order="created_at.desc",
        )
        found.extend({**d, "version_id": item["version_id"]} for d in decisions)
    return sorted(found, key=lambda d: str(d.get("created_at", "")), reverse=True)


@app.get("/r/{token}")
def review_page(token: str) -> FileResponse:
    # The page itself knows nothing; it asks /api/review/{token}, which asks the database.
    return FileResponse(STATIC / "review.html", headers=PRIVATE)


@app.get("/api/review/{token}")
async def open_review(token: str, anon: Supa = Depends(anonymous)) -> Any:
    found = await reviews.open_review(anon, token)
    if found is None:
        raise HTTPException(404, LINK_FAILED, headers=PRIVATE)
    return JSONResponse(found, headers=PRIVATE)


@app.post("/api/review/{token}/decide")
async def decide_review(token: str, body: Decision, anon: Supa = Depends(anonymous)) -> Any:
    try:
        result = await reviews.decide(
            anon,
            token,
            item_id=body.item_id,
            verdict=body.verdict,
            comment=body.comment,
            pin=body.pin.model_dump(exclude_none=True) if body.pin else None,
        )
    except reviews.ReviewError as exc:
        raise HTTPException(400, str(exc), headers=PRIVATE) from exc
    return JSONResponse(result, headers=PRIVATE)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
