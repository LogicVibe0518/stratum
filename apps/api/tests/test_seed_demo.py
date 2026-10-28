"""tools/seed_demo.py: one command fills the shared demo account.

What a reviewer should be able to repeat back:
- it adds the fictional clients with their brand kits, logos and photos, and drafts the
  posts through the queue, the same calls the page makes;
- running it again adds nothing and queues nothing, so it costs nothing;
- an account with no agency gets one; a refused step or a failed draft stops it with
  the server's own words; a draft no worker picks up stops it with what to check;
- it never prints the password or the token, and says which settings are missing.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stratum_api import main
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World
from stratum_worker.queue.worker import Deps, QueueWorker

REPO = Path(__file__).resolve().parents[3]
SECRET = "not-a-real-password-1"  # noqa: S105 - a test value, never a real one


def tool() -> ModuleType:
    path = REPO / "tools" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("stratum_seed_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def jpeg(seed: int) -> bytes:
    """Sharp and bright enough to pass intake, different for every seed."""
    y, x = np.mgrid[0:1350, 0:1080]
    edges = 40 * (((x // 6) + (y // 6) + seed) % 2)
    base = 120 + 50 * np.sin(x / (9.0 + seed)) * np.cos(y / 11.0) + edges
    rgb = np.stack([base, base * 0.8 + 30, 255 - base * 0.5], axis=-1).clip(0, 255)
    out = io.BytesIO()
    Image.fromarray(rgb.astype("uint8"), "RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    """Two photos per client and no logo: the logo comes from clients/<slug>/."""
    root = tmp_path / "photos"
    for n, slug in enumerate(("sparkle-squad", "lone-star-roofing")):
        (root / slug).mkdir(parents=True)
        for i in (1, 2):
            (root / slug / f"{slug}-0{i}.jpg").write_bytes(jpeg(4 * n + i))
        (root / slug / "notes.txt").write_text("not a photo")
    return root


@pytest.fixture
def studio(tmp_path: Path) -> Iterator[tuple[TestClient, World, QueueWorker]]:
    """The API on a fake database, signed in to an empty agency, and one queue worker."""
    world = World(tmp_path / "db.sqlite")
    agency = str(uuid.uuid4())

    def context() -> service.Context:
        return service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
            user_id=None,
        )

    worker = QueueWorker(
        ledger=world.ledger(),
        factory=world.scoped,
        deps=Deps(settings=world.settings(REPO), gateway=fake.gateway()),
    )
    main.app.dependency_overrides[main.context] = context
    try:
        yield TestClient(main.app), world, worker
    finally:
        main.app.dependency_overrides.clear()


def test_it_fills_an_empty_agency_and_a_second_run_adds_nothing(
    studio: tuple[TestClient, World, QueueWorker], photos: Path
) -> None:
    seeding = tool()
    http, world, worker = studio
    briefs = (seeding.POSTS[0], seeding.POSTS[2])  # one post each for two clients
    said: list[str] = []

    def work(_seconds: float) -> None:
        asyncio.run(worker.run_once())

    seeding.seed(http, photos, said.append, briefs=briefs, pause=work)

    clients = http.get("/api/clients").json()
    assert sorted(row["slug"] for row in clients) == ["lone-star-roofing", "sparkle-squad"]
    sparkle = next(row for row in clients if row["slug"] == "sparkle-squad")
    brand = http.get(f"/api/clients/{sparkle['id']}/brand").json()
    kit = json.loads((REPO / "clients" / "sparkle-squad" / "brand.json").read_text())
    assert brand["kit"]["palette"] == kit["palette"] and brand["starter"] is False
    files = http.get(f"/api/clients/{sparkle['id']}/assets").json()
    assert sorted(row["kind"] for row in files) == ["logo", "photo", "photo"]
    posts = http.get("/api/posts").json()
    assert sorted(row["slug"] for row in posts) == sorted(brief.slug for brief in briefs)
    assert all(row["status"] == "done" for row in world.jobs())
    assert "drafted black-friday-deep-clean" in said

    jobs, said[:] = len(world.jobs()), []
    seeding.seed(http, photos, said.append, briefs=briefs, pause=work)
    assert len(http.get(f"/api/clients/{sparkle['id']}/assets").json()) == 3
    assert len(http.get("/api/posts").json()) == 2
    assert len(world.jobs()) == jobs
    assert not [line for line in said if line.startswith(("added", "uploaded"))]


# --- the steps on their own, against a stand-in server ------------------------------


def server(answers: dict[tuple[str, str], Any]) -> tuple[httpx.Client, list[str]]:
    """A client whose server answers by method and path; it records what was asked."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        asked.append(f"{request.method} {request.url.path}")
        status, body = answers[key]() if callable(answers[key]) else answers[key]
        return httpx.Response(status, json=body)

    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handle))
    return client, asked


