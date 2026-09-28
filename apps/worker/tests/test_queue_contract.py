"""The three promises the queue makes, checked against BOTH claim implementations.

- sqlite:   the stand-in used by tests, --fake and the kill -9 run. Always runs.
- postgres: supabase/migrations/ applied to a throwaway database, so these exercise
            the real stratum.claim_job. Runs when STRATUM_TEST_DATABASE_URL points at
            a Postgres the test may create databases on, e.g.
            docker run --rm -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
            STRATUM_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres

The lease expiring is simulated by moving leased_until into the past rather than by
sleeping, which tests the same WHERE clause without a slow test.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pytest
from stratum_worker.queue.fake import World, stamp
from stratum_worker.queue.jobs import Job, failed_values
from stratum_worker.queue.ledger import Ledger

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
STUBS = Path(__file__).with_name("supabase_stubs.sql")
PG_URL = os.environ.get("STRATUM_TEST_DATABASE_URL", "")


class Backend(Protocol):
    ledger: Ledger

    async def add_job(self, *, max_attempts: int = 3) -> str: ...
    async def expire_lease(self, job_id: str) -> None: ...
    async def make_due(self, job_id: str) -> None: ...
    async def row(self, job_id: str) -> dict[str, Any]: ...


# --- sqlite -------------------------------------------------------------------------


class SqliteBackend:
    def __init__(self, root: Path) -> None:
        self.world = World(root / "db.sqlite")
        self.ledger = self.world.ledger()
        self.agency = str(uuid.uuid4())

    async def add_job(self, *, max_attempts: int = 3) -> str:
        rows = await self.world.factory(self.agency).insert(
            "jobs",
            {
                "agency_id": self.agency,
                "kind": "draft_post",
                "payload": {"post_id": "p"},
                "idempotency_key": uuid.uuid4().hex,
                "max_attempts": max_attempts,
            },
        )
        return str(rows[0]["id"])

    def _set(self, job_id: str, column: str) -> None:
        past = stamp(datetime.now(UTC) - timedelta(seconds=1))
        with self.world.db() as db:
            db.execute(
                "update rows set doc = json_set(doc, ?, ?) where tbl='jobs' and id=?",
                (f"$.{column}", past, job_id),
            )

    async def expire_lease(self, job_id: str) -> None:
        self._set(job_id, "leased_until")

    async def make_due(self, job_id: str) -> None:
        self._set(job_id, "run_after")

    async def row(self, job_id: str) -> dict[str, Any]:
        return next(r for r in self.world.jobs() if r["id"] == job_id)


# --- postgres -----------------------------------------------------------------------


def _plain(record: Any) -> dict[str, Any]:
    out = {}
    for key, value in dict(record).items():
        out[key] = str(value) if isinstance(value, uuid.UUID) else value
    return out


class PgLedger:
    """The same two operations PostgrestLedger performs, issued as SQL.

    Each runs as service_role, the role PostgREST uses for the worker's key, so a
    missing grant fails here and not on the first live claim.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def claim(self, worker: str, lease_seconds: int) -> Job | None:
        async with self.conn.transaction():
            await self.conn.execute("set local role service_role")
            rows = await self.conn.fetch(
                "select * from stratum.claim_job($1, $2)", worker, lease_seconds
            )
        return Job.model_validate(_plain(rows[0])) if rows else None

    async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        async with self.conn.transaction():
            await self.conn.execute("set local role service_role")
            return await self._settle(job, worker, values)

    async def _settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        done = await self.conn.fetch(
            """
            update stratum.jobs set
                status = $3,
                last_error = coalesce($4, last_error),
                run_after = coalesce($5::text::timestamptz, run_after),
                result = coalesce($6::text::jsonb, result),
                leased_until = null,
                updated_at = now()
            where id = $1 and leased_by = $2 and status = 'running'
            returning id
            """,
            uuid.UUID(job.id),
            worker,
            values["status"],
            values.get("last_error"),
            values.get("run_after"),
            json.dumps(values["result"]) if "result" in values else None,
        )
        return bool(done)


class PgBackend:
    def __init__(self, conn: Any, agency: str) -> None:
        self.conn = conn
        self.agency = agency
        self.ledger = PgLedger(conn)

    async def add_job(self, *, max_attempts: int = 3) -> str:
        found = await self.conn.fetchval(
            "insert into stratum.jobs (agency_id, kind, payload, idempotency_key, max_attempts) "
            "values ($1, 'draft_post', '{}'::jsonb, $2, $3) returning id",
            uuid.UUID(self.agency),
            uuid.uuid4().hex,
            max_attempts,
        )
        return str(found)

    async def expire_lease(self, job_id: str) -> None:
        await self.conn.execute(
            "update stratum.jobs set leased_until = now() - interval '1 second' where id = $1",
            uuid.UUID(job_id),
        )

    async def make_due(self, job_id: str) -> None:
        await self.conn.execute(
            "update stratum.jobs set run_after = now() - interval '1 second' where id = $1",
            uuid.UUID(job_id),
        )

    async def row(self, job_id: str) -> dict[str, Any]:
        found = await self.conn.fetchrow(
            "select * from stratum.jobs where id = $1", uuid.UUID(job_id)
        )
        return _plain(found)


