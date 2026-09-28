"""Client review links: a client approves or asks for changes from a link, no account.

The link is a credential, so it is handled like one:
- the token is 32 random bytes, and only its SHA-256 is stored (migration
  20260928125400_client_review_links); the database never holds a usable link;
- making a link runs as the signed-in designer, so row-level security decides which
  posts they may send; nothing here holds a key that crosses agencies;
- opening a link and deciding run as the anonymous role through two narrow database
  functions, which check the token, the expiry and that the post belongs to the review.

The client approves exactly the pixels they saw. Previews are rendered and stored when
the link is made, with signed URLs that expire with the link, so a designer editing the
post afterwards cannot change what the client is looking at. (Everywhere else previews
are rendered on demand and never stored; a review is the one place where a frozen
picture is the point.)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from stratum_worker.app.supa import Supa, SupabaseError

MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 30, 14
MAX_POSTS = 31  # a month of posts, one a day


class ReviewError(ValueError):
    """Something a person asked for that cannot be done; the message says why."""


def new_token() -> tuple[str, str]:
    """(token for the URL, the SHA-256 hex digest the database keeps)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def link_expiry(days: int, now: datetime) -> datetime:
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise ReviewError(f"a review link lasts {MIN_DAYS} to {MAX_DAYS} days")
    return now + timedelta(days=days)


def caption_of(post: dict[str, Any]) -> str:
    words = post.get("words") or {}
    caption = str(words.get("caption", "")).strip()
    tags = " ".join(str(t) for t in (words.get("hashtags") or []))
    return f"{caption}\n\n{tags}".strip()


async def create_review(
    ctx: Any,
    *,
    client_id: str,
    post_ids: list[str],
    days: int = DEFAULT_DAYS,
    title: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freeze the latest version of each post and make one link for all of them.

    `ctx` is a service.Context acting as the signed-in designer. Returns the path of
    the link; the token appears in it and nowhere else.
    """
    from stratum_worker.app import service

    unique = list(dict.fromkeys(post_ids))
    if not 1 <= len(unique) <= MAX_POSTS:
        raise ReviewError(f"send between 1 and {MAX_POSTS} posts in one review")
    started = now or datetime.now(UTC)
    expires = link_expiry(days, started)

    clients = await ctx.supa.select("clients", id=f"eq.{client_id}", select="*")
    if not clients:
        raise ReviewError("no such client")
    client = clients[0]

    review_id = str(uuid.uuid4())
    items: list[dict[str, Any]] = []
    for position, post_id in enumerate(unique):
        posts = await ctx.supa.select("posts", id=f"eq.{post_id}", select="*")
        if not posts or posts[0]["client_id"] != client_id:
            raise ReviewError("every post in a review must belong to that client")
        post = posts[0]
        versions = await ctx.supa.select(
            "post_versions",
            post_id=f"eq.{post_id}",
            select="id,spec,post_id,version",
            order="version.desc",
            limit="1",
        )
        if not versions:
            raise ReviewError(f"{post['slug']} has no draft yet")
        version = versions[0]
        seconds = int((expires - started).total_seconds()) + 3600
        previews: dict[str, str] = {}
        for aspect in post["aspects"]:
            data = await service.render_preview(ctx, version, post, client, aspect)
            shape = aspect.replace(":", "x")
            path = f"{ctx.agency_id}/reviews/{review_id}/{version['id']}-{shape}.webp"
            await ctx.supa.upload(path, data, "image/webp")
            previews[aspect] = await ctx.supa.signed_url(path, expires_in=seconds)
        items.append(
            {
                "agency_id": ctx.agency_id,
                "review_id": review_id,
                "post_id": post_id,
                "version_id": version["id"],
                "position": position,
                "caption": caption_of(post)[:5000],
                "previews": previews,
            }
        )

    token, digest = new_token()
    await ctx.supa.insert(
        "reviews",
        {
            "id": review_id,
            "agency_id": ctx.agency_id,
            "client_id": client_id,
            "token_hash": digest,
            "title": title.strip()[:200],
            "expires_at": expires.isoformat(),
        },
    )
    await ctx.supa.insert("review_items", items)
    for post_id in unique:
        await ctx.supa.update("posts", {"status": "review"}, id=f"eq.{post_id}")
    return {
        "review_id": review_id,
        "path": f"/r/{token}",
        "expires_at": expires.isoformat(),
        "posts": len(items),
    }


async def list_reviews(
    client: Supa, client_id: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    """A client's links, newest first. The token is not here: it was never stored."""
    rows = await client.select(
        "reviews",
        client_id=f"eq.{client_id}",
        select="id,title,expires_at,revoked_at,created_at",
        order="created_at.desc",
    )
    at = now or datetime.now(UTC)
    for row in rows:
        if row.get("revoked_at"):
            row["state"] = "revoked"
        elif datetime.fromisoformat(str(row["expires_at"])) <= at:
            row["state"] = "expired"
        else:
            row["state"] = "open"
    return rows


async def revoke(client: Supa, review_id: str) -> bool:
    rows = await client.update(
        "reviews", {"revoked_at": "now"}, id=f"eq.{review_id}", revoked_at="is.null"
    )
    return bool(rows)


async def open_review(anon: Supa, token: str) -> dict[str, Any] | None:
    """What the client sees, or None for any link that does not work."""
    found = await anon.rpc("review_open", {"token": token})
    return found if isinstance(found, dict) else None


VERDICTS = frozenset({"approved", "changes"})


async def decide(
    anon: Supa,
    token: str,
    *,
    item_id: str,
    verdict: str,
    comment: str = "",
    pin: dict[str, float] | None = None,
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ReviewError("the verdict must be approved or changes")
    if verdict == "changes" and not comment.strip():
        raise ReviewError("say what should change")
    try:
        uuid.UUID(item_id)
    except ValueError as exc:
        raise ReviewError("that post is not part of this review") from exc
    try:
        result = await anon.rpc(
            "review_decide",
            {
                "token": token,
                "item_id": item_id,
                "verdict": verdict,
                "comment": comment[:2000],
                "pin": pin,
            },
        )
    except SupabaseError as exc:
        # The database's own sentence is safe to show: it never says why a token failed.
        raise ReviewError(_plain(exc.detail)) from exc
    return result if isinstance(result, dict) else {}


def _plain(detail: str) -> str:
    """PostgREST wraps a raised exception as JSON; keep only its message."""
    import json

    try:
        body = json.loads(detail)
    except ValueError:
        return "that did not work; please try again"
    message = body.get("message") if isinstance(body, dict) else None
    return str(message) if message else "that did not work; please try again"
