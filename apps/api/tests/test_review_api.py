"""The review routes: what a client's browser gets back, and what it never gets.

The database functions are tested against Postgres in test_reviews_contract.py; here
the anonymous database calls are stubbed so these tests pin the HTTP contract: status
codes, plain-English errors, and the headers that keep a link out of other sites,
caches and search engines.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import reviews
from stratum_worker.app.settings import Settings

TOKEN = "t" * 43
STATIC = Path(main.__file__).parent / "static"


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    main.app.dependency_overrides[main.config] = lambda: Settings(
        SUPABASE_URL="https://example.supabase.co", SUPABASE_ANON_KEY="anon"
    )
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def private(res: Any) -> bool:
    return (
        res.headers.get("referrer-policy") == "no-referrer"
        and res.headers.get("cache-control") == "no-store"
        and "noindex" in res.headers.get("x-robots-tag", "")
    )


def test_the_page_is_served_privately(http: TestClient) -> None:
    res = http.get(f"/r/{TOKEN}")
    assert res.status_code == 200 and private(res)
    assert '<meta name="referrer" content="no-referrer">' in res.text


def test_the_page_escapes_what_it_shows_and_loads_nothing_from_other_sites() -> None:
    page = (STATIC / "review.html").read_text()
    assert "const esc = (value)" in page
    assert "https://" not in page.split("<script>")[0]  # no third-party fonts or styles


def test_a_link_that_does_not_work_says_so_plainly(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(anon: Any, token: str) -> None:
        return None

    monkeypatch.setattr(reviews, "open_review", nothing)
    res = http.get(f"/api/review/{TOKEN}")
    assert res.status_code == 404 and private(res)
    assert "expired or is not valid" in res.json()["detail"]


def test_a_working_link_returns_the_review_privately(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def found(anon: Any, token: str) -> dict[str, Any]:
        # The anonymous role, never a user's token. "anon" is the test's fake key.
        assert anon.access_token == "anon"  # noqa: S105
        return {"client": "Kowalski", "items": []}

    monkeypatch.setattr(reviews, "open_review", found)
    res = http.get(f"/api/review/{TOKEN}")
    assert res.status_code == 200 and private(res)
    assert res.json()["client"] == "Kowalski"


@pytest.mark.parametrize(
    "body",
    [
        {"item_id": "x", "verdict": "maybe"},
        {"item_id": "x", "verdict": "changes", "comment": "c" * 2001},
        {"item_id": "x", "verdict": "changes", "comment": "here", "pin": {"x": 1.5, "y": 0}},
    ],
)
def test_malformed_decisions_never_reach_the_database(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    async def unreachable(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr(reviews, "decide", unreachable)
    assert http.post(f"/api/review/{TOKEN}/decide", json=body).status_code == 422


def test_a_refused_decision_comes_back_as_a_sentence(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise reviews.ReviewError("say what should change")

    monkeypatch.setattr(reviews, "decide", refuse)
    res = http.post(
        f"/api/review/{TOKEN}/decide", json={"item_id": "x", "verdict": "changes", "comment": ""}
    )
    assert res.status_code == 400 and private(res)
    assert res.json()["detail"] == "say what should change"


def test_a_decision_passes_the_pin_through(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def record(anon: Any, token: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs, token=token)
        return {"verdict": kwargs["verdict"]}

    monkeypatch.setattr(reviews, "decide", record)
    res = http.post(
        f"/api/review/{TOKEN}/decide",
        json={
            "item_id": "i",
            "verdict": "changes",
            "comment": "bigger",
            "pin": {"x": 0.2, "y": 0.8, "aspect": "4:5"},
        },
    )
    assert res.status_code == 200 and private(res)
    assert seen["pin"] == {"x": 0.2, "y": 0.8, "aspect": "4:5"} and seen["token"] == TOKEN


def test_a_pin_without_a_format_sends_no_format(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def record(anon: Any, token: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(reviews, "decide", record)
    body = {"item_id": "i", "verdict": "changes", "comment": "c", "pin": {"x": 0, "y": 1}}
    assert http.post(f"/api/review/{TOKEN}/decide", json=body).status_code == 200
    # A JSON null "aspect" would be refused by the database; absent is what it expects.
    assert seen["pin"] == {"x": 0, "y": 1}
