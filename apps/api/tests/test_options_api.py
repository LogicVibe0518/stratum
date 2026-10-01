"""The top three over HTTP (M12b): choose "best three", see three, pick one."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.design import choose as chooser
from stratum_worker.design.choose import AUTO
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
DESIGNER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, str]]:
    monkeypatch.setattr(chooser, "MAX_CANDIDATES", 40)
    monkeypatch.setattr(chooser, "SHORTLIST", 8)
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
            user_id=DESIGNER,
        )

    main.app.dependency_overrides[main.context] = context
    try:
        http = TestClient(main.app)
        made = http.post(
            "/api/posts",
            json={
                "client_id": client,
                "brief": "20% off boiler servicing.",
                "slug": "boiler-best-three",
                "template_id": AUTO,
                "aspects": ["4:5", "9:16"],
            },
        )
        assert made.status_code == 200, made.text
        yield http, made.json()["post"]["id"]
    finally:
        main.app.dependency_overrides.clear()


def test_best_three_is_offered_first() -> None:
    assert TestClient(main.app).get("/api/designs").json()[0] == AUTO


def test_a_best_three_post_lists_three_options_with_pictures(
    post: tuple[TestClient, str],
) -> None:
    http, post_id = post
    listed = http.get(f"/api/posts/{post_id}/options").json()
    assert [one["position"] for one in listed] == [0, 1, 2]
    assert [one["name"] for one in listed] == ["A", "B", "C"]
    picture = http.get(listed[0]["preview"].replace("{shape}", "4x5"))
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    assert "immutable" in picture.headers["cache-control"]


def test_picking_an_option_makes_the_next_version(post: tuple[TestClient, str]) -> None:
    http, post_id = post
    listed = http.get(f"/api/posts/{post_id}/options").json()
    shown = http.get(f"/api/posts/{post_id}").json()
    current = max(version["version"] for version in shown["versions"])
    others = [one for one in listed if one["design_id"] != shown["post"]["template_id"]]
    picked = http.post(f"/api/posts/{post_id}/options/{others[0]['id']}/pick")
    assert picked.status_code == 200, picked.text
    assert picked.json()["version"] == current + 1
    assert http.get(f"/api/posts/{post_id}").json()["post"]["template_id"] == others[0]["design_id"]


def test_an_unknown_option_is_a_clear_refusal(post: tuple[TestClient, str]) -> None:
    http, post_id = post
    res = http.post(f"/api/posts/{post_id}/options/00000000-0000-0000-0000-00000000dead/pick")
    assert res.status_code in (400, 422)
    assert "does not belong" in res.text


def test_a_post_from_one_design_has_no_options(tmp_path: Path) -> None:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))
    main.app.dependency_overrides[main.context] = lambda: service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
        user_id=DESIGNER,
    )
    try:
        http = TestClient(main.app)
        made = http.post(
            "/api/posts",
            json={"client_id": client, "brief": "20% off.", "slug": "one-design"},
        )
        assert made.status_code == 200, made.text
        assert http.get(f"/api/posts/{made.json()['post']['id']}/options").json() == []
    finally:
        main.app.dependency_overrides.clear()
