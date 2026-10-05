"""POST /api/clients/{id}/brief/check: what a brief is missing, for the brief box."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))
    main.app.dependency_overrides[main.context] = lambda: service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
        user_id="00000000-0000-0000-0000-00000000a11c",
    )
    try:
        yield TestClient(main.app), client
    finally:
        main.app.dependency_overrides.clear()


def test_a_thin_brief_gets_questions_from_the_clients_own_profile(
    api: tuple[TestClient, str],
) -> None:
    http, client = api
    out = http.post(f"/api/clients/{client}/brief/check", json={"brief": "boilers 20% off"})
    assert out.status_code == 200, out.text
    gaps = out.json()["gaps"]
    assert [gap["code"] for gap in gaps] == ["too_short", "no_end", "no_service"]
    assert all(gap["question"] for gap in gaps)


def test_a_full_brief_gets_none(api: tuple[TestClient, str]) -> None:
    http, client = api
    brief = "Boiler service 20% off for bookings before 31 October in Didsbury"
    out = http.post(f"/api/clients/{client}/brief/check", json={"brief": brief})
    assert out.json() == {"gaps": []}


def test_another_agencys_client_is_not_found(api: tuple[TestClient, str]) -> None:
    http, _ = api
    out = http.post(
        "/api/clients/00000000-0000-0000-0000-000000000000/brief/check", json={"brief": "x"}
    )
    assert out.status_code == 404
