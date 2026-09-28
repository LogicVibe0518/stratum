"""The brand-kit routes: read the colours from a logo, save them, see the history."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
TEAL = {
    "primary": "#19b5b0",
    "secondary": "#ffd23f",
    "surface": "#f4fdfd",
    "text": "#1a1a1a",
    "text_on_primary": "#1a1a1a",
    "text_on_dark": "#ffffff",
    "muted": "#4d7a79",
}


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
            user_id="designer-1",
        )

    main.app.dependency_overrides[main.context] = context
    try:
        yield TestClient(main.app), client
    finally:
        main.app.dependency_overrides.clear()


def new_client(http: TestClient, name: str = "Acme Heating", **extra: Any) -> Any:
    slug = name.lower().replace(" ", "-")
    res = http.post(
        "/api/clients", json={"slug": slug, "name": name, "rules": {"client_id": slug}, **extra}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_new_client_starts_with_plain_colours_that_say_so(api: tuple[TestClient, str]) -> None:
    http, _ = api
    made = new_client(http)
    brand = http.get(f"/api/clients/{made['id']}/brand").json()
    assert brand["starter"] is True
    assert brand["kit"]["version"] == 1
    assert all(check["ok"] for check in brand["checks"])


def test_typing_an_existing_name_again_opens_it_and_keeps_its_colours(
    api: tuple[TestClient, str],
) -> None:
    http, _ = api
    made = new_client(http)
    saved = http.put(f"/api/clients/{made['id']}/brand", json={"palette": TEAL, "version": 1})
    assert saved.status_code == 200, saved.text
    again = new_client(http)
    assert again["id"] == made["id"]
    assert again["brand"]["palette"]["primary"] == TEAL["primary"]


def test_a_broken_brand_kit_is_refused_when_the_client_is_made(
    api: tuple[TestClient, str],
) -> None:
    http, _ = api
    res = http.post(
        "/api/clients",
        json={"slug": "bad", "name": "Bad", "rules": {}, "brand": {"palette": "blue"}},
    )
    assert res.status_code == 422


def test_the_suggestion_comes_from_the_logo_and_explains_itself(
    api: tuple[TestClient, str],
) -> None:
    http, client = api
    res = http.get(f"/api/clients/{client}/brand/suggestion")
    assert res.status_code == 200, res.text
    body = res.json()
    # The seeded fictional logo is one light colour, made for dark backgrounds.
    assert body["tone"] == "light"
    assert body["swatches"][0]["hex"] == "#ffffff"
    assert set(body["tokens"]) >= {"primary", "secondary", "surface", "text"}
    assert all(token["why"] for token in body["tokens"].values())
    assert len(body["logo"]) == 64
    # Nothing was saved.
    assert http.get(f"/api/clients/{client}/brand").json()["kit"]["version"] == 1


def test_saving_makes_the_next_version_and_keeps_the_last(api: tuple[TestClient, str]) -> None:
    http, client = api
    res = http.put(f"/api/clients/{client}/brand", json={"palette": TEAL, "version": 1})
    assert res.status_code == 200, res.text
    assert res.json()["kit"]["version"] == 2
    history = http.get(f"/api/clients/{client}/brand").json()["history"]
    assert [row["version"] for row in history] == [2, 1]


def test_saving_over_someone_elses_change_is_a_409_with_a_sentence(
    api: tuple[TestClient, str],
) -> None:
    http, client = api
    http.put(f"/api/clients/{client}/brand", json={"palette": TEAL, "version": 1})
    res = http.put(
        f"/api/clients/{client}/brand",
        json={"palette": {**TEAL, "primary": "#000000"}, "version": 1},
    )
    assert res.status_code == 409
    assert "Someone else changed" in res.text


def test_an_incomplete_palette_is_refused(api: tuple[TestClient, str]) -> None:
    http, client = api
    palette = {k: v for k, v in TEAL.items() if k != "muted"}
    res = http.put(f"/api/clients/{client}/brand", json={"palette": palette, "version": 1})
    assert res.status_code == 422
    assert "muted is missing" in res.text


def test_a_client_without_a_logo_is_asked_for_one(api: tuple[TestClient, str]) -> None:
    http, _ = api
    made = new_client(http)
    res = http.get(f"/api/clients/{made['id']}/brand/suggestion")
    assert res.status_code == 422
    assert "has no logo yet" in res.text


def test_an_unknown_client_is_a_404(api: tuple[TestClient, str]) -> None:
    http, _ = api
    assert http.get("/api/clients/nope/brand").status_code == 404
