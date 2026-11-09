"""Each visitor to the shared demo works on their own copies of its posts.

What a reviewer should be able to repeat back:
- a demo sign-in names the visitor; a visitor who comes back with their name keeps it,
  and anything that is not one of our names is replaced;
- the first time a visitor lists the posts they get their own copies of the originals
  (first draft, the three designs, the sizes), made once even if asked twice;
- a visitor sees and changes only their own copies: another visitor's post, or an
  original, is "no such post", and an edit to one copy leaves every other post alone;
- an editor with no visitor name (opened before this) sees only the originals;
- copies older than a day are deleted, originals and fresh copies never;
- a visitor can only share their own posts for review, and lists only their links;
- the demo's daily AI cap is a setting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from stratum_api import main, visitors
from stratum_worker.app import service
from stratum_worker.app.settings import Settings
from stratum_worker.design.choose import AUTO
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
A, B = "a1a1a1a1a1a1", "b2b2b2b2b2b2"


def make_settings(**extra: Any) -> Settings:
    return Settings(
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_ANON_KEY="sb_publishable_example",
        DEMO_EMAIL="demo@example.com",
        DEMO_PASSWORD="not-a-real-password-2",  # noqa: S106 - a test value
        **extra,
    )


def demo_settings() -> Settings:
    return make_settings()


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[tuple[World, str, str, list[str]]]:
    """A demo agency with two original posts, drafted the way the seed tool drafts them."""
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))
    main.app.dependency_overrides[main.context] = lambda: ctx(world, agency, demo=False)
    try:
        http = TestClient(main.app)
        made = [
            http.post(
                "/api/posts",
                json={"client_id": client, "brief": brief, "slug": slug, "template_id": AUTO},
            )
            for slug, brief in (
                ("boiler-offer", "Boiler service, 20% off."),
                ("winter", "Book a winter check."),
            )
        ]
        assert all(m.status_code == 200 for m in made), [m.text for m in made]
        yield world, agency, client, [m.json()["post"]["id"] for m in made]
    finally:
        main.app.dependency_overrides.clear()


def ctx(
    world: World, agency: str, *, demo: bool = True, visitor: str | None = None
) -> service.Context:
    return service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
        demo=demo,
        visitor=visitor,
    )


def as_(world: World, agency: str, visitor: str | None) -> TestClient:
    main.app.dependency_overrides[main.context] = lambda: ctx(world, agency, visitor=visitor)
    return TestClient(main.app)


def test_a_sign_in_names_the_visitor_and_a_returning_visitor_keeps_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(main, "_reset_due", lambda now: False)
    main._demo_calls.clear()
    main.app.dependency_overrides[main.config] = demo_settings
    try:
        http = TestClient(main.app)
        first = http.post("/api/demo").json()["visitor"]
        assert visitors.VISITOR.fullmatch(first)
        assert http.post("/api/demo", headers={"X-Demo-Visitor": first}).json()["visitor"] == first
        other = http.post("/api/demo", headers={"X-Demo-Visitor": "../../etc"}).json()["visitor"]
        assert other != "../../etc" and visitors.VISITOR.fullmatch(other)
    finally:
        main.app.dependency_overrides.clear()
        main._demo_calls.clear()


def test_each_visitor_gets_their_own_copies_once(demo: tuple[World, str, str, list[str]]) -> None:
    world, agency, _, originals = demo
    a = as_(world, agency, A).get("/api/posts").json()
    assert sorted(p["slug"] for p in a) == ["boiler-offer--" + A, "winter--" + A]
    assert not {p["id"] for p in a} & set(originals)
    # Asked again (a second tab): nothing new is made.
    again = as_(world, agency, A).get("/api/posts").json()
    assert {p["id"] for p in again} == {p["id"] for p in a}
    b = as_(world, agency, B).get("/api/posts").json()
    assert sorted(p["slug"] for p in b) == ["boiler-offer--" + B, "winter--" + B]
    # The copy is the first draft, with its designs and its sizes.
    http = as_(world, agency, A)
    copy = next(p for p in a if p["slug"].startswith("boiler-offer"))
    detail = http.get(f"/api/posts/{copy['id']}").json()
    [orig_v1] = asyncio.run(
        world.factory(agency).select("post_versions", post_id=f"eq.{originals[0]}", version="eq.1")
    )
    assert [v["version"] for v in detail["versions"]] == [1]
    [copy_v1] = asyncio.run(
        world.factory(agency).select("post_versions", post_id=f"eq.{copy['id']}", version="eq.1")
    )
    assert copy_v1["spec"] == orig_v1["spec"]
    assert len(detail["options"]) == 3 == len(http.get(f"/api/posts/{copy['id']}/options").json())
    assert {r["aspect"] for r in detail["renders"]} == {"4:5", "1:1", "9:16", "16:9"}
    main.app.dependency_overrides.clear()


def test_a_visitor_sees_and_changes_only_their_own_copies(
    demo: tuple[World, str, str, list[str]],
) -> None:
    world, agency, _, originals = demo
    mine = as_(world, agency, A).get("/api/posts").json()
    theirs = as_(world, agency, B).get("/api/posts").json()
    http = as_(world, agency, A)
    for other in [theirs[0]["id"], originals[0]]:
        assert http.get(f"/api/posts/{other}").status_code == 404
        edit = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.4}]}
        assert http.post(f"/api/posts/{other}/edit", json=edit).status_code == 404
    target = mine[0]["id"]
    edit = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.4}]}
    assert http.post(f"/api/posts/{target}/edit", json=edit).status_code == 200
    supa = world.factory(agency)
    for post in [*theirs, *[{"id": o} for o in originals], mine[1]]:
        assert len(asyncio.run(supa.select("post_versions", post_id=f"eq.{post['id']}"))) == 1
    assert len(asyncio.run(supa.select("post_versions", post_id=f"eq.{target}"))) == 2
    main.app.dependency_overrides.clear()


def test_an_editor_without_a_visitor_name_sees_only_the_originals(
    demo: tuple[World, str, str, list[str]],
) -> None:
    world, agency, _, originals = demo
    as_(world, agency, A).get("/api/posts")
    old = as_(world, agency, None).get("/api/posts").json()
    assert sorted(p["id"] for p in old) == sorted(originals)
    main.app.dependency_overrides[main.context] = lambda: ctx(world, agency, demo=False)
    everyone = TestClient(main.app).get("/api/posts").json()
    assert len(everyone) == 4  # the agency itself (not the demo) sees every post
    main.app.dependency_overrides.clear()


def test_old_copies_are_deleted_and_nothing_else(demo: tuple[World, str, str, list[str]]) -> None:
    world, agency, _, originals = demo
    as_(world, agency, A).get("/api/posts")
    as_(world, agency, B).get("/api/posts")
    main.app.dependency_overrides.clear()
    supa = world.factory(agency)
    a_ids = [p["id"] for p in asyncio.run(supa.select("posts", slug=visitors.mine(A)))]
    # A's copies were made more than a day before "now"; B's a minute before.
    later = datetime.now(UTC) + visitors.COPY_TTL + timedelta(minutes=1)
    b_made = asyncio.run(supa.select("posts", slug=visitors.mine(B)))[0]["created_at"]
    for p in asyncio.run(supa.select("posts", slug=visitors.mine(B))):
        asyncio.run(supa.update("posts", {"created_at": later.isoformat()}, id=f"eq.{p['id']}"))
    assert b_made
    gone = asyncio.run(visitors.clean_copies(ctx(world, agency), now=later))
    assert gone == 2
    left = asyncio.run(supa.select("posts"))
    assert sorted(p["id"] for p in left if visitors.MARK not in p["slug"]) == sorted(originals)
    assert all(p["slug"].endswith(B) for p in left if visitors.MARK in p["slug"])
    for pid in a_ids:  # their versions, options and sizes went with them
        assert not asyncio.run(supa.select("post_versions", post_id=f"eq.{pid}"))
        assert not asyncio.run(supa.select("post_options", post_id=f"eq.{pid}"))


def test_a_visitor_shares_only_their_own_posts(demo: tuple[World, str, str, list[str]]) -> None:
    world, agency, client, originals = demo
    theirs = as_(world, agency, B).get("/api/posts").json()
    http = as_(world, agency, A)
    mine = http.get("/api/posts").json()
    refused = http.post("/api/reviews", json={"client_id": client, "post_ids": [theirs[0]["id"]]})
    assert refused.status_code == 404
    refused = http.post("/api/reviews", json={"client_id": client, "post_ids": [originals[0]]})
    assert refused.status_code == 404
    main.app.dependency_overrides.clear()
    assert mine


def test_the_demo_cap_is_a_setting() -> None:
    assert make_settings().demo_daily_cap_usd == 2.0
    assert make_settings(DEMO_DAILY_CAP_USD=5).demo_daily_cap_usd == 5.0
