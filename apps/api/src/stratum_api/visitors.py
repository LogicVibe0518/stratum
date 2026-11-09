"""Every visitor to the shared demo gets their own copies of its posts.

Everyone who taps "Try the demo" is signed in as the same account, so until now they
all edited the same four posts: two people at once saw each other's changes, and an
edit could be refused because somebody else had moved the poster on in the meantime.

Now the first time a visitor lists the posts, the server copies the demo's original
posts for them - the first draft, its three designs, its sizes and its checks - under
slugs ending in "--<visitor>", and from then on that visitor sees and changes only those
copies. Copies older than COPY_TTL are deleted by a later sign-in (main._reset_demo).

The visitor id is not a secret and not a sign-in. It is a random name the server hands
out with the demo session and the editor sends back as X-Demo-Visitor. All it decides
is which of the demo account's own posts are shown: it reaches nothing the demo account
could not already reach, and row-level security still scopes every query to the demo's
agency.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from stratum_worker.app import service
from stratum_worker.app.supa import SupabaseError

VISITOR = re.compile(r"[0-9a-f]{12}")
MARK = "--"  # a copy's slug is "<original slug>--<visitor>"; an original never has "--"
COPY_TTL = timedelta(hours=24)
ORIGINALS = f"not.like.*{MARK}*"


def new_visitor() -> str:
    return secrets.token_hex(6)


def visitor_of(value: str | None) -> str | None:
    """The visitor id from a header, or None when it is missing or not one of ours."""
    return value if value and VISITOR.fullmatch(value) else None


def copy_slug(slug: str, visitor: str) -> str:
    return f"{slug[:60]}{MARK}{visitor}"


def mine(visitor: str) -> str:
    return f"like.*{MARK}{visitor}"


def owns(ctx: service.Context, post: dict[str, Any]) -> bool:
    """Whether this caller may see the post. Outside the demo, row-level security has
    already decided. In the demo, a visitor sees only their own copies; a caller with
    no visitor id (an editor opened before this existed) sees only the originals."""
    if not ctx.demo:
        return True
    slug = str(post.get("slug", ""))
    if ctx.visitor:
        return slug.endswith(f"{MARK}{ctx.visitor}")
    return MARK not in slug


def post_filter(ctx: service.Context) -> dict[str, str]:
    """The PostgREST filter that lists exactly the posts `owns` allows."""
    if not ctx.demo:
        return {}
    return {"slug": mine(ctx.visitor) if ctx.visitor else ORIGINALS}


async def ensure_copies(ctx: service.Context) -> int:
    """Make this visitor's copies of the demo's original posts, unless they exist.
    Returns how many posts were copied. Safe to run twice at once (two tabs): the
    unique (client_id, slug) key lets only one of them make each copy."""
    if not ctx.demo or not ctx.visitor:
        return 0
    if await ctx.supa.select("posts", slug=mine(ctx.visitor), select="id", limit="1"):
        return 0
    originals = await ctx.supa.select("posts", slug=ORIGINALS, select="*", order="created_at.asc")
    made = 0
    for post in originals:
        try:
            [copy] = await ctx.supa.insert(
                "posts",
                {
                    "agency_id": ctx.agency_id,
                    "client_id": post["client_id"],
                    "slug": copy_slug(post["slug"], ctx.visitor),
                    "template_id": post["template_id"],
                    "brief": post["brief"],
                    "occasion": post.get("occasion"),
                    "photo_note": post.get("photo_note"),
                    "aspects": post["aspects"],
                    "status": "draft",
                    "words": post.get("words"),
                    "cost_usd": post.get("cost_usd"),
                    "created_by": ctx.user_id,
                },
            )
        except SupabaseError as exc:
            if exc.status == 409:  # the other tab made this one first
                continue
            raise
        await _copy_first_draft(ctx, post["id"], copy["id"])
        await _copy_options(ctx, post["id"], copy["id"])
        made += 1
    return made


async def _copy_first_draft(ctx: service.Context, from_post: str, to_post: str) -> None:
    first = await ctx.supa.select(
        "post_versions", post_id=f"eq.{from_post}", version="eq.1", select="id,spec,note"
    )
    if not first:
        return
    [version] = await ctx.supa.insert(
        "post_versions",
        {
            "agency_id": ctx.agency_id,
            "post_id": to_post,
            "version": 1,
            "spec": first[0]["spec"],
            "note": first[0].get("note"),
            "created_by": ctx.user_id,
        },
    )
    # Which sizes it has, and what the checks found: the picture itself is drawn from
    # the spec on demand (service.save_version), so nothing is uploaded or copied.
    renders = await ctx.supa.select("renders", post_version_id=f"eq.{first[0]['id']}")
    if renders:
        await ctx.supa.insert(
            "renders",
            [
                {
                    "agency_id": ctx.agency_id,
                    "post_version_id": version["id"],
                    "aspect": r["aspect"],
                    "width": r["width"],
                    "height": r["height"],
                    "bytes": r["bytes"],
                    "storage_path": f"preview:{version['id']}:{r['aspect']}",
                }
                for r in renders
            ],
        )
    flags = await ctx.supa.select("flags", post_version_id=f"eq.{first[0]['id']}")
    if flags:
        await ctx.supa.insert(
            "flags",
            [
                {
                    "agency_id": ctx.agency_id,
                    "post_version_id": version["id"],
                    "code": f["code"],
                    "message": f["message"],
                    "severity": f["severity"],
                }
                for f in flags
            ],
        )


async def _copy_options(ctx: service.Context, from_post: str, to_post: str) -> None:
    # The newest set of three, as options.latest_options finds it.
    newest = await ctx.supa.select(
        "post_options",
        post_id=f"eq.{from_post}",
        select="batch",
        order="created_at.desc",
        limit="1",
    )
    if not newest:
        return
    rows = await ctx.supa.select(
        "post_options", post_id=f"eq.{from_post}", batch=f"eq.{newest[0]['batch']}", select="*"
    )
    batch = str(uuid.uuid4())
    await ctx.supa.insert(
        "post_options",
        [
            {
                "agency_id": ctx.agency_id,
                "post_id": to_post,
                "batch": batch,
                "slot": o["slot"],
                "position": o["position"],
                "design_id": o["design_id"],
                "spec": o["spec"],
                "score": o["score"],
                "propensity": o["propensity"],
                "explored": o.get("explored", False),
                "detail": o.get("detail") or {},
                "created_by": ctx.user_id,
            }
            for o in rows
        ],
    )


async def clean_copies(ctx: service.Context, now: datetime | None = None) -> int:
    """Delete visitors' copies older than COPY_TTL; the originals are never touched.
    The database's ON DELETE CASCADE takes each copy's versions, options and edits."""
    cutoff = (now or datetime.now(UTC)) - COPY_TTL
    gone = await ctx.supa.delete(
        "posts", slug=f"like.*{MARK}*", created_at=f"lt.{cutoff.isoformat()}"
    )
    return len(gone)


async def review_ids_of(ctx: service.Context, client_id: str) -> set[str] | None:
    """The review links this caller made, in the demo: those holding their own posts.
    None outside the demo (every review the agency can see is theirs)."""
    if not ctx.demo:
        return None
    posts = await ctx.supa.select(
        "posts", client_id=f"eq.{client_id}", select="id", **post_filter(ctx)
    )
    if not posts:
        return set()
    items = await ctx.supa.select(
        "review_items",
        post_id=f"in.({','.join(str(p['id']) for p in posts)})",
        select="review_id",
    )
    return {str(i["review_id"]) for i in items}
