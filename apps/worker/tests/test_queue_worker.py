"""The queue loop, run against the SQLite stand-in with a clock the test controls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from stratum_worker.app.supa import Supa, SupabaseError
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.queue import fake, jobs
from stratum_worker.queue.fake import FakeSupa, World
from stratum_worker.queue.jobs import Job, backoff_seconds, describe
from stratum_worker.queue.worker import Deps, Handler, QueueWorker, work_timeout

REPO = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def world(tmp_path: Path, clock: Clock) -> World:
    return World(tmp_path / "db.sqlite", clock=clock)


async def add_job(world: World, agency: str, *, payload: dict[str, Any] | None = None) -> str:
    rows = await world.factory(agency).insert(
        "jobs",
        {
            "agency_id": agency,
            "kind": "draft_post",
            "payload": payload or {"post_id": "p"},
            "idempotency_key": f"k-{len(world.jobs())}",
        },
    )
    return str(rows[0]["id"])


def worker(world: World, handler: Handler, *, lease: int = 60, factory: Any = None) -> QueueWorker:
    return QueueWorker(
        ledger=world.ledger(),
        factory=factory or world.scoped,
        deps=Deps(settings=world.settings(REPO), gateway=fake.gateway()),
        lease_seconds=lease,
        worker_id="w-1",
        handlers={"draft_post": handler},
    )


def row(world: World, job_id: str) -> dict[str, Any]:
    return next(r for r in world.jobs() if r["id"] == job_id)


async def test_failures_back_off_exponentially_then_stop_with_last_error(
    world: World, clock: Clock
) -> None:
    async def flaky(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        raise RuntimeError(f"provider down on attempt {job.attempts}")

    job_id = await add_job(world, "agency-a")
    loop = worker(world, flaky)
    waits = []
    for _ in range(3):
        assert await loop.run_once()
        state = row(world, job_id)
        if state["status"] == "queued":
            due = datetime.fromisoformat(state["run_after"])
            waits.append((due - clock.now).total_seconds())
            assert not await loop.run_once()  # not due yet: backoff is honoured
            clock.advance(waits[-1] + 1)

    assert [round(w) for w in waits] == [5, 10]
    state = row(world, job_id)
    assert state["status"] == "failed"
    assert state["attempts"] == 3
    assert state["last_error"] == "RuntimeError: provider down on attempt 3"
    clock.advance(3600)
    assert not await loop.run_once()


def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert backoff_seconds(30) == 300


async def test_an_error_that_would_repeat_fails_at_once(world: World) -> None:
    async def no_logo(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        raise DraftError("Kowalski has no logo yet")

    job_id = await add_job(world, "agency-a")
    assert await worker(world, no_logo).run_once()
    state = row(world, job_id)
    assert (state["status"], state["attempts"]) == ("failed", 1)
    assert "no logo" in state["last_error"]


async def test_a_job_is_stopped_before_its_lease_can_expire(world: World) -> None:
    assert work_timeout(180) == 150
    assert work_timeout(8) == 6

    async def slow(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    job_id = await add_job(world, "agency-a")
    assert await worker(world, slow, lease=2).run_once()  # 2s lease -> 1s budget
    state = row(world, job_id)
    assert state["status"] == "queued" and state["last_error"].startswith("TimeoutError")


async def test_all_work_goes_through_the_client_for_the_jobs_agency(world: World) -> None:
    asked: list[str] = []
    seen: list[str] = []

    def factory(job: Job) -> Supa:
        asked.append(job.agency_id)
        return world.scoped(job)

    async def record(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        assert isinstance(client, FakeSupa)
        seen.append(client.agency_id)
        return {"ok": True}

    await add_job(world, "agency-a")
    await add_job(world, "agency-b")
    loop = worker(world, record, factory=factory)
    assert await loop.run_once() and await loop.run_once()
    assert sorted(asked) == sorted(seen) == ["agency-a", "agency-b"]
    assert {r["status"] for r in world.jobs()} == {"done"}


async def test_success_records_result_and_timings(world: World) -> None:
    async def ok(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        return {"post_id": "p-1"}

    job_id = await add_job(world, "agency-a")
    assert await worker(world, ok).run_once()
    state = row(world, job_id)
    assert state["status"] == "done" and state["last_error"] == ""
    assert state["result"]["post_id"] == "p-1"
    assert state["result"]["attempt"] == 1
    assert {"claimed_at", "work_seconds", "worker"} <= set(state["result"])


# --- tenancy of the stand-in, and the read path -------------------------------------


async def test_the_fake_client_cannot_cross_agencies(world: World) -> None:
    a, b = world.factory("agency-a"), world.factory("agency-b")
    await add_job(world, "agency-a")
    assert await b.select("jobs") == []
    with pytest.raises(SupabaseError) as denied:
        await b.insert("jobs", {"agency_id": "agency-a", "kind": "x", "payload": {}})
    assert denied.value.status == 403
    with pytest.raises(SupabaseError):
        await a.upload("agency-b/assets/x.png", b"x", "image/png")


async def test_enqueue_is_idempotent_per_client_and_slug(world: World) -> None:
    supa = world.factory("agency-a")
    key = jobs.idempotency_key("client-1", "boiler-offer")
    first = await jobs.enqueue(supa, agency_id="agency-a", kind="draft_post", payload={}, key=key)
    second = await jobs.enqueue(supa, agency_id="agency-a", kind="draft_post", payload={}, key=key)
    assert first["id"] == second["id"]
    assert len(world.jobs()) == 1


def test_a_job_lost_on_its_final_attempt_reads_as_failed() -> None:
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    stranded = {
        "id": "j",
        "status": "running",
        "attempts": 3,
        "max_attempts": 3,
        "last_error": "",
        "leased_until": (now - timedelta(seconds=1)).isoformat(),
        "payload": {"post_id": "p"},
    }
    seen = describe(stranded, now)
    assert seen["status"] == "failed" and seen["last_error"] == jobs.LOST_ON_FINAL_ATTEMPT
    # Still inside its lease, or with attempts left, it is honestly running.
    assert describe({**stranded, "attempts": 2}, now)["status"] == "running"
    later = (now + timedelta(seconds=30)).isoformat()
    assert describe({**stranded, "leased_until": later}, now)["status"] == "running"


# --- the real draft, end to end, with fake models --------------------------------------


async def _post(world: World, agency: str, client: str, slug: str) -> str:
    rows = await world.factory(agency).insert(
        "posts",
        {
            "agency_id": agency,
            "client_id": client,
            "slug": slug,
            "brief": "20% off boiler servicing this month. Gas Safe 000000.",
            "template_id": "offer_4x5",
            "aspects": ["4:5"],
        },
    )
    return str(rows[0]["id"])


async def test_draft_post_runs_the_real_service_through_the_queue(tmp_path: Path) -> None:
    world = World(tmp_path / "db.sqlite")
    agency, client = await fake.seed(world, REPO)
    post_id = await _post(world, agency, client, "boiler-offer")
    job_id = await add_job(world, agency, payload={"post_id": post_id})
    loop = QueueWorker(
        ledger=world.ledger(),
        factory=world.scoped,
        deps=Deps(settings=world.settings(REPO), gateway=fake.gateway()),
    )
    assert await loop.run_once()
    state = row(world, job_id)
    assert state["status"] == "done", state["last_error"]
    assert state["result"]["post_id"] == post_id
    versions = await world.factory(agency).select("post_versions", post_id=f"eq.{post_id}")
    assert [v["version"] for v in versions] == [1]
    assert all(v["agency_id"] == agency for v in versions)


async def test_a_factory_handing_back_another_agency_is_refused(tmp_path: Path) -> None:
    world = World(tmp_path / "db.sqlite")
    agency, client = await fake.seed(world, REPO)
    post_id = await _post(world, agency, client, "boiler-offer")
    rows = await world.factory("agency-x").insert(
        "jobs", {"agency_id": "agency-x", "kind": "draft_post", "payload": {"post_id": post_id}}
    )

    def wrong(_: Job) -> Supa:
        return world.factory(agency)  # a buggy factory: ignores the job it was given

    loop = QueueWorker(
        ledger=world.ledger(),
        factory=wrong,
        deps=Deps(settings=world.settings(REPO), gateway=fake.gateway()),
    )
    assert await loop.run_once()
    state = row(world, str(rows[0]["id"]))
    assert state["status"] == "failed" and "another agency" in state["last_error"]
    assert await world.factory(agency).select("post_versions") == []


async def test_a_reclaimed_job_does_not_draft_the_post_twice(tmp_path: Path) -> None:
    """The invariant that makes at-least-once delivery usable.

    The first run finishes the draft. Then the row is put back to exactly what
    claim_job sees after a lease expires on a job with attempts left - which is what a
    worker killed between finishing and settling leaves behind. The second run must
    not call a model or write a second version.
    """
    world = World(tmp_path / "db.sqlite")
    agency, client = await fake.seed(world, REPO)
    post_id = await _post(world, agency, client, "boiler-offer")
    job_id = await add_job(world, agency, payload={"post_id": post_id})
    deps = Deps(settings=world.settings(REPO), gateway=fake.gateway())
    first = QueueWorker(ledger=world.ledger(), factory=world.scoped, deps=deps)
    assert await first.run_once()
    assert row(world, job_id)["status"] == "done"

    await world.factory(agency).update(
        "jobs",
        {"status": "queued", "attempts": 0, "leased_by": None, "leased_until": None},
        id=f"eq.{job_id}",
    )
    second = QueueWorker(ledger=world.ledger(), factory=world.scoped, deps=deps)
    assert await second.run_once()

    state = row(world, job_id)
    assert state["status"] == "done", state["last_error"]
    assert state["result"]["recovered"] is True
    assert state["result"]["post_id"] == post_id
    versions = await world.factory(agency).select("post_versions", post_id=f"eq.{post_id}")
    assert [v["version"] for v in versions] == [1]


# --- when the queue itself says no ---------------------------------------------------


class RefusingLedger:
    """What PostgREST answers when the key is wrong or the grants are missing."""

    def __init__(self, status: int = 403) -> None:
        self.status = status
        self.calls = 0

    async def claim(self, worker: str, lease_seconds: int) -> Job | None:
        self.calls += 1
        raise SupabaseError(self.status, '{"message":"permission denied for schema stratum"}')

    async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        raise AssertionError("nothing was claimed")


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_key_stops_every_loop_with_one_clear_message(
    world: World, status: int
) -> None:
    from stratum_worker.queue.worker import QueueRefused, run_pool

    ledger = RefusingLedger(status)
    stop = asyncio.Event()
    with pytest.raises(QueueRefused, match="SUPABASE_SERVICE_ROLE_KEY"):
        await asyncio.wait_for(
            run_pool(
                ledger,  # type: ignore[arg-type]
                world.scoped,
                Deps(settings=world.settings(), gateway=fake.gateway()),
                concurrency=3,
                stop=stop,
                idle_s=0.01,
            ),
            timeout=5,
        )
    # At most one question per loop, then everything stops: no retry storm.
    assert 1 <= ledger.calls <= 3 and stop.is_set()


async def test_an_unreachable_queue_is_asked_less_often_each_time(world: World) -> None:
    import httpx

    class Down:
        calls = 0

        async def claim(self, worker: str, lease_seconds: int) -> Job | None:
            Down.calls += 1
            raise httpx.ConnectError("no route to host")

        async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
            raise AssertionError("nothing was claimed")

    loop = QueueWorker(
        ledger=Down(),  # type: ignore[arg-type]
        factory=world.scoped,
        deps=Deps(settings=world.settings(), gateway=fake.gateway()),
        worker_id="w-1",
        idle_s=1.0,
    )
    assert [loop.pause(n) for n in (0, 1, 2, 3, 4, 5, 6)] == [1, 2, 4, 8, 16, 30, 30]
    stop = asyncio.Event()
    task = asyncio.create_task(loop.run(stop))
    await asyncio.sleep(0.2)  # the first retry is 2s away
    stop.set()
    await asyncio.wait_for(task, timeout=3)
    assert Down.calls == 1


# --- what a person watching the worker sees -------------------------------------------


async def test_each_job_gets_a_started_and_a_done_line(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    async def quick(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        return {}

    job_id = await add_job(world, "agency-a")
    with caplog.at_level("INFO", logger="stratum.queue"):
        assert await worker(world, quick).run_once()
    lines = [r.getMessage() for r in caplog.records if r.name == "stratum.queue"]
    assert lines[0] == f"draft_post {job_id[:8]}: started, attempt 1 of 3"
    assert lines[1].startswith(f"draft_post {job_id[:8]}: done in ")


async def test_an_idle_worker_says_nothing(world: World, caplog: pytest.LogCaptureFixture) -> None:
    async def never(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
        raise AssertionError("no job was queued")

    with caplog.at_level("INFO"):
        assert not await worker(world, never).run_once()
    assert caplog.records == []


def test_polling_requests_are_not_logged_one_by_one() -> None:
    import logging

    from stratum_worker.queue.worker import configure_logging

    configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