async def fresh_database() -> AsyncIterator[tuple[Any, str]]:
    # Skip here, not only in each test's decorator: with no URL, asyncpg quietly tries
    # localhost:5432, and a test that forgot the decorator fails on any laptop without
    # Postgres. (One did, on the first push.)
    if not PG_URL:
        pytest.skip("set STRATUM_TEST_DATABASE_URL")
    asyncpg = pytest.importorskip("asyncpg")
    name = f"stratum_test_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(PG_URL)
    await admin.execute(f'create database "{name}"')
    base, _, _ = PG_URL.rpartition("/")
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        await conn.execute(STUBS.read_text(encoding="utf-8"))
        for migration in MIGRATIONS:
            await conn.execute(migration.read_text(encoding="utf-8"))
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        yield conn, name
    finally:
        await conn.close()
        await admin.execute(f'drop database "{name}" with (force)')
        await admin.close()


@pytest.fixture(params=["sqlite", "postgres"])
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Backend]:
    if request.param == "sqlite":
        yield SqliteBackend(tmp_path)
        return
    if not PG_URL:
        pytest.skip("set STRATUM_TEST_DATABASE_URL to test the real claim_job")
    async for conn, _ in fresh_database():
        agency = await conn.fetchval(
            "insert into stratum.agencies (name) values ('Test') returning id"
        )
        yield PgBackend(conn, str(agency))


# --- the three promises ---------------------------------------------------------------


async def test_a_claimed_job_is_invisible_to_a_second_worker(backend: Backend) -> None:
    job_id = await backend.add_job()
    first = await backend.ledger.claim("worker-a", 60)
    assert first is not None and first.id == job_id
    assert first.attempts == 1 and first.leased_by == "worker-a"

    assert await backend.ledger.claim("worker-b", 60) is None


async def test_a_job_whose_lease_expired_is_reclaimed(backend: Backend) -> None:
    job_id = await backend.add_job()
    assert await backend.ledger.claim("worker-a", 60) is not None
    # worker-a is killed with -9 here: no status change, no error, nothing written.
    await backend.expire_lease(job_id)

    again = await backend.ledger.claim("worker-b", 60)
    assert again is not None and again.id == job_id
    assert again.attempts == 2 and again.leased_by == "worker-b"

    # If worker-a was only slow, not dead, its late result must not land.
    stale = again.model_copy(update={"leased_by": "worker-a"})
    assert not await backend.ledger.settle(stale, "worker-a", {"status": "done", "result": {}})
    assert (await backend.row(job_id))["leased_by"] == "worker-b"


