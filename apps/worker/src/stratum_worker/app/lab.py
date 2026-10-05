"""The Taste Lab (taste plan, step 1): people pick between good designs; that is taste.

Screens are made from the agency's own posts: the post's words, the client's photos
and logo, laid out by the design space and ranked as for the top three, then four
designs per screen (design/lab.py). A rater sees one screen at a time, picks the one
they would send the client (or none), ticks any they would never send, and may add a
reason. One click is three comparisons and some defect labels, in about eight seconds.

How the labels are kept honest:
- shared screens are shown to every rater, so agreement between people - the ceiling
  any model can reach - is measured, not assumed;
- one answer in ten is a repeat of a screen the rater already answered, so each
  rater's self-consistency is measured too;
- the order on screen is shuffled per rater and stored, so a "first one" habit can be
  found and removed;
- nothing about a design (family, rank, score) is shown.

Screens are made by a job (slow: a ranking per photo set per post). Rating is quick
and runs in the request. Answers are decision_log rows of kind 'designer_pick' with
source 'lab'.

What a screen is made of (design/lab.py, chooser 2, 5 Oct): designs that look
different at a glance, top picks rotating through the families, and - when the client
has more than two photos - alternate screens laid out around the client's next-best
photos, so the lab sees more than one picture. Screens from chooser 1 (near-twins on
one photo) are no longer served; their answers stay in the export, marked.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import random
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from stratum_spec import BrandKit, ImageLayer
from stratum_spec import load as load_spec

from stratum_worker.app import service
from stratum_worker.app.options import _edges as edges_of
from stratum_worker.app.options import material_for
from stratum_worker.critic.features import measure
from stratum_worker.critic.taste import ROLES
from stratum_worker.design.choose import (
    Candidate,
    Material,
    NothingFits,
    candidates,
    seed_for,
)
from stratum_worker.design.lab import (
    LOOK_WIDTH,
    MIN_APART,
    Look,
    Shown,
    cielab,
    closest,
    lab_screen,
)
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.render import render_poster

REPEAT_RATE = 0.1
REPEAT_AFTER = 10  # answers before repeats start
RECENT = 5  # a repeat is never one of the rater's last five screens
REASONS = (
    "Hard to read",
    "Photo badly cropped",
    "Words cover the subject",
    "Cluttered",
    "Too plain",
    "Logo looks wrong",
    "Colours feel off",
    "Looks cheap",
)
MAX_MS = 10 * 60 * 1000
CHOOSER = 2  # how screens are chosen (design/lab.py); older screens are not served
PHOTO_SETS = 2  # the best two photos, then the next two, when the client has them
# Seconds a screen-making job spends before it stops starting new posts. The job may
# run for its lease minus a margin (queue/worker.work_timeout: 150 s on the default
# lease); a post takes several seconds per photo set. Posts left over are made by the
# next press, which skips posts that already have current screens.
BUDGET_S = 100.0


def _rng(*parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))  # noqa: S311 - reproducible, not a secret


def display_order(screen_id: str, user_id: str | None, served: str, count: int) -> list[int]:
    """The order a rater saw the designs in: shuffled per rater and per serving, and
    worked out the same way when the answer comes back, so the page cannot change it."""
    order = list(range(count))
    _rng(screen_id, user_id, served).shuffle(order)
    return order


# --- making screens (a job) --------------------------------------------------------------


def looks_of(
    ranked: Sequence[Candidate], kit: BrandKit, assets: dict[str, str], root: Path
) -> list[Look]:
    """How each design looks at a glance: a LOOK_WIDTH px render, in CIELAB."""
    out: list[Look] = []
    for candidate in ranked:
        png = render_poster(candidate.spec, kit, assets=assets, root=root, width=LOOK_WIDTH)
        with Image.open(io.BytesIO(png)) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
        out.append(cielab(rgb))
    return out


def photo_sets(ranked_photos: Sequence[str]) -> list[tuple[str, ...]]:
    """The photos each ranking is laid out around: the best two, then the next two."""
    sets = [tuple(ranked_photos[n * 2 : n * 2 + 2]) for n in range(PHOTO_SETS)]
    return [one for one in sets if one]


async def _current_posts(ctx: service.Context) -> set[str]:
    """Posts that already have screens made the current way."""
    rows = await ctx.supa.select("lab_screens", select="post_id,detail")
    return {
        str(one["post_id"])
        for one in rows
        if int((one.get("detail") or {}).get("chooser", 1)) >= CHOOSER
    }


async def make_screens(
    ctx: service.Context,
    *,
    posts: int = 20,
    per_post: int = 6,
    shared_per_post: int = 3,
    budget_s: float = BUDGET_S,
) -> dict[str, Any]:
    """Screens for the agency's newest posts that have words, a client with photos, and
    no current screens yet.

    The first `shared_per_post` screens of each post are shown to every rater. Stops
    starting new posts after `budget_s` seconds; the rest are reported as `left`.
    """
    began = time.monotonic()
    rows = await ctx.supa.select("posts", order="created_at.desc", select="*")
    covered = await _current_posts(ctx)
    done, made, skipped, left = 0, 0, [], 0
    near_twins = 0
    families: Counter[str] = Counter()
    batch = str(uuid.uuid4())
    root = ctx.settings.repo_root
    for post in rows:
        if done >= posts:
            break
        words = post.get("words") or {}
        if not words.get("headline") or str(post["id"]) in covered:
            continue
        if time.monotonic() - began > budget_s:
            left += 1
            continue
        clients = await ctx.supa.select("clients", id=f"eq.{post['client_id']}", select="*")
        if not clients:
            continue
        client = clients[0]
        wanted = {f"p{n}": "photo" for n in range(2 * PHOTO_SETS)}
        picked, _ = await service.pick_photos(ctx, client["id"], wanted)
        ranked_photos = [picked[key] for key in wanted if key in picked]
        if not ranked_photos:
            skipped.append(post["slug"])
            continue
        rankings: list[
            tuple[tuple[str, ...], list[Candidate], list[Look], dict[str, str], Material]
        ] = []
        kit = service.brand_of(client)
        for photos in photo_sets(ranked_photos):
            try:
                material, asset_paths = await material_for(ctx, client, words, kit, photos, None)
                ranked = await asyncio.to_thread(
                    candidates, material, ["4:5"], root, seed=seed_for(str(post["id"]))
                )
            except (DraftError, NothingFits):
                continue
            looks = await asyncio.to_thread(looks_of, ranked, kit, asset_paths, root)
            rankings.append((photos, ranked, looks, asset_paths, material))
        if not rankings:
            skipped.append(post["slug"])
            continue
        shown = Shown.nothing()
        screens = []
        for index in range(per_post):
            photos, ranked, looks, asset_paths, material = rankings[index % len(rankings)]
            chosen = lab_screen(ranked, _rng(post["id"], batch, index), looks=looks, shown=shown)
            if chosen is None:
                break
            hardest = closest(chosen, looks)
            near_twins += int(hardest < MIN_APART)
            families.update(one.candidate.family for one in chosen)
            screens.append(
                {
                    "agency_id": ctx.agency_id,
                    "post_id": post["id"],
                    "shared": index < shared_per_post,
                    "designs": [
                        {
                            "design_id": one.candidate.design_id,
                            "family": one.candidate.family,
                            "bucket": one.bucket,
                            "rank": one.rank,
                            "score": round(one.candidate.score, 6),
                            "spec": (
                                await service.with_mattes(ctx, one.candidate.spec, asset_paths)
                            ).model_dump(mode="json"),
                            # The critic's 35 measurements, exactly as the ranking took
                            # them: what critic v2 learns from (taste.py).
                            "features": _rounded(
                                measure(one.candidate.spec, kit, root, edges=dict(material.edges))
                            ),
                        }
                        for one in chosen
                    ],
                    "detail": {
                        "batch": batch,
                        "candidates": len(ranked),
                        "index": index,
                        "chooser": CHOOSER,
                        "photos": list(photos),
                        "closest": round(hardest, 2),
                    },
                }
            )
        if screens:
            await ctx.supa.insert("lab_screens", screens)
            made += len(screens)
            done += 1
    return {
        "posts": done,
        "screens": made,
        "skipped": skipped,
        "left": left,
        "near_twins": near_twins,
        "families": dict(sorted(families.items())),
        "batch": batch,
    }


# --- rating ------------------------------------------------------------------------------


async def _answers(ctx: service.Context) -> list[dict[str, Any]]:
    return await ctx.supa.select(
        "decision_log",
        kind="eq.designer_pick",
        **{"detail->>source": "eq.lab"},
        select="id,actor,post_id,detail,created_at",
        order="created_at.asc",
    )


async def _screens(ctx: service.Context) -> list[dict[str, Any]]:
    """The screens raters are served: those made the current way."""
    rows = await ctx.supa.select(
        "lab_screens", select="id,post_id,shared,created_at,detail", order="created_at.asc"
    )
    return [one for one in rows if int((one.get("detail") or {}).get("chooser", 1)) >= CHOOSER]


def _choose(
    screens: list[dict[str, Any]], answers: list[dict[str, Any]], user: str | None
) -> tuple[dict[str, Any] | None, bool]:
    """The next screen for this rater, and whether it is a repeat."""
    mine = [one for one in answers if one.get("actor") == user]
    seen = [one["detail"]["screen"] for one in mine]
    rng = _rng(user, len(mine))
    if len(mine) >= REPEAT_AFTER and rng.random() < REPEAT_RATE:
        recent = set(seen[-RECENT:]) if RECENT else set()
        servable = {one["id"] for one in screens}
        older = sorted((set(seen) - recent) & servable)
        if older:
            screen_id = rng.choice(older)
            return next((one for one in screens if one["id"] == screen_id), None), True
    answered = Counter(
        one["detail"]["screen"] for one in answers if not one["detail"].get("repeat")
    )
    fresh = [one for one in screens if one["id"] not in set(seen)]
    shared = [one for one in fresh if one.get("shared")]
    if shared:
        _rng(user, "shared").shuffle(shared)
        return shared[0], False
    if fresh:
        fresh.sort(key=lambda one: (answered[one["id"]], one["created_at"]))
        return fresh[0], False
    return None, False


async def next_screen(ctx: service.Context) -> dict[str, Any]:
    """One screen to rate: the post's brief, the designs to show in their shuffled
    order (as picture links), the reasons to choose from, and progress."""
    screens, answers = await asyncio.gather(_screens(ctx), _answers(ctx))
    chosen, repeat = _choose(screens, answers, ctx.user_id)
    progress = summarise(screens, answers, ctx.user_id)
    if chosen is None:
        return {"done": True, "progress": progress}
    [screen] = await ctx.supa.select("lab_screens", id=f"eq.{chosen['id']}", select="*")
    posts = await ctx.supa.select("posts", id=f"eq.{screen['post_id']}", select="*")
    post = posts[0] if posts else {}
    clients = await ctx.supa.select("clients", id=f"eq.{post.get('client_id')}", select="name")
    served = str(uuid.uuid4())
    order = display_order(screen["id"], ctx.user_id, served, len(screen["designs"]))
    return {
        "done": False,
        "screen_id": screen["id"],
        "served": served,
        "repeat": repeat,
        "post": {
            "brief": post.get("brief", ""),
            "client": clients[0]["name"] if clients else "",
        },
        "cards": [
            {"design": index, "preview": f"/previews/lab/{screen['id']}/{index}/{{shape}}.webp"}
            for index in order
        ],
        "reasons": list(REASONS),
        "progress": progress,
    }


async def answer(
    ctx: service.Context,
    screen_id: str,
    *,
    served: str,
    picked: int | None,
    never: list[int],
    reasons: list[str],
    ms: int,
    repeat: bool,
    send: list[int] | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Record one rater's choice on one screen. The same serving sent twice is one
    answer. Raises DraftError with a sentence when the answer cannot be right.

    `send` (M15c, issue I-31) is every design the rater would send as it is; None
    from a page that does not ask. The best one is always one they would send, so a
    pick is added to it; ticking several without saying which is best is refused.
    `role` is what the rater says they are (ROLES): designers' answers are the answer
    key critic v2 is scored against."""
    rows = await ctx.supa.select("lab_screens", id=f"eq.{screen_id}", select="*")
    if not rows:
        raise DraftError("That screen is not one of this agency's.")
    screen = rows[0]
    count = len(screen["designs"])
    try:
        uuid.UUID(served)
    except ValueError as exc:
        raise DraftError("That answer does not match a screen that was shown.") from exc
    if picked is not None and not 0 <= picked < count:
        raise DraftError("The picked design is not on this screen.")
    if any(not 0 <= one < count for one in never) or len(set(never)) != len(never):
        raise DraftError("A design marked 'never send' is not on this screen.")
    if picked is not None and picked in never:
        raise DraftError("A design cannot be both the pick and 'never send'.")
    if send is not None:
        if any(not 0 <= one < count for one in send) or len(set(send)) != len(send):
            raise DraftError("A design marked 'would send' is not on this screen.")
        if set(send) & set(never):
            raise DraftError("A design cannot be both 'would send' and 'never send'.")
        if picked is None and len(send) == 1:
            picked = send[0]  # the only one they would send is the best
        elif picked is None and send:
            raise DraftError("Pick the best of the designs you would send.")
        send = sorted({*send, *([picked] if picked is not None else [])})
    if role is not None and role not in ROLES:
        raise DraftError(f"Unknown role: {role}.")
    unknown = sorted(set(reasons) - set(REASONS))
    if unknown:
        raise DraftError(f"Unknown reason: {', '.join(unknown)}.")
    if not 0 <= ms <= MAX_MS:
        raise DraftError("That answer took an impossible amount of time.")
    mine = await ctx.supa.select(
        "decision_log",
        kind="eq.designer_pick",
        actor=f"eq.{ctx.user_id}",
        **{"detail->>served": f"eq.{served}"},
        select="id",
    )
    if not mine:
        posts = await ctx.supa.select("posts", id=f"eq.{screen['post_id']}", select="client_id")
        await ctx.supa.insert(
            "decision_log",
            {
                "agency_id": ctx.agency_id,
                "client_id": posts[0]["client_id"] if posts else None,
                "post_id": screen["post_id"],
                "version_id": None,
                "kind": "designer_pick",
                "actor": ctx.user_id,
                "detail": {
                    "source": "lab",
                    "screen": screen_id,
                    "served": served,
                    "picked": picked,
                    "never": sorted(never),
                    "reasons": reasons,
                    "ms": ms,
                    "order": display_order(screen_id, ctx.user_id, served, count),
                    "repeat": repeat,
                    "shared": bool(screen.get("shared")),
                    "design_ids": [one["design_id"] for one in screen["designs"]],
                    **({"send": send} if send is not None else {}),
                    **({"role": role} if role is not None else {}),
                },
            },
        )
    screens, answers = await asyncio.gather(_screens(ctx), _answers(ctx))
    return {"ok": True, "progress": summarise(screens, answers, ctx.user_id)}


