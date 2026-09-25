"""draft_post: a brief in, a finished post out.

Seven steps, each a function of its inputs plus what earlier steps produced. The two
that cost money write their result to a checkpoint file, so a run that fails at step 6
and is started again does not pay for step 3 twice. Those step boundaries are also
what a progress report is built from later - "60% done" can only be honest if there
are real milestones to be 60% through.

DBOS replaces the checkpoint file when the database lands: @DBOS.step on each step,
@DBOS.workflow on draft_post, delete Checkpoints. The shape does not change, which is
the point of writing it this way now.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from stratum_spec.models import Aspect, PosterSpec
from stratum_spec.templates import load_template

from stratum_worker.assets.models import AssetMeta
from stratum_worker.assets.store import AssetStore
from stratum_worker.draft.bindings import Binding, binding_for
from stratum_worker.draft.clients import Client, load_client
from stratum_worker.draft.compose import build_spec
from stratum_worker.draft.models import (
    Flag,
    PostBrief,
    PostCopy,
    PostDraft,
    RenderedPoster,
    StepRecord,
)
from stratum_worker.draft.preflight import check_copy
from stratum_worker.gateway.gateway import Gateway, NullTracer, Tracer
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import (
    BackgroundRequest,
    PostCopyRequest,
    PostCopyResult,
    TaskName,
)
from stratum_worker.render import render_poster

# A photo with any of these is not fit to build a post around. The others
# (colour_profile_ignored) are worth telling a human about, not refusing over.
UNUSABLE = frozenset({"too_blurry", "too_dark", "too_small"})


class DraftError(RuntimeError):
    """The draft could not be produced at all. Everything softer is a Flag."""


# --- provenance ------------------------------------------------------------------


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    task: str
    provider: str
    outcome: str
    model: str
    cost_usd: float
    latency_ms: int


@dataclass
class CollectingTracer:
    """Wraps the real tracer and keeps every call record for this draft.

    The gateway already reports model, cost and latency to a tracer; reading them here
    costs nothing and means the draft can say exactly which model wrote its headline
    and what that cost, without the gateway knowing anything about drafts.
    """

    inner: Tracer = field(default_factory=NullTracer)
    records: list[CallRecord] = field(default_factory=list)

    def record(
        self,
        *,
        run_id: str,
        task: TaskName,
        provider: str,
        call: ProviderCall | None,
        outcome: str,
        agency_id: str,
        error: str | None = None,
    ) -> None:
        self.records.append(
            CallRecord(
                run_id=run_id,
                task=str(task),
                provider=provider,
                outcome=outcome,
                model=call.model if call else "",
                cost_usd=call.cost_usd if call else 0.0,
                latency_ms=call.latency_ms if call else 0,
            )
        )
        self.inner.record(
            run_id=run_id,
            task=task,
            provider=provider,
            call=call,
            outcome=outcome,
            agency_id=agency_id,
            error=error,
        )

    def step(self, name: str, task: TaskName) -> StepRecord:
        """Fold every attempt of one task into one line of provenance."""
        mine = [r for r in self.records if r.task == str(task)]
        if not mine:
            return StepRecord(step=name)
        ok = next((r for r in mine if r.outcome == "ok"), mine[-1])
        return StepRecord(
            step=name,
            model=ok.model,
            provider=ok.provider,
            cost_usd=round(sum(r.cost_usd for r in mine), 6),
            latency_ms=sum(r.latency_ms for r in mine),
            run_id=ok.run_id,
        )


# --- checkpoints -----------------------------------------------------------------


class Checkpoints:
    """Step results on disk, so a re-run is free where it can be.

    Deliberately dumb: one JSON file per draft. Its whole job is to make the expensive
    steps idempotent, and DBOS takes the job over later.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, object] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self._data = loaded if isinstance(loaded, dict) else {}
            except ValueError:
                self._data = {}  # a corrupt checkpoint file just means redo the work

    def get(self, step: str) -> dict[str, object] | None:
        value = self._data.get(step)
        return value if isinstance(value, dict) else None

    def put(self, step: str, value: dict[str, object]) -> None:
        self._data[step] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(self._data, file, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


# --- the steps -------------------------------------------------------------------


@dataclass
class Deps:
    gateway: Gateway
    store: AssetStore
    templates_dir: Path
    repo_root: Path
    clients_root: Path
    agency_id: str = "nimble"


def ingest_photos(client: Client, deps: Deps) -> tuple[list[AssetMeta], list[Flag]]:
    """Put every photo in the client's folder through intake, best first.

    Ordering by sharpness means the background gets their best picture without anyone
    choosing, which removes a decision from the designer's day.
    """
    metas: list[AssetMeta] = []
    flags: list[Flag] = []
    for path in client.photos:
        try:
            meta = deps.store.put(path)
        except ValueError as exc:
            flags.append(
                Flag(
                    code="photo_rejected",
                    message=f"{path.name} could not be read: {exc}",
                )
            )
            continue
        metas.append(meta)
        for flag in meta.flags:
            if flag in UNUSABLE:
                flags.append(
                    Flag(
                        code=f"photo_{flag}",
                        message=f"{path.name} is {flag.replace('_', ' ')} and was not used.",
                    )
                )
    usable = [m for m in metas if not (UNUSABLE & set(m.flags))]
    usable.sort(key=lambda m: m.quality.get("sharpness", 0.0), reverse=True)
    return usable, flags


async def write_copy(
    brief: PostBrief,
    client: Client,
    binding: Binding,
    deps: Deps,
    checkpoints: Checkpoints,
) -> tuple[PostCopy, bool]:
    """The one model call every post makes. Cached, because it costs money."""
    saved = checkpoints.get("write_copy")
    if saved is not None:
        return PostCopy.model_validate(saved), True

    capacity = binding.capacity
    request = PostCopyRequest(
        brief=brief.brief if not brief.occasion else f"{brief.brief}\nOccasion: {brief.occasion}",
        client_name=client.name,
        trade=client.trade,
        language="en-US" if client.rules.areas and _is_us(client) else "en-GB",
        platform=brief.platform,
        services=client.rules.services,
        areas=client.rules.areas,
        allowed_hashtags=client.rules.allowed_hashtags,
        max_hashtags=client.rules.max_hashtags,
        must_include=client.rules.must_include,
        banned_claims=client.rules.banned_claims,
        photo_description=brief.photo_description,
        headline_max_chars=capacity.get("headline", capacity.get("quote", 60)),
        offer_max_chars=capacity.get("offer", capacity.get("quote", 120)),
        cta_max_chars=capacity.get("cta", capacity.get("contact", capacity.get("attribution", 40))),
    )
    result = await deps.gateway.run(TaskName.POST_COPY, request, agency_id=deps.agency_id)
    assert isinstance(result, PostCopyResult)
    copy = PostCopy(
        headline=result.headline,
        offer_detail=result.offer_detail,
        cta=result.cta,
        caption=result.caption,
        hashtags=result.hashtags,
        alt_text=result.alt_text,
    )
    checkpoints.put("write_copy", copy.model_dump())
    return copy, False


def _is_us(client: Client) -> bool:
    return any(
        marker in " ".join(client.rules.areas)
        for marker in ("San Antonio", "Tampa", "Bexar", "Hillsborough")
    )


async def make_background(
    brief: PostBrief,
    client: Client,
    deps: Deps,
    checkpoints: Checkpoints,
) -> tuple[str, bool]:
    """Only reached when the client sent no usable photo.

    The normal path uses their own picture - that is what they send, and it is free.
    Generating is the fallback, not the product.
    """
    saved = checkpoints.get("make_background")
    if saved is not None and isinstance(saved.get("sha256"), str):
        return str(saved["sha256"]), True

    trade = client.trade.replace("_", " ") or "local trade"
    request = BackgroundRequest(
        prompt=(
            f"A clean, realistic photograph of a {trade} work setting in a style "
            f"suitable as a poster background. Plain, uncluttered, nothing in the "
            f"lower half. Absolutely no words, letters, numbers, logos or signage."
        ),
        aspect=brief.aspects[0],
    )
    result = await deps.gateway.run(TaskName.BACKGROUND_CREATE, request, agency_id=deps.agency_id)
    png = getattr(result, "image_png", None)
    if not isinstance(png, bytes):
        raise DraftError("the background service returned something that is not an image")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(png)
        temp = Path(handle.name)
    try:
        meta = deps.store.put(temp, source="generated")
    finally:
        temp.unlink(missing_ok=True)
    checkpoints.put("make_background", {"sha256": meta.sha256})
    return meta.sha256, False


# --- the pipeline ----------------------------------------------------------------


def _aspect_suffix(aspect: Aspect) -> str:
    return aspect.replace(":", "x")


def _render_all(
    brief: PostBrief,
    client: Client,
    template: PosterSpec,
    texts: dict[str, str],
    assets: dict[str, str],
    asset_paths: dict[str, str],
    deps: Deps,
) -> tuple[RenderedPoster, ...]:
    """One spec and one PNG per aspect, from one set of words.

    The model is not called again here. That is the whole cost argument for shipping
    four formats: the words and the pictures are already decided, and a render is
    arithmetic.
    """
    out = client.out_dir(brief.slug)
    out.mkdir(parents=True, exist_ok=True)
    posters: list[RenderedPoster] = []
    for aspect in brief.aspects:
        spec = build_spec(template, client.kit, texts, assets, aspect=aspect)
        png = render_poster(spec, client.kit, assets=asset_paths, root=deps.repo_root)
        suffix = _aspect_suffix(aspect)
        png_path = out / f"poster-{suffix}.png"
        spec_path = out / f"spec-{suffix}.json"
        png_path.write_bytes(png)
        spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        width, height = spec.canvas.size
        posters.append(
            RenderedPoster(
                aspect=aspect,
                png_path=png_path,
                spec_path=spec_path,
                width=width,
                height=height,
            )
        )
    return tuple(posters)


async def draft_post(brief: PostBrief, deps: Deps) -> PostDraft:
    """Brief in, finished post out. See docs/modules/M7-draft-post.md."""
    client = load_client(deps.clients_root, brief.client_id)
    binding = binding_for(brief.template_id)
    template = load_template(deps.templates_dir / f"{brief.template_id}.json")

    out = client.out_dir(brief.slug)
    checkpoints = Checkpoints(out / ".checkpoints.json")
    tracer = CollectingTracer(inner=deps.gateway.tracer)
    deps.gateway.tracer = tracer

    flags: list[Flag] = []
    steps: list[StepRecord] = []

    usable, photo_flags = ingest_photos(client, deps)
    flags.extend(photo_flags)
    steps.append(StepRecord(step="ingest_photos"))

    copy, cached = await write_copy(brief, client, binding, deps, checkpoints)
    steps.append(
        StepRecord(step="write_copy", cached=True)
        if cached
        else tracer.step("write_copy", TaskName.POST_COPY)
    )

    # Photos to layers, best picture first. A template wanting two photos and given
    # one is a blocker: we will not invent a "before".
    assets: dict[str, str] = {}
    photo_layers = list(binding.photos)
    if len(usable) < len(photo_layers):
        background_only = photo_layers == ["bg"]
        if background_only:
            sha, cached_bg = await make_background(brief, client, deps, checkpoints)
            assets["bg"] = sha
            steps.append(
                StepRecord(step="make_background", cached=True)
                if cached_bg
                else tracer.step("make_background", TaskName.BACKGROUND_CREATE)
            )
            flags.append(
                Flag(
                    code="generated_background",
                    message=(
                        "No usable photo, so the background was generated. Swap in a "
                        "real photo when they send one - it always looks better."
                    ),
                )
            )
        else:
            raise DraftError(
                f"{brief.template_id} needs {len(photo_layers)} photos "
                f"({', '.join(photo_layers)}) and {client.id} has {len(usable)} usable. "
                f"Add photos to {client.folder / 'photos'} or pick another design."
            )
    for layer_id, meta in zip(photo_layers, usable, strict=False):
        assets[layer_id] = meta.sha256

    if binding.logo:
        logo = client.logo
        if logo is None:
            raise DraftError(
                f"{client.name} has no logo file. Put one at {client.folder / 'logo.png'} - "
                f"we will not invent branding."
            )
        assets[binding.logo] = deps.store.put(logo, source="client").sha256

    texts = {layer: getattr(copy, attribute) for layer, attribute in binding.texts.items()}
    texts.update(binding.fixed or {})

    asset_paths = {
        sha: str(deps.store.path_for(sha, "full").resolve()) for sha in set(assets.values())
    }
    posters = _render_all(brief, client, template, texts, assets, asset_paths, deps)
    steps.append(StepRecord(step="render"))

    flags.extend(check_copy(copy, client.rules, brief.brief))
    steps.append(StepRecord(step="preflight"))

    draft = PostDraft(
        client_id=client.id,
        slug=brief.slug,
        template_id=brief.template_id,
        words=copy,
        assets=assets,
        posters=posters,
        flags=tuple(flags),
        steps=tuple(steps),
    )
    (out / "caption.txt").write_text(
        copy.caption + ("\n\n" + " ".join(copy.hashtags) if copy.hashtags else "") + "\n",
        encoding="utf-8",
    )
    (out / "draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    return draft


__all__ = ["Checkpoints", "CollectingTracer", "Deps", "DraftError", "draft_post"]