async def test_a_job_failing_max_attempts_times_stops_and_keeps_its_last_error(
    backend: Backend,
) -> None:
    job_id = await backend.add_job(max_attempts=3)
    for attempt in (1, 2, 3):
        job = await backend.ledger.claim("worker-a", 60)
        assert job is not None and job.attempts == attempt
        values = failed_values(job, f"ProviderUnavailable: attempt {attempt}", elapsed_s=0.1)
        assert await backend.ledger.settle(job, "worker-a", values)
        await backend.make_due(job_id)  # skip the backoff wait

    row = await backend.row(job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"] == "ProviderUnavailable: attempt 3"
    assert await backend.ledger.claim("worker-b", 60) is None


async def test_a_retry_waits_for_its_backoff(backend: Backend) -> None:
    await backend.add_job()
    job = await backend.ledger.claim("worker-a", 60)
    assert job is not None
    assert await backend.ledger.settle(job, "worker-a", failed_values(job, "boom", elapsed_s=0))
    assert await backend.ledger.claim("worker-b", 60) is None  # run_after is 5s away


async def test_a_worker_lost_on_the_final_attempt_leaves_the_job_unclaimable(
    backend: Backend,
) -> None:
    """The known edge: no sweeper means nothing ever marks this row failed.

    jobs.describe() reports it as failed on the read path; this pins down that the
    database really does leave it 'running' and never hands it out again.
    """
    job_id = await backend.add_job(max_attempts=2)
    for _ in range(2):
        assert await backend.ledger.claim("worker-a", 60) is not None
        await backend.expire_lease(job_id)
    assert await backend.ledger.claim("worker-b", 60) is None
    row = await backend.row(job_id)
    assert row["status"] == "running" and row["attempts"] == 2


# --- postgres only ----------------------------------------------------------------------


@pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")
async def test_claim_skips_a_locked_row_instead_of_waiting() -> None:
    """SKIP LOCKED: a second worker steps over a row another transaction holds."""
    import asyncio

    asyncpg = pytest.importorskip("asyncpg")
    async for conn, name in fresh_database():
        agency = await conn.fetchval(
            "insert into stratum.agencies (name) values ('T') returning id"
        )
        await conn.execute(
            "insert into stratum.jobs (agency_id, kind, payload) values ($1, 'draft_post', '{}')",
            agency,
        )
        base, _, _ = PG_URL.rpartition("/")
        other = await asyncpg.connect(f"{base}/{name}")
        try:
            async with other.transaction():
                await other.execute("select id from stratum.jobs for update")
                claimed = await asyncio.wait_for(
                    conn.fetch("select * from stratum.claim_job('worker-b', 60)"), timeout=5
                )
                assert claimed == []
        finally:
            await other.close()
        assert await conn.fetch("select * from stratum.claim_job('worker-b', 60)")


@pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")
async def test_the_database_decides_who_asked_for_a_job() -> None:
    """requested_by comes from the caller's token, never from what the caller wrote.

    The worker signs a token for this user and acts as them, so a value the caller
    could choose would let agency A's user run a job as agency B's user.
    """
    async for conn, _ in fresh_database():
        agency = await conn.fetchval(
            "insert into stratum.agencies (name) values ('T') returning id"
        )
        alice, mallory, bob = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await conn.executemany(
            "insert into auth.users (id) values ($1)", [(u,) for u in (alice, mallory, bob)]
        )

        async def as_user(user: uuid.UUID, sql: str, *args: Any, db: Any = conn) -> Any:
            async with db.transaction():
                await db.execute("select set_config('request.jwt.claim.sub', $1, true)", str(user))
                return await db.fetchval(sql, *args)

        async def requester(job_id: Any, db: Any = conn) -> Any:
            return await db.fetchval("select requested_by from stratum.jobs where id = $1", job_id)

        # Alice writes a job that names Mallory. The database writes Alice.
        job = await as_user(
            alice,
            "insert into stratum.jobs (agency_id, kind, payload, requested_by) "
            "values ($1, 'draft_post', '{}', $2) returning id",
            agency,
            mallory,
        )
        assert await requester(job) == alice

        # She cannot rename it afterwards either.
        rename = "update stratum.jobs set requested_by = $2 where id = $1"
        await as_user(alice, rename, job, mallory)
        assert await requester(job) == alice

        # The worker's own writes carry no user and leave it alone.
        await conn.execute("update stratum.jobs set status = 'done' where id = $1", job)
        assert await requester(job) == alice

        # Bob re-sends the post: now Bob is the one who asked.
        await as_user(bob, "update stratum.jobs set status = 'queued' where id = $1", job)
        assert await requester(job) == bob


@pytest.mark.skipif(not PG_URL, reason="set STRATUM_TEST_DATABASE_URL")
async def test_the_service_role_key_can_claim_and_settle_and_nothing_else() -> None:
    """The worker's key reads every agency's queue, so what it may write is kept tiny."""
    async for conn, _ in fresh_database():
        agency = await conn.fetchval(
            "insert into stratum.agencies (name) values ('T') returning id"
        )
        job = await conn.fetchval(
            "insert into stratum.jobs (agency_id, kind, payload) "
            "values ($1, 'draft_post', '{}') returning id",
            agency,
        )

        async def as_service(sql: str, *args: Any, db: Any = conn) -> Any:
            async with db.transaction():
                await db.execute("set local role service_role")
                return await db.fetch(sql, *args)

        [claimed] = await as_service("select id from stratum.claim_job('w', 60)")
        assert claimed["id"] == job
        assert await as_service(
            "update stratum.jobs set status = 'done', result = '{}', last_error = '', "
            "leased_until = null, updated_at = now() "
            "where id = $1 and leased_by = 'w' and status = 'running' returning id",
            job,
        )
        refused = [
            ('update stratum.jobs set payload = \'{"post_id": "x"}\' where id = $1', job),
            ("update stratum.jobs set agency_id = agency_id where id = $1", job),
            ("update stratum.jobs set requested_by = null where id = $1", job),
            ("delete from stratum.jobs where id = $1", job),
            (
                "insert into stratum.jobs (agency_id, kind, payload) "
                "values ($1, 'draft_post', '{}')",
                agency,
            ),
            ("select count(*) from stratum.posts where agency_id = $1", agency),
        ]
        for sql, arg in refused:
            with pytest.raises(Exception, match="permission denied"):
                await as_service(sql, arg)