# --- what the labels say so far -----------------------------------------------------------


def summarise(
    screens: list[dict[str, Any]], answers: list[dict[str, Any]], user: str | None
) -> dict[str, Any]:
    """Counts, agreement between raters on shared screens, and each rater's
    self-consistency on repeats. Agreement is the share of rater pairs who picked the
    same design (or both picked none)."""
    firsts: dict[tuple[str, str], Any] = {}
    repeats_same, repeats = 0, 0
    for one in answers:
        detail, actor = one["detail"], str(one.get("actor"))
        key = (actor, detail["screen"])
        if detail.get("repeat") and key in firsts:
            repeats += 1
            repeats_same += int(firsts[key] == detail.get("picked"))
        elif key not in firsts:
            firsts[key] = detail.get("picked")
    shared_ids = {one["id"] for one in screens if one.get("shared")}
    by_screen: dict[str, list[Any]] = defaultdict(list)
    for (_, screen_id), picked in firsts.items():
        if screen_id in shared_ids:
            by_screen[screen_id].append(picked)
    pairs = agree = 0
    for picks in by_screen.values():
        for left, right in combinations(picks, 2):
            pairs += 1
            agree += int(left == right)
    raters = Counter(actor for actor, _ in firsts)
    return {
        "screens": len(screens),
        "shared_screens": len(shared_ids),
        "answers": len(answers),
        "mine": sum(1 for one in answers if one.get("actor") == user),
        "raters": len(raters),
        "agreement": {"pairs": pairs, "rate": round(agree / pairs, 3) if pairs else None},
        "self_consistency": {
            "repeats": repeats,
            "rate": round(repeats_same / repeats, 3) if repeats else None,
        },
    }