def test_an_account_without_an_agency_gets_one() -> None:
    seeding = tool()
    api, asked = server(
        {
            ("GET", "/api/clients"): (403, {"detail": "you are not in an agency yet"}),
            ("POST", "/api/onboard"): (200, {"agency_id": "a"}),
        }
    )
    seeding.ensure_agency(api)
    assert asked == ["GET /api/clients", "POST /api/onboard"]


def test_a_draft_that_fails_stops_with_the_reason() -> None:
    seeding = tool()
    api, _ = server(
        {
            ("GET", "/api/posts"): (200, []),
            ("POST", "/api/posts"): (200, {"job_id": "j1"}),
            ("GET", "/api/jobs/j1"): (200, {"status": "failed", "last_error": "no photo"}),
        }
    )
    with pytest.raises(seeding.SeedError, match="no photo"):
        seeding.ensure_post(api, "c1", seeding.POSTS[0], print, lambda _s: None)


def test_a_draft_no_worker_picks_up_says_to_check_the_worker() -> None:
    seeding = tool()
    api, asked = server(
        {
            ("GET", "/api/posts"): (200, []),
            ("POST", "/api/posts"): (200, {"job_id": "j1"}),
            ("GET", "/api/jobs/j1"): (200, {"status": "queued"}),
        }
    )
    with pytest.raises(seeding.SeedError, match="is the worker running"):
        seeding.ensure_post(api, "c1", seeding.POSTS[0], print, lambda _s: None)
    polls = asked.count("GET /api/jobs/j1")
    assert polls == int(seeding.DRAFT_TIMEOUT_S / seeding.POLL_S) + 1


def test_a_refused_step_is_reported_in_the_servers_words() -> None:
    seeding = tool()
    api, _ = server({("GET", "/api/clients"): (403, {"detail": "The demo can only edit."})})
    with pytest.raises(seeding.SeedError, match="The demo can only edit"):
        seeding.ensure_agency(api)


def test_signing_in_never_repeats_the_password() -> None:
    seeding = tool()
    sent: list[dict[str, Any]] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "invalid_grant", "password": SECRET})

    auth = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(seeding.SeedError) as refused:
        seeding.sign_in(auth, "https://x.supabase.co", "sb_publishable_x", "d@x.test", SECRET)
    assert SECRET not in str(refused.value)
    assert sent == [{"email": "d@x.test", "password": SECRET}]

    def accept(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "sb_publishable_x"
        return httpx.Response(200, json={"access_token": "tok"})

    auth = httpx.Client(transport=httpx.MockTransport(accept))
    assert seeding.sign_in(auth, "https://x.supabase.co/", "sb_publishable_x", "d", SECRET) == "tok"


def test_missing_settings_are_named_and_nothing_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeding = tool()
    monkeypatch.setattr(seeding, "REPO", tmp_path)  # no .env here
    for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "DEMO_EMAIL", "DEMO_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEMO_PASSWORD", SECRET)
    assert seeding.main(["--photos", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "SUPABASE_URL, SUPABASE_ANON_KEY, DEMO_EMAIL" in err
    assert SECRET not in err
