"""Making a client review link, through the SQLite stand-in with a real draft behind it.

What a reviewer should check here:
- the link is the only place the token exists; the database keeps its SHA-256 only;
- each post is frozen at its latest version, rendered and stored as the client saw it;
- a designer cannot slip another client's post, or an undrafted one, into a review.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from stratum_worker.app import reviews, service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


@pytest.fixture
async def drafted(tmp_path: Path) -> tuple[World, service.Context, str, str]:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    ctx = service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
    )
    post = (
        await ctx.supa.insert(
            "posts",
            {
                "agency_id": agency,
                "client_id": client_id,
                "slug": "boiler-offer",
                "brief": "20% off boiler servicing this month. Gas Safe 000000.",
                "template_id": "offer_4x5",
                "aspects": ["4:5", "1:1"],
            },
        )
    )[0]
    clients = await ctx.supa.select("clients", id=f"eq.{client_id}")
    await service.draft(ctx, post, clients[0])
    return world, ctx, client_id, str(post["id"])


def _everything_stored(world: World) -> str:
    with sqlite3.connect(world.root / "db.sqlite") as db:
        return "\n".join(doc for (doc,) in db.execute("select doc from rows"))


async def test_the_link_is_the_only_place_the_token_exists(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    world, ctx, client_id, post_id = drafted
    made = await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id], now=NOW)
    token = made["path"].removeprefix("/r/")
    assert len(token) >= 43
    [review] = await ctx.supa.select("reviews")
    assert review["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in _everything_stored(world)


async def test_each_post_is_frozen_as_the_client_will_see_it(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    world, ctx, client_id, post_id = drafted
    made = await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id], now=NOW)
    [item] = await ctx.supa.select("review_items")
    [latest] = await ctx.supa.select("post_versions", post_id=f"eq.{post_id}")
    assert item["version_id"] == latest["id"]
    assert set(item["previews"]) == {"4:5", "1:1"}
    folder = world.root / "storage" / ctx.agency_id / "reviews" / made["review_id"]
    stored = list(folder.iterdir())
    assert len(stored) == 2 and all(p.suffix == ".webp" and p.stat().st_size > 0 for p in stored)
    [post] = await ctx.supa.select("posts", id=f"eq.{post_id}")
    assert post["status"] == "review"
    assert made["expires_at"].startswith("2026-10-12")  # 14 days by default


async def test_the_same_post_twice_is_one_item(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    _, ctx, client_id, post_id = drafted
    await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id, post_id], now=NOW)
    assert len(await ctx.supa.select("review_items")) == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"post_ids": []}, "between 1 and 31"),
        ({"post_ids": [f"p{i}" for i in range(32)]}, "between 1 and 31"),
        ({"days": 0}, "1 to 30 days"),
        ({"days": 31}, "1 to 30 days"),
    ],
)
async def test_bad_requests_are_refused_in_plain_words(
    drafted: tuple[World, service.Context, str, str], kwargs: dict[str, Any], message: str
) -> None:
    _, ctx, client_id, post_id = drafted
    args: dict[str, Any] = {"client_id": client_id, "post_ids": [post_id], **kwargs}
    with pytest.raises(reviews.ReviewError, match=message):
        await reviews.create_review(ctx, now=NOW, **args)
    assert await ctx.supa.select("reviews") == []


async def test_another_clients_post_cannot_be_slipped_in(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    _, ctx, _, post_id = drafted
    other = (
        await ctx.supa.insert(
            "clients",
            {
                "agency_id": ctx.agency_id,
                "slug": "other",
                "name": "Other",
                "brand": {},
                "rules": {},
            },
        )
    )[0]
    with pytest.raises(reviews.ReviewError, match="must belong to that client"):
        await reviews.create_review(ctx, client_id=other["id"], post_ids=[post_id], now=NOW)
    assert await ctx.supa.select("reviews") == []


async def test_an_undrafted_post_cannot_be_sent(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    _, ctx, client_id, _ = drafted
    empty = (
        await ctx.supa.insert(
            "posts",
            {
                "agency_id": ctx.agency_id,
                "client_id": client_id,
                "slug": "not-yet",
                "brief": "later",
                "template_id": "offer_4x5",
                "aspects": ["4:5"],
            },
        )
    )[0]
    with pytest.raises(reviews.ReviewError, match="has no draft yet"):
        await reviews.create_review(ctx, client_id=client_id, post_ids=[empty["id"]], now=NOW)


async def test_revoking_is_once_only(drafted: tuple[World, service.Context, str, str]) -> None:
    _, ctx, client_id, post_id = drafted
    made = await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id], now=NOW)
    assert await reviews.revoke(ctx.supa, made["review_id"])
    assert not await reviews.revoke(ctx.supa, made["review_id"])


async def test_links_are_listed_as_open_expired_or_revoked_and_never_show_the_token(
    drafted: tuple[World, service.Context, str, str],
) -> None:
    _, ctx, client_id, post_id = drafted
    kept = await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id], now=NOW)
    gone = await reviews.create_review(
        ctx, client_id=client_id, post_ids=[post_id], days=1, now=NOW
    )
    killed = await reviews.create_review(ctx, client_id=client_id, post_ids=[post_id], now=NOW)
    await reviews.revoke(ctx.supa, killed["review_id"])
    later = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)  # two days on: the 1-day link is dead
    listed = {r["id"]: r for r in await reviews.list_reviews(ctx.supa, client_id, now=later)}
    assert listed[kept["review_id"]]["state"] == "open"
    assert listed[gone["review_id"]]["state"] == "expired"
    assert listed[killed["review_id"]]["state"] == "revoked"
    shown = str(listed)
    for made in (kept, gone, killed):
        assert made["path"].removeprefix("/r/") not in shown


def test_captions_include_the_hashtags() -> None:
    post = {"words": {"caption": "Book now.", "hashtags": ["#boiler", "#manchester"]}}
    assert reviews.caption_of(post) == "Book now.\n\n#boiler #manchester"
    assert reviews.caption_of({}) == ""


@pytest.mark.parametrize(
    ("verdict", "comment", "item", "message"),
    [
        ("maybe", "", "00000000-0000-0000-0000-000000000000", "approved or changes"),
        ("changes", "  ", "00000000-0000-0000-0000-000000000000", "say what should change"),
        ("approved", "", "not-a-uuid", "not part of this review"),
    ],
)
async def test_decisions_are_checked_before_they_reach_the_database(
    verdict: str, comment: str, item: str, message: str
) -> None:
    class Unreachable(fake.FakeSupa):
        def __init__(self) -> None:
            pass

        async def rpc(self, name: str, args: Any = None) -> Any:
            raise AssertionError("the database should not have been asked")

    with pytest.raises(reviews.ReviewError, match=message):
        await reviews.decide(
            Unreachable(), "t" * 43, item_id=item, verdict=verdict, comment=comment
        )
