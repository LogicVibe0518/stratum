"""Client review links against real Postgres: the migration's promises, one by one.

Runs when STRATUM_TEST_DATABASE_URL points at a Postgres the test may create
databases on (see test_queue_contract.py). These are the rules a reviewer should be
able to repeat back:
- a link opens its own review and nothing else; wrong, expired and revoked links all
  look the same;
- only review_decide writes a client's verdict; a designer can read it, never forge it;
- the anonymous role reaches two functions and no table;
- the decision log is append-only, and a person can only log their own actions;
- a link cannot be back-dated past the 31-day limit or re-keyed after it is issued.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from test_queue_contract import PG_URL, fresh_database

pytestmark = pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")

TOKEN = "t" * 43


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Seeded:
    conn: Any
    agency: uuid.UUID
    other_agency: uuid.UUID
    designer: uuid.UUID
    outsider: uuid.UUID
    client: uuid.UUID
    post: uuid.UUID
    version: uuid.UUID
    review: uuid.UUID
    item: uuid.UUID
    other_item: uuid.UUID


async def _agency_with_post(conn: Any, name: str) -> tuple[Any, Any, Any, Any]:
    agency = await conn.fetchval(
        "insert into stratum.agencies (name) values ($1) returning id", name
    )
    client = await conn.fetchval(
        "insert into stratum.clients (agency_id, slug, name, brand, rules) "
        "values ($1, 'client', 'Client', '{}', '{}') returning id",
        agency,
    )
    post = await conn.fetchval(
        "insert into stratum.posts (agency_id, client_id, slug, template_id, brief) "
        "values ($1, $2, 'offer', 'hero', 'brief') returning id",
        agency,
        client,
    )
    version = await conn.fetchval(
        "insert into stratum.post_versions (agency_id, post_id, version, spec) "
        "values ($1, $2, 1, '{}') returning id",
        agency,
        post,
    )
    return agency, client, post, version


async def _review(conn: Any, agency: Any, client: Any, post: Any, version: Any, token: str) -> Any:
    review = await conn.fetchval(
        "insert into stratum.reviews (agency_id, client_id, token_hash, expires_at) "
        "values ($1, $2, $3, now() + interval '14 days') returning id",
        agency,
        client,
        digest(token),
    )
    item = await conn.fetchval(
        "insert into stratum.review_items "
        "(agency_id, review_id, post_id, version_id, caption, previews) "
        "values ($1, $2, $3, $4, 'Book now', '{\"4:5\": \"https://example/p.webp\"}') returning id",
        agency,
        review,
        post,
        version,
    )
    return review, item


@pytest.fixture
async def db() -> AsyncIterator[Seeded]:
    async for conn, _ in fresh_database():
        agency, client, post, version = await _agency_with_post(conn, "Agency")
        other, other_client, other_post, other_version = await _agency_with_post(conn, "Other")
        designer, outsider = uuid.uuid4(), uuid.uuid4()
        await conn.executemany(
            "insert into auth.users (id) values ($1)", [(designer,), (outsider,)]
        )
        await conn.execute(
            "insert into stratum.profiles (user_id, agency_id) values ($1, $2), ($3, $4)",
            designer,
            agency,
            outsider,
            other,
        )
        review, item = await _review(conn, agency, client, post, version, TOKEN)
        _, other_item = await _review(
            conn, other, other_client, other_post, other_version, "o" * 43
        )
        yield Seeded(
            conn, agency, other, designer, outsider, client, post, version, review, item, other_item
        )


async def as_role(db: Seeded, role: str, sql: str, *args: Any, user: Any = None) -> Any:
    """Run one statement as `anon` or `authenticated`, the way PostgREST would."""
    async with db.conn.transaction():
        if user is not None:
            await db.conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(user))
        await db.conn.execute(f"set local role {role}")
        return await db.conn.fetchval(sql, *args)


async def open_(db: Seeded, token: str) -> Any:
    found = await as_role(db, "anon", "select stratum.review_open($1)", token)
    return found if found is None or isinstance(found, dict) else json.loads(found)


async def decide(db: Seeded, item: Any, verdict: str, comment: str = "", pin: Any = None) -> Any:
    return await as_role(
        db,
        "anon",
        "select stratum.review_decide($1, $2, $3, $4, $5::jsonb)",
        TOKEN,
        item,
        verdict,
        comment,
        pin,  # the connection's jsonb codec serialises it
    )


# --- opening a link -----------------------------------------------------------------


async def test_a_link_opens_its_own_review(db: Seeded) -> None:
    review = await open_(db, TOKEN)
    assert review["client"] == "Client" and review["agency"] == "Agency"
    [item] = review["items"]
    assert item["id"] == str(db.item)
    assert item["caption"] == "Book now" and item["decision"] is None


async def test_wrong_expired_and_revoked_links_all_look_the_same(db: Seeded) -> None:
    assert await open_(db, "x" * 43) is None
    assert await open_(db, "short") is None
    await db.conn.execute("update stratum.reviews set revoked_at = now() where id = $1", db.review)
    assert await open_(db, TOKEN) is None
    await db.conn.execute(
        "update stratum.reviews set revoked_at = null, expires_at = now() - interval '1 second' "
        "where id = $1",
        db.review,
    )
    assert await open_(db, TOKEN) is None


# --- deciding -------------------------------------------------------------------------


async def test_approving_records_the_verdict_moves_the_post_and_logs_it(db: Seeded) -> None:
    await decide(db, db.item, "approved")
    assert await db.conn.fetchval("select status from stratum.posts where id = $1", db.post) == (
        "approved"
    )
    log = await db.conn.fetchrow("select * from stratum.decision_log")
    assert log["kind"] == "client_verdict" and log["actor"] is None
    assert log["post_id"] == db.post and log["version_id"] == db.version
    review = await open_(db, TOKEN)
    assert review["items"][0]["decision"]["verdict"] == "approved"


async def test_asking_for_changes_needs_words_and_keeps_the_pin(db: Seeded) -> None:
    with pytest.raises(Exception, match="say what should change"):
        await decide(db, db.item, "changes", "   ")
    await decide(db, db.item, "changes", "Logo bigger", {"x": 0.9, "y": 0.1})
    row = await db.conn.fetchrow("select * from stratum.review_decisions")
    assert row["comment"] == "Logo bigger"
    assert row["pin"] == {"x": 0.9, "y": 0.1}
    assert await db.conn.fetchval("select status from stratum.posts where id = $1", db.post) == (
        "review"
    )


async def test_a_pin_keeps_its_format_and_nothing_else(db: Seeded) -> None:
    await decide(
        db, db.item, "changes", "Phone bigger", {"x": 0.5, "y": 0.5, "aspect": "4:5", "x2": "junk"}
    )
    stored = await db.conn.fetchval("select pin from stratum.review_decisions")
    logged = await db.conn.fetchval("select detail -> 'pin' from stratum.decision_log")
    assert stored == logged == {"x": 0.5, "y": 0.5, "aspect": "4:5"}


@pytest.mark.parametrize("aspect", ["9:16", 45, ""])
async def test_a_pin_on_a_format_the_post_was_not_shown_in_is_refused(
    db: Seeded, aspect: Any
) -> None:
    with pytest.raises(Exception, match="a pin is"):
        await decide(db, db.item, "changes", "here", {"x": 0.5, "y": 0.5, "aspect": aspect})


async def test_a_link_cannot_decide_on_another_reviews_post(db: Seeded) -> None:
    with pytest.raises(Exception, match="not part of this review"):
        await decide(db, db.other_item, "approved")
    assert await db.conn.fetchval("select count(*) from stratum.review_decisions") == 0


@pytest.mark.parametrize(
    "pin",
    [{"x": 2, "y": 0}, {"x": "0.5", "y": 0.5}, {"y": 0.5}, {"x": "abc", "y": 0.5}, [0.5, 0.5]],
)
async def test_a_malformed_pin_is_refused(db: Seeded, pin: Any) -> None:
    with pytest.raises(Exception, match="a pin is"):
        await decide(db, db.item, "changes", "here", pin)


# --- who can touch what -----------------------------------------------------------------


async def test_the_anonymous_role_reaches_no_table(db: Seeded) -> None:
    for table in ("reviews", "review_items", "review_decisions", "decision_log", "posts"):
        with pytest.raises(Exception, match="permission denied"):
            # The table names are this test's own constants, not input.
            await as_role(db, "anon", f"select count(*) from stratum.{table}")  # noqa: S608


async def test_the_anonymous_role_can_run_the_two_review_functions_and_nothing_else(
    db: Seeded,
) -> None:
    runnable = await db.conn.fetch(
        "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'stratum' and has_function_privilege('anon', p.oid, 'execute')"
    )
    # If this fails after you added a function: its migration needs
    # `revoke all on function stratum.<name>(...) from public;` and explicit grants.
    assert {row["proname"] for row in runnable} == {"review_open", "review_decide"}


async def test_a_revoked_link_stays_revoked(db: Seeded) -> None:
    await as_role(
        db,
        "authenticated",
        "update stratum.reviews set revoked_at = now() where id = $1",
        db.review,
        user=db.designer,
    )
    await as_role(
        db,
        "authenticated",
        "update stratum.reviews set revoked_at = null where id = $1",
        db.review,
        user=db.designer,
    )
    assert await open_(db, TOKEN) is None


async def test_a_designer_cannot_forge_a_client_verdict(db: Seeded) -> None:
    with pytest.raises(Exception, match="permission denied"):
        await as_role(
            db,
            "authenticated",
            "insert into stratum.review_decisions (agency_id, review_item_id, verdict) "
            "values ($1, $2, 'approved')",
            db.agency,
            db.item,
            user=db.designer,
        )
    with pytest.raises(Exception, match="row-level security"):
        await as_role(
            db,
            "authenticated",
            "insert into stratum.decision_log (agency_id, kind, actor) "
            "values ($1, 'client_verdict', $2)",
            db.agency,
            db.designer,
            user=db.designer,
        )


async def test_a_designer_logs_only_their_own_actions_and_cannot_rewrite_them(
    db: Seeded,
) -> None:
    logged = await as_role(
        db,
        "authenticated",
        "insert into stratum.decision_log (agency_id, post_id, kind, detail, actor) "
        "values ($1, $2, 'designer_pick', '{\"shown\": 3}', $3) returning id",
        db.agency,
        db.post,
        db.designer,
        user=db.designer,
    )
    assert logged is not None
    with pytest.raises(Exception, match="row-level security"):
        await as_role(
            db,
            "authenticated",
            "insert into stratum.decision_log (agency_id, kind, actor) "
            "values ($1, 'designer_pick', $2)",
            db.agency,
            db.outsider,
            user=db.designer,
        )
    with pytest.raises(Exception, match="permission denied"):
        await as_role(
            db,
            "authenticated",
            "update stratum.decision_log set detail = '{}' where id = $1",
            logged,
            user=db.designer,
        )


async def test_a_designer_sees_their_agencys_reviews_only(db: Seeded) -> None:
    mine = await as_role(
        db, "authenticated", "select count(*) from stratum.reviews", user=db.designer
    )
    theirs = await as_role(
        db, "authenticated", "select count(*) from stratum.reviews", user=db.outsider
    )
    assert mine == 1 and theirs == 1  # each sees only their own one


async def test_a_link_cannot_be_backdated_or_rekeyed(db: Seeded) -> None:
    with pytest.raises(Exception, match="check constraint"):
        await as_role(
            db,
            "authenticated",
            "insert into stratum.reviews "
            "(agency_id, client_id, token_hash, expires_at, created_at) "
            "values ($1, $2, $3, '2031-01-01', '2030-12-31')",
            db.agency,
            db.client,
            digest("n" * 43),
            user=db.designer,
        )
    await as_role(
        db,
        "authenticated",
        "update stratum.reviews set token_hash = $2 where id = $1",
        db.review,
        digest("stolen" * 8),
        user=db.designer,
    )
    assert await db.conn.fetchval(
        "select token_hash from stratum.reviews where id = $1", db.review
    ) == digest(TOKEN)
