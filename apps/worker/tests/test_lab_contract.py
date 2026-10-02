"""lab_screens against real Postgres: the migration's promises, one by one.

Runs when STRATUM_TEST_DATABASE_URL points at a Postgres the test may create
databases on (see test_queue_contract.py). Every statement runs AS the role
PostgREST uses - `authenticated` for a signed-in person, `anon` for nobody.

What a reviewer should be able to repeat back:
- a designer can make and read their own agency's screens, for their own posts;
- nobody sees another agency's screens or attaches one to another agency's post;
- a viewer cannot make screens (but can rate them: ratings go to decision_log);
- nobody can change or delete a screen; the anonymous role cannot read them;
- a screen holds 2 to 6 designs, and goes with its post.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from test_queue_contract import PG_URL, fresh_database

pytestmark = pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")

TWO = '[{"design_id": "a"}, {"design_id": "b"}]'
# The designs are a literal, not a parameter: asyncpg would send a str as a JSON string.
SHAPED = (
    "insert into stratum.lab_screens (agency_id, post_id, designs) "
    "values ($1, $2, '{designs}'::jsonb) returning id"
)
INSERT = SHAPED.replace("{designs}", TWO)
RATE = (
    "insert into stratum.decision_log (agency_id, post_id, kind, actor, detail) "
    "values ($1, $2, 'designer_pick', $3, '{\"source\": \"lab\"}'::jsonb) returning id"
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


async def test_a_designer_makes_and_reads_their_own_screens(db: Seeded) -> None:
    screen = await as_user(db, INSERT, db.agency, db.post)
    assert screen is not None
    assert await as_user(db, "select count(*) from stratum.lab_screens") == 1


async def test_another_agency_sees_nothing(db: Seeded) -> None:
    await as_user(db, INSERT, db.agency, db.post)
    seen = await as_user(db, "select count(*) from stratum.lab_screens", user=db.outsider)
    assert seen == 0


async def test_a_screen_cannot_point_at_another_agencys_post(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.agency, db.their_post)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.other, db.their_post)


async def test_a_viewer_cannot_make_screens_but_can_rate_them(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, INSERT, db.agency, db.post, user=db.viewer)
    rated = await as_user(db, RATE, db.agency, db.post, db.viewer, user=db.viewer)
    assert rated is not None


async def test_what_was_shown_never_changes(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    screen = await as_user(db, INSERT, db.agency, db.post)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "update stratum.lab_screens set shared = true where id = $1", screen)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "delete from stratum.lab_screens where id = $1", screen)


async def test_the_anonymous_role_cannot_read_screens(db: Seeded) -> None:
    from asyncpg.exceptions import InsufficientPrivilegeError

    await as_user(db, INSERT, db.agency, db.post)
    with pytest.raises(InsufficientPrivilegeError):
        await as_user(db, "select count(*) from stratum.lab_screens", role="anon")


@pytest.mark.parametrize(
    "designs", ['[{"design_id": "a"}]', "{}", "[" + ",".join(["{}"] * 7) + "]"]
)
async def test_a_screen_holds_two_to_six_designs(db: Seeded, designs: str) -> None:
    from asyncpg.exceptions import CheckViolationError

    with pytest.raises(CheckViolationError):
        await as_user(db, SHAPED.replace("{designs}", designs), db.agency, db.post)


async def test_screens_go_with_their_post(db: Seeded) -> None:
    await as_user(db, INSERT, db.agency, db.post)
    await db.conn.execute("delete from stratum.posts where id = $1", db.post)
    assert await db.conn.fetchval("select count(*) from stratum.lab_screens") == 0
