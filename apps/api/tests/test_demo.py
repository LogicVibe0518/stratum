"""One link for a CV: "/" opens the editor, and "Try the demo" needs no password.

What a reviewer should be able to repeat back:
- "/" sends everyone to the editor (/app); the first page lives on at /classic;
- POST /api/demo signs in to the shared demo account on the server and returns only the
  session: the password never reaches a page; off (404) unless both settings are set;
- it is rate-limited per visitor and overall;
- the demo account edits but cannot upload, add clients or posts, change the brand, or
  answer the Taste Lab (whose answers train the ranker), and its AI spend has its own
  small daily cap.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.app.settings import Settings
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.errors import BudgetExceeded
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]

DEMO = "Demo@Example.com"
SECRET = "not-a-real-password-0"  # noqa: S105 - a test value, never a real one


def settings(demo: bool = True) -> Settings:
    # Both values are always given: Settings also reads the developer's own .env, and a
    # laptop that runs the demo has DEMO_EMAIL there (pre-push, 28 Oct: "off" read as on).
    extra = (
        {"DEMO_EMAIL": DEMO, "DEMO_PASSWORD": SECRET}
        if demo
        else {"DEMO_EMAIL": "", "DEMO_PASSWORD": ""}
    )
    return Settings(
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_ANON_KEY="sb_publishable_example",
        **extra,  # type: ignore[arg-type]
    )


def token(email: str) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'ES256'})}.{part({'email': email, 'sub': 'u'})}.sig"


@pytest.fixture
def http() -> Iterator[TestClient]:
    main._demo_calls.clear()
    main.app.dependency_overrides[main.config] = settings
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main._demo_calls.clear()


def test_the_one_link_opens_the_editor_and_the_first_page_moves(http: TestClient) -> None:
    out = http.get("/", follow_redirects=False)
    assert out.status_code == 307 and out.headers["location"] == "/app"
    page = http.get("/classic")
    assert page.status_code == 200 and "<title>" in page.text


def test_the_page_is_told_whether_there_is_a_demo(http: TestClient) -> None:
    assert http.get("/api/config").json()["demo"] is True
    main.app.dependency_overrides[main.config] = lambda: settings(demo=False)
    assert http.get("/api/config").json()["demo"] is False
    assert http.post("/api/demo").status_code == 404


def test_the_demo_session_comes_from_the_server_without_the_password(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    async def post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, **kwargs})
        body = {"access_token": "a", "refresh_token": "r", "expires_at": 123, "user": {}}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    out = http.post("/api/demo")
    assert out.status_code == 200
    body = out.json()
    assert body == {
        "access_token": "a",
        "refresh_token": "r",
        "expires_at": 123,
        "user": {"email": DEMO},
        "demo": True,
    }
    assert SECRET not in out.text
    [call] = sent
    assert call["url"] == "https://example.supabase.co/auth/v1/token?grant_type=password"
    assert call["json"] == {"email": DEMO, "password": SECRET}


def test_a_refused_demo_sign_in_is_a_sentence(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    out = http.post("/api/demo")
    assert out.status_code == 503 and "not available" in out.text


def test_one_visitor_cannot_sign_in_over_and_over(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    codes = [
        http.post("/api/demo", headers={"x-forwarded-for": "203.0.113.9"}).status_code
        for _ in range(main.DEMO_PER_IP[0] + 1)
    ]
    assert codes[:-1] == [200] * main.DEMO_PER_IP[0] and codes[-1] == 429
    assert http.post("/api/demo", headers={"x-forwarded-for": "198.51.100.4"}).status_code == 200


def test_the_demo_is_recognised_by_its_email_only() -> None:
    on = settings()
    assert main.is_demo(token("demo@example.com"), on)
    assert main.is_demo(token(" DEMO@example.COM "), on)
    assert not main.is_demo(token("owner@agency.example"), on)
    assert not main.is_demo("not-a-token", on)
    assert not main.is_demo(token("demo@example.com"), settings(demo=False))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/clients"),
        ("post", "/api/clients/c/assets"),
        ("put", "/api/clients/c/brand"),
        ("post", "/api/posts"),
        ("post", "/api/lab/screens"),
        ("post", "/api/lab/screens/s/answer"),
    ],
)
def test_the_demo_cannot_upload_add_or_train(http: TestClient, method: str, path: str) -> None:
    async def demo_context() -> Any:
        return type("Ctx", (), {"demo": True})()

    main.app.dependency_overrides[main.context] = demo_context
    out = getattr(http, method)(path, json={})
    # Refused by the dependency, before the body is looked at.
    assert out.status_code == 403 and "shared demo" in out.text


def test_the_demo_has_its_own_small_daily_cap() -> None:
    budget = Budget(daily_cap_usd=5.0, caps={"demo-agency": main.DEMO_DAILY_CAP_USD})
    budget.check("real-agency", 4.0)
    with pytest.raises(BudgetExceeded):
        budget.check("demo-agency", 0.6)
    budget.record("demo-agency", 0.4)
    assert budget.warn_level("demo-agency") == pytest.approx(0.8)


def test_running_out_of_allowance_is_a_sentence_not_a_500(http: TestClient) -> None:
    @main.app.get("/test-only/over-budget")
    def over() -> None:
        raise BudgetExceeded("agency x would exceed its daily cap")

    try:
        out = http.get("/test-only/over-budget")
        assert out.status_code == 429 and "allowance" in out.text
    finally:
        main.app.router.routes = [
            route
            for route in main.app.router.routes
            if getattr(route, "path", "") != "/test-only/over-budget"
        ]


# --- the shared posts go back to their first drafts --------------------------------
#
# Shared with a 350-person group on 29 Oct: everyone edits the same posts, so the
# demo is put back as drafted at most every DEMO_RESET_S, by the next sign-in.


def test_a_reset_is_due_at_most_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_demo_reset_at", [])
    assert main._reset_due(1000.0) is True
    assert main._reset_due(1000.0 + main.DEMO_RESET_S - 1) is False
    assert main._reset_due(1000.0 + main.DEMO_RESET_S) is True


def test_a_demo_sign_in_starts_the_reset_only_when_it_is_due(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "tok", "refresh_token": "r"})

    started: list[str] = []

    async def reset(token: str, conf: Settings) -> None:
        started.append(token)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(main, "_reset_demo", reset)
    monkeypatch.setattr(main, "_demo_reset_at", [])
    assert http.post("/api/demo").status_code == 200
    assert http.post("/api/demo").status_code == 200
    assert started == ["tok"]


def test_the_reset_brings_back_the_first_draft_and_draft_status(tmp_path: Path) -> None:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))

    def ctx(demo: bool = True) -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
            demo=demo,
        )

    # The post is drafted as the agency would; a visitor then edits and archives it.
    main.app.dependency_overrides[main.context] = lambda: ctx(demo=False)
    try:
        http = TestClient(main.app)
        made = http.post(
            "/api/posts",
            json={"client_id": client, "brief": "Boiler service, 20% off.", "slug": "demo-post"},
        )
        post_id = made.json()["post"]["id"]
        main.app.dependency_overrides[main.context] = ctx
        edit = {"version": 1, "ops": [{"op": "dim", "layer": "bg", "amount": 0.4}]}
        assert http.post(f"/api/posts/{post_id}/edit", json=edit).status_code == 200
        assert http.post(f"/api/posts/{post_id}/status", data={"status": "archived"}).is_success
    finally:
        main.app.dependency_overrides.clear()

    assert asyncio.run(main.reset_demo_posts(ctx())) == 1
    supa = world.factory(agency)
    versions = asyncio.run(supa.select("post_versions", post_id=f"eq.{post_id}", order="version"))
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[-1]["spec"] == versions[0]["spec"]
    [post] = asyncio.run(supa.select("posts", id=f"eq.{post_id}"))
    assert post["status"] == "draft"
    # Nothing left to put back: a second reset changes nothing and adds no version.
    assert asyncio.run(main.reset_demo_posts(ctx())) == 0
    assert len(asyncio.run(supa.select("post_versions", post_id=f"eq.{post_id}"))) == 3
