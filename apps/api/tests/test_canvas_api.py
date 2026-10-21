"""The editor's canvas over HTTP (editor v2).

What a reviewer should be able to repeat back:
- GET /api/posts/{id}/canvas: one size of the latest version as SVG markup that
  points at URLs, never at files on the server, with the fonts it uses and every
  layer measured;
- POST .../canvas/try: the same for changes not yet saved; nothing is written, and
  it is checked like a saved change (unknown ops, off-brand colours, old versions);
- a part dragged in one size (place) moves in that size only, and says so in History;
- GET /api/versions/{v}/assets/{sha}: only an asset that version draws, sized for a
  screen; another sha, or another agency's version, is a 404;
- POST .../restore: undo and redo name the version they want; a double press is one
  step (409), and redo after undo comes back exactly;
- /fonts serves only the renderer's own font files; /app serves the built editor.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stratum_api import main
from stratum_spec import load as load_spec
from stratum_spec import retarget
from stratum_worker.app import service
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
BOX = {"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.1, "rotation": 0}


@dataclass
class Desk:
    http: TestClient
    post_id: str
    world: World
    agency: str

    def versions(self) -> list[dict[str, Any]]:
        return asyncio.run(
            self.world.factory(self.agency).select(
                "post_versions", post_id=f"eq.{self.post_id}", order="version.desc"
            )
        )

    def edits(self) -> list[dict[str, Any]]:
        return asyncio.run(
            self.world.factory(self.agency).select(
                "edits", post_id=f"eq.{self.post_id}", order="created_at"
            )
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
                "slug": "canvas",
                "template_id": PANEL,
                "aspects": ["4:5", "9:16"],
            },
        )
        assert made.status_code == 200, made.text
        yield Desk(http, made.json()["post"]["id"], world, agency)
    finally:
        main.app.dependency_overrides.clear()


def canvas(desk: Desk, aspect: str = "4x5") -> dict[str, Any]:
    out = desk.http.get(f"/api/posts/{desk.post_id}/canvas?aspect={aspect}")
    assert out.status_code == 200, out.text
    body: dict[str, Any] = out.json()
    return body


def test_the_canvas_is_svg_that_points_at_urls_not_files(desk: Desk) -> None:
    body = canvas(desk)
    assert body["version"] == 1 and body["aspect"] == "4:5"
    svg = body["svg"]
    assert svg.startswith("<svg") and 'id="headline"' in svg
    assert f"/api/versions/{body['version_id']}/assets/" in svg
    assert str(REPO) not in svg and ".stratum" not in svg
    assert {"family", "weight", "url"} <= set(body["faces"][0])
    assert all(face["url"].startswith("/fonts/") for face in body["faces"])
    assert {one["id"] for one in body["layers"]} >= {"bg", "headline", "logo"}
    headline = next(one for one in body["layers"] if one["id"] == "headline")
    assert headline["size_px"] > 0 and headline["px"]["w"] > 0


def test_a_try_changes_the_picture_and_saves_nothing(desk: Desk) -> None:
    before = canvas(desk)
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/canvas/try",
        json={
            "version": 1,
            "aspect": "4:5",
            "ops": [{"op": "set_text", "layer": "headline", "text": "Warm all winter"}],
        },
    )
    assert out.status_code == 200, out.text
    [headline] = [one for one in out.json()["layers"] if one["id"] == "headline"]
    assert " ".join(headline["lines"]).lower() == "warm all winter"
    assert out.json()["svg"] != before["svg"]
    assert len(desk.versions()) == 1 and desk.edits() == []


@pytest.mark.parametrize(
    ("ops", "said"),
    [
        ([{"op": "swap_asset", "layer": "bg", "asset": "b" * 64}], "cannot try"),
        ([{"op": "set_color", "layer": "headline", "color": "#ff00ff"}], "outside"),
        ([{"op": "place", "layer": "headline", "aspect": "4:5", "box": {}}], "outside"),
        ([{"op": "place", "layer": "nope", "aspect": "4:5", "box": BOX}], "This design has no"),
        (
            [{"op": "place", "layer": "logo", "aspect": "4:5", "box": BOX, "max_lines": 2}],
            "The logo has no words",
        ),
    ],
)
def test_a_try_is_checked_like_a_saved_change(desk: Desk, ops: list[Any], said: str) -> None:
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/canvas/try", json={"version": 1, "aspect": "4:5", "ops": ops}
    )
    assert out.status_code == 422 and said in out.text


def test_a_try_on_an_old_version_is_refused(desk: Desk) -> None:
    edit = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.3}]}
    assert desk.http.post(f"/api/posts/{desk.post_id}/edit", json=edit).status_code == 200
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/canvas/try",
        json={"version": 1, "aspect": "4:5", "ops": [{"op": "dim", "layer": "bg", "amount": 0}]},
    )
    assert out.status_code == 409


def test_a_drag_in_the_story_moves_the_story_only(desk: Desk) -> None:
    before = load_spec(desk.versions()[0]["spec"])
    box = BOX
    out = desk.http.post(
        f"/api/posts/{desk.post_id}/edit",
        json={
            "version": 1,
            "ops": [{"op": "place", "layer": "headline", "aspect": "9:16", "box": box}],
        },
    )
    assert out.status_code == 200, out.text
    after = load_spec(desk.versions()[0]["spec"])
    assert retarget(after, "9:16").layer("headline").box.model_dump() == box
    assert (
        retarget(after, "4:5").layer("headline").box
        == retarget(before, "4:5").layer("headline").box
    )
    assert desk.edits()[-1]["message"] == "Editor: headline placed in the story"
    assert canvas(desk, "9x16")["version"] == 2


def test_an_asset_is_served_only_for_a_version_that_draws_it(desk: Desk) -> None:
    body = canvas(desk)
    version_id = body["version_id"]
    spec = load_spec(desk.versions()[0]["spec"])
    photo = next(one.asset for one in spec.layers if one.id == "bg")  # type: ignore[union-attr]
    out = desk.http.get(f"/api/versions/{version_id}/assets/{photo}")
    assert out.status_code == 200, out.text
    assert out.headers["content-type"] in ("image/jpeg", "image/png")
    assert "immutable" in out.headers["cache-control"]
    with Image.open(io.BytesIO(out.content)) as image:
        assert max(image.size) <= service.WEB_SIDE
    again = desk.http.get(f"/api/versions/{version_id}/assets/{photo}")
    assert again.content == out.content  # made once, kept
    assert desk.http.get(f"/api/versions/{version_id}/assets/{'c' * 64}").status_code == 404
    assert desk.http.get(f"/api/versions/{version_id}/assets/..%2F..%2Fx").status_code == 404
    other = "00000000-0000-0000-0000-00000000dead"
    assert desk.http.get(f"/api/versions/{other}/assets/{photo}").status_code == 404
    odd = "eq.x,or(id.neq.0)"
    assert desk.http.get(f"/api/versions/{odd}/assets/{photo}").status_code == 404


def test_undo_and_redo_name_the_version_they_want(desk: Desk) -> None:
    edit = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.4}]}
    assert desk.http.post(f"/api/posts/{desk.post_id}/edit", json=edit).status_code == 200
    first, dimmed = (load_spec(row["spec"]) for row in reversed(desk.versions()))
    undo = desk.http.post(f"/api/posts/{desk.post_id}/restore", json={"version": 2, "to": 1})
    assert undo.status_code == 200, undo.text
    assert undo.json()["version"] == 3 and undo.json()["why"] == "Back to version 1."
    assert load_spec(desk.versions()[0]["spec"]) == first
    # The same press again is refused: the poster moved on.
    twice = desk.http.post(f"/api/posts/{desk.post_id}/restore", json={"version": 2, "to": 1})
    assert twice.status_code == 409
    redo = desk.http.post(f"/api/posts/{desk.post_id}/restore", json={"version": 3, "to": 2})
    assert redo.status_code == 200 and redo.json()["version"] == 4
    assert load_spec(desk.versions()[0]["spec"]) == dimmed
    assert [one["ops"][0]["op"] for one in desk.edits()][-2:] == ["restore", "restore"]


def test_restoring_a_version_that_is_not_there_is_a_sentence(desk: Desk) -> None:
    out = desk.http.post(f"/api/posts/{desk.post_id}/restore", json={"version": 1, "to": 7})
    assert out.status_code == 422 and out.text == "This post has no version 7."
    same = desk.http.post(f"/api/posts/{desk.post_id}/restore", json={"version": 1, "to": 1})
    assert same.status_code == 422


def test_fonts_are_the_renderers_own_files_only(desk: Desk) -> None:
    main.app.dependency_overrides[main.config] = lambda: main.settings().model_copy(
        update={"repo_root": REPO}
    )
    out = desk.http.get("/fonts/Anton-Regular.ttf")
    assert out.status_code == 200 and out.content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")
    assert desk.http.get("/fonts/LICENSES.md").status_code == 404
    assert desk.http.get("/fonts/..%2F..%2Fpyproject.toml").status_code == 404
    assert desk.http.get("/fonts/Missing.ttf").status_code == 404


def test_the_editor_is_served_from_its_build(desk: Desk, tmp_path: Path) -> None:
    dist = tmp_path / "apps" / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Stratum</title>")
    (dist / "assets" / "app-1a2b.js").write_text("console.log(1)")
    main.app.dependency_overrides[main.config] = lambda: main.settings().model_copy(
        update={"repo_root": tmp_path}
    )
    try:
        page = desk.http.get("/app/posts/123")
        assert page.status_code == 200 and "Stratum" in page.text
        assert page.headers["cache-control"] == "no-cache"
        built = desk.http.get("/app/assets/app-1a2b.js")
        assert built.status_code == 200 and "immutable" in built.headers["cache-control"]
        sneaky = desk.http.get("/app/..%2F..%2F..%2Fpyproject.toml")
        assert sneaky.status_code == 200 and "Stratum" in sneaky.text  # the page, not the file
    finally:
        del main.app.dependency_overrides[main.config]
