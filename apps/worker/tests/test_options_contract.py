"""post_options against real Postgres: the migration's promises, one by one.

Runs when STRATUM_TEST_DATABASE_URL points at a Postgres the test may create
databases on (see test_queue_contract.py). Every statement runs AS the role
PostgREST uses - `authenticated` for a signed-in person, `anon` for nobody - because
that is the path the app takes and the one row-level security guards.

What a reviewer should be able to repeat back:
- a designer can write and read their own agency's options, for their own posts;
- nobody can see another agency's options, or attach an option to another
  agency's post even by writing their own agency id on it;
- a viewer cannot write options; nobody can change or delete one once written;
- the anonymous role cannot read them at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from test_queue_contract import PG_URL, fresh_database

pytestmark = pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")

INSERT = (
    "insert into stratum.post_options "
    "(agency_id, post_id, batch, slot, position, design_id, spec, score, propensity) "
    "values ($1, $2, $3, $4, $5, 'ds.panel.1.0000000', '{\"layers\": []}'::jsonb, 0.5, 1.0) "
    "returning id"
)


@dataclass
class Seeded:
    conn: Any
    agency: uuid.UUID
    other: uuid.UUID
    designer: uuid.UUID
    viewer: uuid.UUID
    outsider: uuid.UUID
    post: uuid.UUID
    their_post: uuid.UUID


async def as_user(db: Seeded, sql: str, *args: Any, user: Any = None, role: str = "") -> Any:
    """One statement as a signed-in person (or `anon`), the way PostgREST runs it."""
    async with db.conn.transaction():
        who = db.designer if user is None else user
        await db.conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(who))
        await db.conn.execute(f"set local role {role or 'authenticated'}")
        return await db.conn.fetchval(sql, *args)


async def _post(conn: Any, agency: uuid.UUID, slug: str) -> uuid.UUID:
    client = await conn.fetchval(
        "insert into stratum.clients (agency_id, slug, name, brand, rules) "
        "values ($1, $2, 'C', '{}'::jsonb, '{}'::jsonb) returning id",
        agency,
        slug,
    )
    post: uuid.UUID = await conn.fetchval(
        "insert into stratum.posts (agency_id, client_id, slug, template_id, brief) "
        "values ($1, $2, 'p', 'best_three', 'A brief') returning id",
        agency,
        client,
    )
    return post


@pytest.fixture
async def db() -> AsyncIterator[Seeded]:
    async for conn, _ in fresh_database():
        new_agency = "insert into stratum.agencies (name) values ($1) returning id"
        agency = await conn.fetchval(new_agency, "A")
        other = await conn.fetchval(new_agency, "B")
        designer, viewer, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await conn.executemany(
            "insert into auth.users (id) values ($1)", [(designer,), (viewer,), (outsider,)]
        )
        await conn.executemany(
            "insert into stratum.profiles (user_id, agency_id, role) values ($1, $2, $3)",
            [
                (designer, agency, "designer"),
                (viewer, agency, "viewer"),
                (outsider, other, "owner"),
            ],
        )
        yield Seeded(
            conn,
            agency,
            other,
            designer,
            viewer,
            outsider,
            await _post(conn, agency, "a"),
            await _post(conn, other, "b"),
        )


async def test_a_designer_writes_and_reads_their_own_options(db: Seeded) -> None:
    batch = uuid.uuid4()
    option = await as_user(db, INSERT, db.agency, db.post, batch, 1, 2)
    assert option is not None
    seen = await as_user(db, "select count(*) from stratum.post_options where batch = $1", batch)
    assert seen == 1


async def test_another_agency_sees_nothing(db: Seeded) -> None:
    await as_user(db, INSERT, db.agency, db.post, uuid.uuid4(), 1, 0)
    seen = await as_user(db, "select count(*) from stratum.post_options", user=db.outsider)
    assert seen == 0


async def test_an_option_cannot_point_at_another_agencys_post(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    # Our own agency id on the row, their post: refused by the post check.
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.agency, db.their_post, uuid.uuid4(), 1, 0)
    # Their agency id: refused by the agency check.
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.other, db.their_post, uuid.uuid4(), 1, 0)


async def test_a_viewer_cannot_write_options(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.agency, db.post, uuid.uuid4(), 1, 0, user=db.viewer)


async def test_what_was_shown_never_changes(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    option = await as_user(db, INSERT, db.agency, db.post, uuid.uuid4(), 1, 0)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "update stratum.post_options set score = 9 where id = $1", option)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "delete from stratum.post_options where id = $1", option)


async def test_the_anonymous_role_cannot_read_options(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    await as_user(db, INSERT, db.agency, db.post, uuid.uuid4(), 1, 0)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "select count(*) from stratum.post_options", role="anon")


async def test_one_batch_has_one_option_per_slot_and_per_place(db: Seeded) -> None:
    from asyncpg.exceptions import UniqueViolationError

    batch = uuid.uuid4()
    await as_user(db, INSERT, db.agency, db.post, batch, 1, 0)
    with pytest.raises(UniqueViolationError):
        await as_user(db, INSERT, db.agency, db.post, batch, 1, 1)
    with pytest.raises(UniqueViolationError):
        await as_user(db, INSERT, db.agency, db.post, batch, 2, 0)


async def test_a_propensity_outside_zero_to_one_is_refused(db: Seeded) -> None:
    from asyncpg.exceptions import CheckViolationError

    bad = INSERT.replace("0.5, 1.0)", "0.5, 0.0)")
    with pytest.raises(CheckViolationError):
        await as_user(db, bad, db.agency, db.post, uuid.uuid4(), 1, 0)


async def test_options_go_with_their_post(db: Seeded) -> None:
    await as_user(db, INSERT, db.agency, db.post, uuid.uuid4(), 1, 0)
    await db.conn.execute("delete from stratum.posts where id = $1", db.post)
    left = await db.conn.fetchval("select count(*) from stratum.post_options")
    assert left == 0
