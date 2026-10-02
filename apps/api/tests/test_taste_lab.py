"""The Taste Lab over HTTP: screens made from real posts, rated by people, exported.

The promises:
- screens are made from the agency's own posts, by a job, with shared screens marked;
- every rater sees the shared screens first, in their own shuffled order;
- an answer is checked (the designs are on the screen, the reasons are known) and the
  same serving sent twice is one answer;
- repeats come back to measure self-consistency; agreement on shared screens is
  measured between raters;
- the export has every answer with the designs it was about.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.app import lab, service
from stratum_worker.design import choose as chooser
from stratum_worker.queue import fake, worker
from stratum_worker.queue.fake import World
from stratum_worker.queue.jobs import Job

REPO = Path(__file__).resolve().parents[3]
ALICE = "00000000-0000-0000-0000-00000000a11c"
BOB = "00000000-0000-0000-0000-000000000b0b"


@dataclass
class Lab:
    http: TestClient
    world: World
    agency: str
    who: dict[str, str] = field(default_factory=lambda: {"user": ALICE})

    def ctx(self, user: str | None = None) -> service.Context:
        return service.Context(
            supa=self.world.factory(self.agency),
            settings=self.world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=self.agency,
            user_id=user or self.who["user"],
        )

    def as_(self, user: str) -> TestClient:
        self.who["user"] = user
        return self.http

    def answers(self) -> list[dict[str, Any]]:
        return asyncio.run(
            self.world.factory(self.agency).select("decision_log", kind="eq.designer_pick")
        )


@pytest.fixture
def lab_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    monkeypatch.setattr(chooser, "MAX_CANDIDATES", 40)
    monkeypatch.setattr(chooser, "SHORTLIST", 8)
    world = World(tmp_path / "db.sqlite")
    agency, client = asyncio.run(fake.seed(world, REPO))
    one = Lab(TestClient(main.app), world, agency)
    main.app.dependency_overrides[main.context] = lambda: one.ctx()
    try:
        made = one.http.post(
            "/api/posts",
            json={
                "client_id": client,
                "brief": "Boiler service, 20% off this month.",
                "slug": "lab-post",
                "template_id": "ds.panel.1.0010110",
                "aspects": ["4:5"],
            },
        )
        assert made.status_code == 200, made.text
        result = asyncio.run(lab.make_screens(one.ctx(), per_post=4, shared_per_post=2))
        assert result["screens"] == 4, result
        yield one
    finally:
        main.app.dependency_overrides.clear()


def answer(http: TestClient, screen: dict[str, Any], picked: int | None, **more: Any) -> Any:
    return http.post(
        f"/api/lab/screens/{screen['screen_id']}/answer",
        json={"served": screen["served"], "picked": picked, "ms": 4200, **more},
    )


def test_screens_are_made_from_the_agencys_posts(lab_world: Lab) -> None:
    progress = lab_world.http.get("/api/lab/progress").json()
    assert progress["screens"] == 4 and progress["shared_screens"] == 2
    assert progress["answers"] == 0


def test_a_rater_sees_shared_screens_first_with_four_pictures(lab_world: Lab) -> None:
    screen = lab_world.http.get("/api/lab/next").json()
    assert screen["done"] is False and screen["repeat"] is False
    assert len(screen["cards"]) == 4
    assert sorted(card["design"] for card in screen["cards"]) == [0, 1, 2, 3]
    assert screen["post"]["brief"] == "Boiler service, 20% off this month."
    assert "Hard to read" in screen["reasons"]
    picture = lab_world.http.get(screen["cards"][0]["preview"].replace("{shape}", "4x5"))
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    rows = asyncio.run(
        lab_world.world.factory(lab_world.agency).select(
            "lab_screens", id=f"eq.{screen['screen_id']}"
        )
    )
    assert rows[0]["shared"] is True


def test_an_answer_is_stored_with_the_order_it_was_shown_in(lab_world: Lab) -> None:
    screen = lab_world.http.get("/api/lab/next").json()
    out = answer(lab_world.http, screen, 2, never=[0], reasons=["Hard to read"])
    assert out.status_code == 200, out.text
    [row] = lab_world.answers()
    detail = row["detail"]
    assert detail["source"] == "lab" and detail["picked"] == 2 and detail["never"] == [0]
    assert detail["order"] == [card["design"] for card in screen["cards"]]
    assert row["actor"] == ALICE and row["version_id"] is None
    assert out.json()["progress"]["mine"] == 1


def test_the_same_serving_sent_twice_is_one_answer(lab_world: Lab) -> None:
    screen = lab_world.http.get("/api/lab/next").json()
    assert answer(lab_world.http, screen, 1).status_code == 200
    assert answer(lab_world.http, screen, 1).status_code == 200
    assert len(lab_world.answers()) == 1


@pytest.mark.parametrize(
    ("picked", "more", "said"),
    [
        (7, {}, "not on this screen"),
        (1, {"never": [1]}, "both the pick and 'never send'"),
        (1, {"never": [9]}, "'never send' is not on this screen"),
        (1, {"reasons": ["Too blue"]}, "Unknown reason: Too blue."),
    ],
)
def test_an_answer_that_cannot_be_right_is_a_sentence(
    lab_world: Lab, picked: int, more: dict[str, Any], said: str
) -> None:
    screen = lab_world.http.get("/api/lab/next").json()
    out = answer(lab_world.http, screen, picked, **more)
    assert out.status_code == 422 and said in out.text
    assert lab_world.answers() == []


def test_none_of_these_is_an_answer(lab_world: Lab) -> None:
    screen = lab_world.http.get("/api/lab/next").json()
    assert answer(lab_world.http, screen, None, never=[0, 1]).status_code == 200
    assert lab_world.answers()[0]["detail"]["picked"] is None


def test_agreement_between_raters_is_measured_on_shared_screens(lab_world: Lab) -> None:
    rows = asyncio.run(lab_world.world.factory(lab_world.agency).select("lab_screens"))
    shared = sorted(one["id"] for one in rows if one["shared"])
    # Both raters pick design 0 on the first shared screen, and differ on the second.
    for user, picks in ((ALICE, (0, 1)), (BOB, (0, 2))):
        for screen_id, picked in zip(shared, picks, strict=True):
            asyncio.run(
                lab.answer(
                    lab_world.ctx(user),
                    screen_id,
                    served=str(uuid.uuid4()),
                    picked=picked,
                    never=[],
                    reasons=[],
                    ms=1000,
                    repeat=False,
                )
            )
    progress = lab_world.as_(ALICE).get("/api/lab/progress").json()
    assert progress["raters"] == 2
    assert progress["agreement"] == {"pairs": 2, "rate": 0.5}


def test_the_order_on_screen_varies_and_is_reproducible() -> None:
    orders = {
        tuple(lab.display_order("screen", user, str(uuid.uuid4()), 4))
        for user in (ALICE, BOB)
        for _ in range(10)
    }
    assert len(orders) > 3
    served = str(uuid.uuid4())
    assert lab.display_order("s", ALICE, served, 4) == lab.display_order("s", ALICE, served, 4)


def test_repeats_measure_self_consistency(lab_world: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lab, "REPEAT_AFTER", 2)
    monkeypatch.setattr(lab, "REPEAT_RATE", 1.0)
    monkeypatch.setattr(lab, "RECENT", 0)
    picks: dict[str, int] = {}
    for _ in range(2):
        screen = lab_world.http.get("/api/lab/next").json()
        picks[screen["screen_id"]] = 1
        assert answer(lab_world.http, screen, 1).status_code == 200
    again = lab_world.http.get("/api/lab/next").json()
    assert again["repeat"] is True and again["screen_id"] in picks
    assert answer(lab_world.http, again, 1, repeat=True).status_code == 200
    progress = lab_world.http.get("/api/lab/progress").json()
    assert progress["self_consistency"] == {"repeats": 1, "rate": 1.0}


def test_a_rater_who_has_seen_everything_is_done(lab_world: Lab) -> None:
    for _ in range(4):
        screen = lab_world.http.get("/api/lab/next").json()
        assert answer(lab_world.http, screen, 0).status_code == 200
    last = lab_world.http.get("/api/lab/next").json()
    assert last["done"] is True and last["progress"]["mine"] == 4


def test_the_export_has_every_answer_with_its_designs(lab_world: Lab) -> None:
    for _ in range(2):
        screen = lab_world.http.get("/api/lab/next").json()
        answer(lab_world.http, screen, 3, reasons=["Cluttered"])
    out = lab_world.http.get("/api/lab/export")
    assert out.status_code == 200 and out.headers["content-type"] == "application/x-ndjson"
    lines = [json.loads(line) for line in out.text.splitlines()]
    assert len(lines) == 2
    assert lines[0]["rater"] == 1 and lines[0]["you"] is True
    assert len(lines[0]["designs"]) == 4 and "spec" in lines[0]["designs"][0]
    assert {one["bucket"] for one in lines[0]["designs"]} <= {"top", "middle", "any"}


def test_making_screens_is_a_job_run_as_the_person_who_asked(lab_world: Lab) -> None:
    out = lab_world.http.post("/api/lab/screens", json={"posts": 1, "per_post": 2})
    assert out.status_code == 200, out.text
    [row] = asyncio.run(
        lab_world.world.factory(lab_world.agency).select("jobs", id=f"eq.{out.json()['job_id']}")
    )
    assert row["kind"] == "lab_screens" and row["payload"]["per_post"] == 2
    now = datetime.now(UTC)
    job = Job(
        id=row["id"],
        agency_id=lab_world.agency,
        kind="lab_screens",
        payload=row["payload"],
        status="running",
        attempts=1,
        max_attempts=3,
        requested_by=ALICE,
        run_after=now,
        created_at=now,
        updated_at=now,
    )
    deps = worker.Deps(settings=lab_world.world.settings(REPO), gateway=fake.gateway())
    result = asyncio.run(worker.lab_screens(job, lab_world.world.factory(lab_world.agency), deps))
    assert result["screens"] == 2
