"""Fill the shared demo account with fictional clients and posts, in one command.

The demo behind "Try the demo" is an ordinary account in its own agency. This signs in
as it (DEMO_EMAIL / DEMO_PASSWORD from .env, read here and never printed), makes the
agency if there is none, adds three of the fictional benchmark clients with their brand
kits (clients/<slug>/) and their CC0 photos, and drafts a few "Best three" posts through
the live API and its queue - the same calls the page makes. Running it again adds
only what is missing, and picks up a draft that failed or timed out.

Run it BEFORE setting DEMO_EMAIL / DEMO_PASSWORD on the API's host: once the server
knows the account is the demo, uploads and new posts are refused for it.

    uv run python tools/seed_demo.py --photos ~/dev/stratum-photos
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
LIVE = "https://stratum-production-d7bd.up.railway.app"
AGENCY = "Demo Agency (fictional clients)"
ASPECTS = ["4:5", "1:1", "9:16", "16:9"]
# A draft lays out many designs and runs the copy model: give the worker time.
DRAFT_TIMEOUT_S = 300.0
POLL_S = 3.0


@dataclass(frozen=True)
class Brief:
    client: str
    slug: str
    text: str


POSTS = (
    Brief(
        "sparkle-squad",
        "black-friday-deep-clean",
        "Black Friday: 30% off a deep clean booked before 30 November. Tampa and Brandon.",
    ),
    Brief(
        "sparkle-squad",
        "move-out-cleaning",
        "Moving out? Our move-out cleaning gets the deposit back. Book a week ahead.",
    ),
    Brief(
        "lone-star-roofing",
        "free-storm-inspection",
        "Free roof inspection after storm season. Book by 15 December.",
    ),
    Brief(
        "kowalski-plumbing",
        "winter-boiler-service",
        "Winter boiler service, 20% off until 31 December.",
    ),
)


class SeedError(RuntimeError):
    """A step the server refused, in its own words."""


def _say(answer: httpx.Response, what: str) -> Any:
    if answer.status_code >= 400:
        raise SeedError(f"{what}: HTTP {answer.status_code}: {answer.text[:300]}")
    return answer.json()


def sign_in(auth: httpx.Client, supabase_url: str, key: str, email: str, password: str) -> str:
    """The demo account's access token. Neither the password nor the token is printed."""
    answer = auth.post(
        f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
        headers={"apikey": key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if answer.status_code != 200:
        raise SeedError(
            f"signing in as {email} was refused (HTTP {answer.status_code}): check DEMO_EMAIL "
            "and DEMO_PASSWORD in .env, and that the user exists and is confirmed"
        )
    token = answer.json().get("access_token")
    if not isinstance(token, str):
        raise SeedError("the sign-in answer had no access token")
    return token


def ensure_agency(api: httpx.Client) -> None:
    answer = api.get("/api/clients")
    if answer.status_code == 403 and "agency" in answer.text:
        _say(api.post("/api/onboard", json={"agency_name": AGENCY}), "making the demo agency")
        return
    _say(answer, "reading the clients")


def ensure_client(api: httpx.Client, slug: str, photos: Path, say: Callable[[str], None]) -> str:
    """The client's id, adding the client and its files if they are not there yet."""
    known = {row["slug"]: row["id"] for row in _say(api.get("/api/clients"), "reading clients")}
    if slug in known:
        client_id = str(known[slug])
    else:
        about = json.loads((REPO / "clients" / slug / "client.json").read_text())
        brand = json.loads((REPO / "clients" / slug / "brand.json").read_text())
        made = _say(
            api.post(
                "/api/clients",
                json={
                    "slug": slug,
                    "name": about["name"],
                    "trade": about.get("trade", ""),
                    "brand": brand,
                    "rules": about["rules"],
                },
            ),
            f"adding {about['name']}",
        )
        client_id = str(made["id"])
        say(f"added {about['name']}")
    have = _say(api.get(f"/api/clients/{client_id}/assets"), "reading files")
    kinds = {row["kind"] for row in have}
    folder = photos / slug
    files: list[tuple[Path, str]] = []
    if "logo" not in kinds:
        logo = folder / "logo.png"
        files.append((logo if logo.is_file() else REPO / "clients" / slug / "logo.png", "logo"))
    if "photo" not in kinds:
        files += [(path, "photo") for path in sorted(folder.glob(f"{slug}-*.jpg"))]
    for path, kind in files:
        kind_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            _say(
                api.post(
                    f"/api/clients/{client_id}/assets",
                    files={"file": (path.name, handle, kind_type)},
                    data={"kind": kind},
                ),
                f"uploading {path.name}",
            )
    if files:
        say(f"uploaded {len(files)} files for {slug}")
    return client_id


def ensure_post(
    api: httpx.Client,
    client_id: str,
    brief: Brief,
    say: Callable[[str], None],
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Queue the post's draft and wait for a worker to finish it, as the page does.

    A post that already has a version is left alone, so a second run costs nothing. One
    without (its draft failed or never ran) is sent again: the same request returns the
    same job, and a job that gave up goes back in the queue, so a second run retries it.
    """
    rows = _say(api.get(f"/api/posts?client_id={client_id}"), "reading posts")
    for row in rows:
        if row.get("slug") == brief.slug:
            post = _say(api.get(f"/api/posts/{row['id']}"), f"reading {brief.slug}")
            if post.get("versions"):
                return
    queued = _say(
        api.post(
            "/api/posts?queue=true",
            json={
                "client_id": client_id,
                "brief": brief.text,
                "slug": brief.slug,
                "template_id": "best_three",
                "aspects": ASPECTS,
            },
        ),
        f"queueing {brief.slug}",
    )
    job_id = str(queued["job_id"])
    waited = 0.0
    while True:
        job = _say(api.get(f"/api/jobs/{job_id}"), f"checking {brief.slug}")
        if job["status"] == "done":
            say(f"drafted {brief.slug}")
            return
        if job["status"] == "failed":
            raise SeedError(f"drafting {brief.slug} failed: {job.get('last_error') or 'no reason'}")
        if waited >= DRAFT_TIMEOUT_S:
            raise SeedError(
                f"{brief.slug} is still {job['status']} after {DRAFT_TIMEOUT_S:.0f} s: is the "
                "worker running? Run this again later; it carries on where it stopped"
            )
        pause(POLL_S)
        waited += POLL_S


def seed(
    api: httpx.Client,
    photos: Path,
    say: Callable[[str], None] = print,
    briefs: tuple[Brief, ...] = POSTS,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    ensure_agency(api)
    ids: dict[str, str] = {}
    for slug in dict.fromkeys(brief.client for brief in briefs):
        ids[slug] = ensure_client(api, slug, photos, say)
    for brief in briefs:
        ensure_post(api, ids[brief.client], brief, say, pause)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--photos", type=Path, default=Path.home() / "dev" / "stratum-photos")
    parser.add_argument("--api", default=LIVE)
    args = parser.parse_args(argv)
    load_dotenv(REPO / ".env", override=False)
    need = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "DEMO_EMAIL", "DEMO_PASSWORD")
    missing = [name for name in need if not os.environ.get(name)]
    if missing:
        print(f"add {', '.join(missing)} to .env first", file=sys.stderr)
        return 2
    if not args.photos.is_dir():
        print(f"no photo folder at {args.photos}", file=sys.stderr)
        return 2
    with httpx.Client(timeout=30) as auth:
        token = sign_in(
            auth,
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_ANON_KEY"],
            os.environ["DEMO_EMAIL"],
            os.environ["DEMO_PASSWORD"],
        )
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=args.api, headers=headers, timeout=60) as api:
        try:
            seed(api, args.photos.expanduser())
        except SeedError as exc:
            print(f"stopped: {exc}", file=sys.stderr)
            return 1
    print("done: open the site, click Try the demo once DEMO_EMAIL/DEMO_PASSWORD are on the API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
