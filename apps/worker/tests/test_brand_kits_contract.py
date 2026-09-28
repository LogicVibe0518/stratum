"""Brand kit history against real Postgres: the migration's promises, one by one.

Runs when STRATUM_TEST_DATABASE_URL points at a Postgres the test may create
databases on (see test_queue_contract.py). Every change is made AS `authenticated`,
the role PostgREST uses for a signed-in designer, because that is the path the app
takes and the one row-level security guards. What a reviewer should be able to
repeat back:
- a client's first kit is version 1, and every change of colours is the next number,
  whatever the request asked for;
- every version stays in history exactly as it was, so an old poster can always be
  redrawn in its own colours;
- history is read-only to everyone, and one agency cannot see another's.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from test_queue_contract import PG_URL, fresh_database

pytestmark = pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")

NAVY = {"client_id": "acme", "version": 1, "palette": {"primary": "#12264a"}, "fonts": {}}
TEAL = {**NAVY, "palette": {"primary": "#19b5b0"}}


@dataclass
class Seeded:
    conn: Any
    agency: uuid.UUID
    client: uuid.UUID
    designer: uuid.UUID
    outsider: uuid.UUID


@pytest.fixture
async def db() -> AsyncIterator[Seeded]:
    async for conn, _ in fresh_database():
        agency = await conn.fetchval(
            "insert into stratum.agencies (name) values ('A') returning id"
        )
        other = await conn.fetchval("insert into stratum.agencies (name) values ('B') returning id")
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
        seeded = Seeded(conn, agency, uuid.uuid4(), designer, outsider)
        seeded.client = await as_user(
            seeded,
            "insert into stratum.clients (agency_id, slug, name, brand, rules) "
            "values ($1, 'acme', 'Acme', $2::jsonb, '{}') returning id",
            agency,
            {**NAVY, "version": 7},
        )
        yield seeded


async def as_user(db: Seeded, sql: str, *args: Any, user: Any = None, role: str = "") -> Any:
    """One statement as a signed-in person (or `anon`), the way PostgREST runs it."""
    async with db.conn.transaction():
        who = db.designer if user is None else user
        await db.conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(who))
        await db.conn.execute(f"set local role {role or 'authenticated'}")
        return await db.conn.fetchval(sql, *args)


async def set_brand(db: Seeded, brand: dict[str, Any]) -> None:
    await as_user(
        db, "update stratum.clients set brand = $1::jsonb where id = $2", brand, db.client
    )


async def history(db: Seeded) -> list[tuple[int, str]]:
    rows = await db.conn.fetch(
        "select version, kit from stratum.brand_kits where client_id = $1 order by version",
        db.client,
    )
    return [(row["version"], row["kit"]["palette"]["primary"]) for row in rows]


async def current(db: Seeded) -> dict[str, Any]:
    brand = await db.conn.fetchval("select brand from stratum.clients where id = $1", db.client)
    return brand if isinstance(brand, dict) else json.loads(brand)


# --- versions -----------------------------------------------------------------------


async def test_a_new_client_starts_at_version_one_whatever_it_asked(db: Seeded) -> None:
    assert (await current(db))["version"] == 1
    assert await history(db) == [(1, "#12264a")]


async def test_new_colours_are_the_next_version_and_the_old_one_is_kept(db: Seeded) -> None:
    await set_brand(db, TEAL)
    assert (await current(db))["version"] == 2
    assert await history(db) == [(1, "#12264a"), (2, "#19b5b0")]
    await set_brand(db, NAVY)
    assert await history(db) == [(1, "#12264a"), (2, "#19b5b0"), (3, "#12264a")]


async def test_a_request_cannot_choose_its_own_version_number(db: Seeded) -> None:
    await set_brand(db, {**TEAL, "version": 1})  # would overwrite v1's meaning
    await set_brand(db, {**NAVY, "version": 99})
    assert [version for version, _ in await history(db)] == [1, 2, 3]
    assert (await current(db))["version"] == 3


async def test_saving_the_same_colours_again_is_not_a_new_version(db: Seeded) -> None:
    await set_brand(db, {**NAVY, "version": 5})
    await as_user(db, "update stratum.clients set name = 'Acme Ltd' where id = $1", db.client)
    assert await history(db) == [(1, "#12264a")]
    assert (await current(db))["version"] == 1


async def test_the_upsert_the_api_uses_is_versioned_too(db: Seeded) -> None:
    await as_user(
        db,
        "insert into stratum.clients (agency_id, slug, name, brand, rules) "
        "values ($1, 'acme', 'Acme', $2::jsonb, '{}') "
        "on conflict (agency_id, slug) do update set brand = excluded.brand",
        db.agency,
        TEAL,
    )
    assert await history(db) == [(1, "#12264a"), (2, "#19b5b0")]


async def test_history_records_who_made_each_version(db: Seeded) -> None:
    await set_brand(db, TEAL)
    made_by = await db.conn.fetchval(
        "select created_by from stratum.brand_kits where client_id = $1 and version = 2",
        db.client,
    )
    assert made_by == db.designer


async def test_a_brand_that_is_not_an_object_is_refused(db: Seeded) -> None:
    with pytest.raises(Exception, match="must be a JSON object"):
        await set_brand(db, ["not", "a", "kit"])  # type: ignore[arg-type]


# --- who can touch history ----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "insert into stratum.brand_kits (agency_id, client_id, version, kit) "
        "select agency_id, client_id, 50, kit from stratum.brand_kits limit 1",
        "update stratum.brand_kits set kit = '{\"palette\": {}}'",
        "delete from stratum.brand_kits",
    ],
)
async def test_nobody_signed_in_can_rewrite_history(db: Seeded, sql: str) -> None:
    with pytest.raises(Exception, match="permission denied"):
        await as_user(db, sql)
    assert await history(db) == [(1, "#12264a")]


async def test_an_agency_sees_its_own_history_and_nobody_elses(db: Seeded) -> None:
    mine = "select count(*) from stratum.brand_kits"
    assert await as_user(db, mine) == 1
    assert await as_user(db, mine, user=db.outsider) == 0


async def test_the_anonymous_role_cannot_read_history(db: Seeded) -> None:
    with pytest.raises(Exception, match="permission denied"):
        await as_user(db, "select count(*) from stratum.brand_kits", role="anon")


async def test_the_trigger_functions_cannot_be_called_by_anyone_signed_in(db: Seeded) -> None:
    for role in ("anon", "authenticated"):
        for name in ("stamp_brand_version", "record_brand_kit"):
            allowed = await db.conn.fetchval(
                "select has_function_privilege($1, $2, 'execute')",
                role,
                f"stratum.{name}()",
            )
            assert allowed is False, (role, name)
