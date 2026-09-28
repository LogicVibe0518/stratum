from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


def test_service_key_is_not_in_the_api_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-a-real-key")
    main.forget_service_role_key()
    assert "SUPABASE_SERVICE_ROLE_KEY" not in os.environ
    assert main.SERVICE_ROLE_KEY == "SUPABASE_SERVICE_ROLE_KEY"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "db.sqlite")


@pytest.fixture
def api(world: World) -> Iterator[tuple[TestClient, World, str, str]]:
    import asyncio

    agency, client = asyncio.run(fake.seed(world, REPO))

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
            user_id=None,
        )

    main.app.dependency_overrides[main.context] = context
    try:
        yield TestClient(main.app), world, agency, client
    finally:
        main.app.dependency_overrides.clear()


def _body(client: str, slug: str = "boiler-offer") -> dict[str, object]:
    return {"client_id": client, "brief": "20% off boiler servicing.", "slug": slug}


def test_queue_mode_returns_a_job_id_and_does_not_draft(
    api: tuple[TestClient, World, str, str],
) -> None:
    http, world, agency, client = api
    res = http.post("/api/posts?queue=true", json=_body(client))
    assert res.status_code == 200
    assert set(res.json()) == {"job_id"}
    [job] = world.jobs()
    assert job["agency_id"] == agency and job["status"] == "queued"
    assert job["idempotency_key"] == f"{client}:boiler-offer"
    assert job["kind"] == "draft_post"


def test_queue_mode_is_idempotent_on_client_and_slug(
    api: tuple[TestClient, World, str, str],
) -> None:
    http, world, _, client = api
    first = http.post("/api/posts?queue=true", json=_body(client)).json()
    second = http.post("/api/posts?queue=true", json=_body(client)).json()
    assert first == second
    assert len(world.jobs()) == 1


def test_a_post_whose_job_was_never_written_is_queued_on_retry(
    api: tuple[TestClient, World, str, str],
) -> None:
    import asyncio

    http, world, agency, client = api
    asyncio.run(
        world.factory(agency).insert(
            "posts",
            {
                "agency_id": agency,
                "client_id": client,
                "slug": "boiler-offer",
                "brief": "x",
                "template_id": "offer_4x5",
            },
        )
    )
    assert http.post("/api/posts?queue=true", json=_body(client)).status_code == 200
    assert len(world.jobs()) == 1


def test_get_job_reports_status_attempts_error_and_result(
    api: tuple[TestClient, World, str, str],
) -> None:
    http, _, _, client = api
    job_id = http.post("/api/posts?queue=true", json=_body(client)).json()["job_id"]
    seen = http.get(f"/api/jobs/{job_id}").json()
    assert seen["status"] == "queued"
    assert seen["attempts"] == 0 and seen["last_error"] == "" and seen["result"] is None
    assert http.get("/api/jobs/not-a-job").status_code == 404


def test_get_job_reports_a_stranded_final_attempt_as_failed(
    api: tuple[TestClient, World, str, str],
) -> None:
    from stratum_worker.queue.fake import stamp
    from stratum_worker.queue.jobs import LOST_ON_FINAL_ATTEMPT

    http, world, _, client = api
    job_id = http.post("/api/posts?queue=true", json=_body(client, slug="stranded")).json()[
        "job_id"
    ]
    past = stamp(datetime.now(UTC) - timedelta(seconds=1))
    with world.db() as db:
        db.execute(
            "update rows set doc = json_set(doc, '$.status', 'running', "
            "'$.attempts', 3, '$.max_attempts', 3, '$.leased_until', ?) "
            "where tbl='jobs' and id=?",
            (past, job_id),
        )
    seen = http.get(f"/api/jobs/{job_id}").json()
    assert seen["status"] == "failed"
    assert seen["last_error"] == LOST_ON_FINAL_ATTEMPT


def test_sending_a_failed_post_again_puts_its_job_back_in_the_queue(
    api: tuple[TestClient, World, str, str],
) -> None:
    """Otherwise the first failure takes the slug for good.

    "This client has no logo yet" is the failure a real user will hit, and the fix
    for it is to upload a logo and make the post again. That has to work.
    """
    import asyncio

    http, world, agency, client = api
    job_id = http.post("/api/posts?queue=true", json=_body(client)).json()["job_id"]
    asyncio.run(
        world.factory(agency).update(
            "jobs",
            {"status": "failed", "attempts": 3, "last_error": "no logo yet"},
            id=f"eq.{job_id}",
        )
    )
    assert http.get(f"/api/jobs/{job_id}").json()["status"] == "failed"

    again = http.post("/api/posts?queue=true", json=_body(client)).json()
    assert again["job_id"] == job_id and len(world.jobs()) == 1
    seen = http.get(f"/api/jobs/{job_id}").json()
    assert seen["status"] == "queued"
    assert seen["attempts"] == 0 and seen["last_error"] == ""


def test_sending_a_post_whose_job_is_still_going_leaves_it_alone(
    api: tuple[TestClient, World, str, str],
) -> None:
    """The reset is narrow on purpose: only a job that has given up is restarted."""
    import asyncio

    http, world, agency, client = api
    job_id = http.post("/api/posts?queue=true", json=_body(client)).json()["job_id"]
    running = {
        "status": "running",
        "attempts": 1,
        "leased_by": "w-1",
        "leased_until": fake.stamp(datetime.now(UTC) + timedelta(seconds=120)),
    }
    asyncio.run(world.factory(agency).update("jobs", running, id=f"eq.{job_id}"))

    assert http.post("/api/posts?queue=true", json=_body(client)).json()["job_id"] == job_id
    seen = http.get(f"/api/jobs/{job_id}").json()
    assert seen["status"] == "running" and seen["attempts"] == 1