async def progress(ctx: service.Context) -> dict[str, Any]:
    screens, answers = await asyncio.gather(_screens(ctx), _answers(ctx))
    return summarise(screens, answers, ctx.user_id)


def _rounded(values: Any) -> list[float]:
    return [round(float(value), 6) for value in values]


class _Measurer:
    """The critic's measurements for designs on screens made before they were kept:
    the client's brand kit as the poster was made, and the photos' edge maps, each
    read once per export."""

    def __init__(self, ctx: service.Context) -> None:
        self.ctx = ctx
        self.kits: dict[tuple[str, int], BrandKit] = {}
        self.edges: dict[str, Any] = {}
        self.clients: dict[str, dict[str, Any]] = {}

    async def features(self, screen: dict[str, Any]) -> list[list[float]]:
        designs = screen["designs"]
        if all(isinstance(one.get("features"), list) for one in designs):
            return [one["features"] for one in designs]
        post_id = str(screen["post_id"])
        if post_id not in self.clients:
            [post] = await self.ctx.supa.select("posts", id=f"eq.{post_id}", select="client_id")
            [client] = await self.ctx.supa.select("clients", id=f"eq.{post['client_id']}")
            self.clients[post_id] = client
        client = self.clients[post_id]
        out = []
        for one in designs:
            if isinstance(one.get("features"), list):
                out.append(one["features"])
                continue
            spec = load_spec(one["spec"])
            key = (str(client["id"]), spec.brand_kit_version)
            if key not in self.kits:
                self.kits[key] = await service.kit_for(self.ctx, client, spec.brand_kit_version)
            for layer in spec.layers:
                sha = getattr(layer, "asset", None)
                if isinstance(layer, ImageLayer) and isinstance(sha, str) and sha not in self.edges:
                    path = await service.local_path(self.ctx, sha)
                    self.edges[sha] = await asyncio.to_thread(edges_of, path)
            root = self.ctx.settings.repo_root
            out.append(_rounded(measure(spec, self.kits[key], root, edges=dict(self.edges))))
        return out


