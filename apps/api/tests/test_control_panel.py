"""The control panel over HTTP (M10d): read every layer, change it by hand, no model.

- GET /api/posts/{id}/inspect: one size of the latest version, measured;
- POST /api/posts/{id}/edit: spec ops and style settings, validated like a typed
  tweak, applied with no model and no cost; refused when the poster has moved on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_spec import TextLayer
from stratum_spec import load as load_spec
from stratum_worker.app import service
from stratum_worker.design import decode
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import TaskName
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
DESIGNER = "00000000-0000-0000-0000-000000000001"
PANEL = "ds.panel.1.0010110"


@dataclass
class Desk:
    http: TestClient
    post_id: str
    provider: FakeProvider
    world: World
    agency: str

    def latest_spec(self) -> Any:
        rows = asyncio.run(
            self.world.factory(self.agency).select(
                "post_versions", post_id=f"eq.{self.post_id}", order="version.desc", limit="1"
            )
        )
        return load_spec(rows[0]["spec"])

    def edits(self) -> list[dict[str, Any]]:
        return asyncio.run(
            self.world.factory(self.agency).select("edits", post_id=f"eq.{self.post_id}")
        )


@pytest.fixture
def desk(tmp_path: Path) -> Iterator[Desk]:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))
    provider = FakeProvider(results=[fake.FAKE_COPY])
    router = Router(
        providers={provider.name: provider},
        policies={task: TaskPolicy(order=(provider.name,)) for task in TaskName},
    )

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=Gateway(router=router, budget=Budget(daily_cap_usd=2.0)),
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
                "brief": "Boiler service, 20% off this month.",
                "slug": "control-panel",
                "template_id": PANEL,
                "aspects": ["4:5", "9:16"],
            },
        )
        assert made.status_code == 200, made.text
        yield Desk(http, made.json()["post"]["id"], provider, world, agency)
    finally:
        main.app.dependency_overrides.clear()


def test_the_panel_reads_every_layer_of_a_size(desk: Desk) -> None:
    out = desk.http.get(f"/api/posts/{desk.post_id}/inspect?aspect=9x16")
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["version"] == 1 and body["aspect"] == "9:16"
    assert {one["id"] for one in body["layers"]} >= {"bg", "panel", "headline", "logo"}
    assert body["style"]["family"] == "panel"
    assert any(one["name"] == "navy" for one in body["palette"])
    assert body["measure"]["story_safe"] is not None


def test_a_size_the_post_does_not_have_is_a_sentence(desk: Desk) -> None:
    out = desk.http.get(f"/api/posts/{desk.post_id}/inspect?aspect=16x9")
    assert out.status_code == 422 and out.text == "This post has no 16:9 size."


def test_a_colour_change_by_hand_is_instant_free_and_needs_no_model(desk: Desk) -> None:
    calls = len(desk.provider.calls)
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/edit",
        json={
            "version": 1,
            "ops": [{"op": "set_color", "layer": "headline", "color": "palette.muted"}],
        },
    )
    assert out.status_code == 200, out.text
    assert out.json()["outcome"] == "applied" and out.json()["version"] == 2
    assert len(desk.provider.calls) == calls
    headline = desk.latest_spec().layer("headline")
    assert isinstance(headline, TextLayer) and headline.color == "palette.muted"
    [edit] = desk.edits()
    assert edit["message"] == "Editor: headline slate blue"
    assert edit["cost_usd"] == 0 and edit["outcome"] == "applied"


def test_a_change_made_on_an_old_version_is_refused(desk: Desk) -> None:
    first = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.3}]}
    assert desk.http.post(f"/api/posts/{desk.post_id}/edit", json=first).status_code == 200
    again = desk.http.post(f"/api/posts/{desk.post_id}/edit", json=first)
    assert again.status_code == 409
    assert again.text == (
        "The poster is on version 2 now, not 1. Reload to see it, then make the change again."
    )


@pytest.mark.parametrize(
    ("ops", "said"),
    [
        # A photo the client does not have (M17a: the panel may swap in the client's own).
        ([{"op": "swap_asset", "layer": "bg", "asset": "a" * 64}], "is not one of"),
        ([{"op": "needs_human", "reason": "x"}], "cannot make that change"),
        ([{"op": "scale", "layer": "logo", "factor": 9}], "outside what the editor allows"),
        ([], "Nothing to change."),
    ],
)
def test_what_the_panel_cannot_do_is_a_sentence(
    desk: Desk, ops: list[dict[str, Any]], said: str
) -> None:
    out = desk.http.post(f"/api/posts/{desk.post_id}/edit", json={"version": 1, "ops": ops})
    assert out.status_code == 422 and said in out.text


def test_a_colour_the_brand_does_not_have_is_refused_with_its_colours(desk: Desk) -> None:
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/edit",
        json={
            "version": 1,
            "ops": [{"op": "set_color", "layer": "headline", "color": "palette.green"}],
        },
    )
    assert out.status_code == 422
    assert out.text.startswith("Green isn't one of Kowalski Plumbing & Heating's brand colours")


def test_a_style_setting_by_hand_restyles_without_a_model(desk: Desk) -> None:
    calls = len(desk.provider.calls)
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/edit", json={"version": 1, "restyle": {"fill": "dark"}}
    )
    assert out.status_code == 200, out.text
    assert out.json()["note"] == "Changed the style: a navy background behind the words."
    assert len(desk.provider.calls) == calls
    post = desk.http.get(f"/api/posts/{desk.post_id}").json()["post"]
    assert decode(post["template_id"]).choice["fill"] == "dark"
    assert desk.edits()[0]["message"] == "Editor: new style"


def test_words_typed_into_the_panel_are_the_designers_own(desk: Desk) -> None:
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/edit",
        json={
            "version": 1,
            "ops": [{"op": "set_text", "layer": "offer", "text": "35% off every service"}],
        },
    )
    assert out.status_code == 200, out.text
    flags = desk.http.get(f"/api/posts/{desk.post_id}").json()["flags"]
    invented = [flag["message"] for flag in flags if flag["code"] == "invented_number"]
    assert not any("35%" in message for message in invented)  # typed by a person
