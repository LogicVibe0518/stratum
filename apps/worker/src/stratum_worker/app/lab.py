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

Screens are made by a job (slow: a ranking per post). Rating is quick and runs in the
request. Answers are decision_log rows of kind 'designer_pick' with source 'lab'.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

from stratum_worker.app import service
from stratum_worker.app.options import material_for
from stratum_worker.design.choose import NothingFits, candidates, seed_for
from stratum_worker.design.lab import lab_screen
from stratum_worker.draft.pipeline import DraftError

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


async def make_screens(
    ctx: service.Context, *, posts: int = 20, per_post: int = 6, shared_per_post: int = 3
) -> dict[str, Any]:
    """Screens for the agency's newest posts that have words and a client with photos.

    The first `shared_per_post` screens of each post are shown to every rater.
    """
    rows = await ctx.supa.select("posts", order="created_at.desc", select="*")
    done, made, skipped = 0, 0, []
    batch = str(uuid.uuid4())
    root = ctx.settings.repo_root
    for post in rows:
        if done >= posts:
            break
        words = post.get("words") or {}
        if not words.get("headline"):
            continue
        clients = await ctx.supa.select("clients", id=f"eq.{post['client_id']}", select="*")
        if not clients:
            continue
        client = clients[0]
        picked, _ = await service.pick_photos(ctx, client["id"], {"a": "photo", "b": "photo"})
        photos = tuple(picked[key] for key in ("a", "b") if key in picked)
        if not photos:
            skipped.append(post["slug"])
            continue
        try:
            material, asset_paths = await material_for(
                ctx, client, words, service.brand_of(client), photos, None
            )
            ranked = await asyncio.to_thread(
                candidates, material, ["4:5"], root, seed=seed_for(str(post["id"]))
            )
        except (DraftError, NothingFits):
            skipped.append(post["slug"])
            continue
        screens = []
        for index in range(per_post):
            chosen = lab_screen(ranked, _rng(post["id"], batch, index))
            if chosen is None:
                break
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
                        }
                        for one in chosen
                    ],
                    "detail": {"batch": batch, "candidates": len(ranked), "index": index},
                }
            )
        if screens:
            await ctx.supa.insert("lab_screens", screens)
            made += len(screens)
            done += 1
    return {"posts": done, "screens": made, "skipped": skipped, "batch": batch}


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
    return await ctx.supa.select(
        "lab_screens", select="id,post_id,shared,created_at", order="created_at.asc"
    )


def _choose(
    screens: list[dict[str, Any]], answers: list[dict[str, Any]], user: str | None
) -> tuple[dict[str, Any] | None, bool]:
    """The next screen for this rater, and whether it is a repeat."""
    mine = [one for one in answers if one.get("actor") == user]
    seen = [one["detail"]["screen"] for one in mine]
    rng = _rng(user, len(mine))
    if len(mine) >= REPEAT_AFTER and rng.random() < REPEAT_RATE:
        recent = set(seen[-RECENT:]) if RECENT else set()
        older = sorted(set(seen) - recent)
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
) -> dict[str, Any]:
    """Record one rater's choice on one screen. The same serving sent twice is one
    answer. Raises DraftError with a sentence when the answer cannot be right."""
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


async def export(ctx: service.Context) -> str:
    """Every answer with the designs it was about, one JSON object per line: what the
    critic v2 walkthrough trains on. Raters are numbered, not named."""
    answers = await _answers(ctx)
    screens = {one["id"]: one for one in await ctx.supa.select("lab_screens", select="*")}
    numbers: dict[str, int] = {}
    lines = []
    for one in answers:
        detail = one["detail"]
        screen = screens.get(detail["screen"])
        if screen is None:
            continue
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
                    "reasons": detail.get("reasons", []),
                    "ms": detail.get("ms"),
                    "order": detail.get("order", []),
                    "designs": screen["designs"],
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
    "REASONS",
    "answer",
    "display_order",
    "export",
    "make_screens",
    "next_screen",
    "preview",
    "progress",
    "summarise",
]
