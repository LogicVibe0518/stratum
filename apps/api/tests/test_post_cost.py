"""What a post has cost, shown in the editor's top bar.

- the draft (copy, caption, a painted background) plus every plain-English change;
- a change made by hand (or an instant button) is counted, and costs nothing;
- the agency's daily AI allowance comes with it, the demo's own when it is the demo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


def test_the_cost_is_the_draft_plus_every_paid_change() -> None:
    post = {"cost_usd": 0.0016}
    spent = [{"cost_usd": 0.000517}, {"cost_usd": 0}, {"cost_usd": "0.000412"}, {"cost_usd": None}]
    assert main.post_cost(post, spent, 5.0) == {
        "draft_usd": 0.0016,
        "changes_usd": 0.000929,
        "total_usd": 0.002529,
        "paid_changes": 2,
        "free_changes": 2,
        "daily_cap_usd": 5.0,
    }


def test_a_post_says_what_it_cost(tmp_path: Path) -> None:
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
        )

    main.app.dependency_overrides[main.context] = context
    try:
        http = TestClient(main.app)
        made = http.post(
            "/api/posts",
            json={"client_id": client, "brief": "Boiler service, 20% off.", "slug": "cost"},
        )
        assert made.status_code == 200, made.text
        post_id = made.json()["post"]["id"]
        version = made.json()["version"]["version"]
        moved = http.post(
            f"/api/posts/{post_id}/edit",
            json={
                "version": version,
                "ops": [{"op": "set_visible", "layer": "logo", "visible": False}],
            },
        )
        assert moved.status_code == 200, moved.text
        cost = http.get(f"/api/posts/{post_id}").json()["cost"]
        assert cost["free_changes"] == 1 and cost["paid_changes"] == 0
        assert cost["total_usd"] == cost["draft_usd"] >= 0
        assert cost["daily_cap_usd"] > 0
    finally:
        main.app.dependency_overrides.clear()