async def export(ctx: service.Context) -> str:
    """Every answer with the designs it was about, one JSON object per line: what the
    critic v2 walkthrough trains on. Raters are numbered, not named. Each line carries
    `features`: the critic's measurements of each design (critic/features.FEATURES
    order), so training needs neither the photos nor the brand kits."""
    answers = await _answers(ctx)
    screens = {one["id"]: one for one in await ctx.supa.select("lab_screens", select="*")}
    measurer = _Measurer(ctx)
    measured: dict[str, list[list[float]]] = {}
    numbers: dict[str, int] = {}
    lines = []
    for one in answers:
        detail = one["detail"]
        screen = screens.get(detail["screen"])
        if screen is None:
            continue
        if screen["id"] not in measured:
            measured[screen["id"]] = await measurer.features(screen)
        rater = numbers.setdefault(str(one.get("actor")), len(numbers) + 1)
        lines.append(
            json.dumps(
                {
                    "screen": detail["screen"],
                    "post": screen["post_id"],
                    "rater": rater,
                    "you": one.get("actor") == ctx.user_id,
                    "shared": detail.get("shared", False),
                    "repeat": detail.get("repeat", False),
                    "picked": detail.get("picked"),
                    "never": detail.get("never", []),
                    "send": detail.get("send"),
                    "role": detail.get("role"),
                    "reasons": detail.get("reasons", []),
                    "ms": detail.get("ms"),
                    "order": detail.get("order", []),
                    "designs": screen["designs"],
                    "features": measured[screen["id"]],
                    "chooser": int((screen.get("detail") or {}).get("chooser", 1)),
                    "closest": (screen.get("detail") or {}).get("closest"),
                    "at": one.get("created_at"),
                },
                separators=(",", ":"),
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


async def preview(ctx: service.Context, screen_id: str, index: int, aspect: str) -> bytes:
    """The picture of one design on one screen, cached for good: a screen never changes."""
    rows = await ctx.supa.select("lab_screens", id=f"eq.{screen_id}", select="*")
    if not rows or not 0 <= index < len(rows[0]["designs"]):
        raise DraftError("No such design on that screen.")
    screen = rows[0]
    posts = await ctx.supa.select("posts", id=f"eq.{screen['post_id']}", select="*")
    clients = await ctx.supa.select("clients", id=f"eq.{posts[0]['client_id']}", select="*")
    design = screen["designs"][index]
    shown = {"id": f"lab-{screen_id}-{index}", "spec": design["spec"]}
    as_designed = {**posts[0], "template_id": design["design_id"]}
    return await service.render_preview(ctx, shown, as_designed, clients[0], aspect)


__all__ = [
    "BUDGET_S",
    "CHOOSER",
    "REASONS",
    "ROLES",
    "answer",
    "display_order",
    "export",
    "looks_of",
    "make_screens",
    "next_screen",
    "photo_sets",
    "preview",
    "progress",
    "summarise",
]
