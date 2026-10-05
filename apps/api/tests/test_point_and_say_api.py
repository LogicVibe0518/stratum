"""POST /api/posts/{id}/tweak with `pointed`: the part tapped or picked (M10h)."""

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
    http = TestClient(main.app)
    try:
        made = http.post(
            "/api/posts",
            json={
                "client_id": client,
                "brief": "Boiler service, 20% off this month.",
                "slug": "point",
                "template_id": "ds.panel.1.0010110",
                "aspects": ["4:5"],
            },
        )
        assert made.status_code == 200, made.text
        yield http, made.json()["post"]["id"]
    finally:
        main.app.dependency_overrides.clear()


def test_a_short_message_about_a_picked_part_is_applied(api: tuple[TestClient, str]) -> None:
    http, post = api
    out = http.post(
        f"/api/posts/{post}/tweak", json={"message": "bigger", "pointed": {"layer": "logo"}}
    )
    assert out.status_code == 200, out.text
    assert out.json()["outcome"] == "applied"


def test_a_tap_outside_the_poster_is_refused(api: tuple[TestClient, str]) -> None:
    http, post = api
    out = http.post(
        f"/api/posts/{post}/tweak", json={"message": "bigger", "pointed": {"x": 1.4, "y": 0.2}}
    )
    assert out.status_code == 422


def test_no_pointing_is_the_tweak_it_always_was(api: tuple[TestClient, str]) -> None:
    http, post = api
    out = http.post(f"/api/posts/{post}/tweak", json={"message": "make the logo bigger"})
    assert out.status_code == 200, out.text
