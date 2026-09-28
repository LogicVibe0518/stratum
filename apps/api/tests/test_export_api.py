"""GET /api/posts/{id}/canva.pptx: one size of a post as an editable PowerPoint file."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def post(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    world = World(tmp_path / "db.sqlite")
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
        http = TestClient(main.app)
        made = http.post(
            "/api/posts",
            json={
                "client_id": client,
                "brief": "20% off boiler servicing.",
                "slug": "boiler-offer",
                "aspects": ["4:5", "1:1"],
            },
        )
        assert made.status_code == 200, made.text
        yield http, made.json()["post"]["id"]
    finally:
        main.app.dependency_overrides.clear()


def test_a_size_downloads_as_a_named_powerpoint_file(post: tuple[TestClient, str]) -> None:
    http, post_id = post
    res = http.get(f"/api/posts/{post_id}/canva.pptx", params={"aspect": "4:5"})
    assert res.status_code == 200
    assert res.headers["content-type"] == main.PPTX
    assert 'filename="boiler-offer-4x5.pptx"' in res.headers["content-disposition"]
    deck = Presentation(io.BytesIO(res.content))
    assert deck.slide_height > deck.slide_width  # 4:5 is portrait


def test_a_size_the_post_does_not_have_is_a_404(post: tuple[TestClient, str]) -> None:
    http, post_id = post
    res = http.get(f"/api/posts/{post_id}/canva.pptx", params={"aspect": "9:16"})
    assert res.status_code == 404
    assert "no 9:16 version" in res.json()["detail"]


def test_a_size_that_does_not_exist_is_refused_before_any_work(
    post: tuple[TestClient, str],
) -> None:
    http, post_id = post
    assert http.get(f"/api/posts/{post_id}/canva.pptx", params={"aspect": "5:4"}).status_code == 422
