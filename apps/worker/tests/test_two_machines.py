"""The API and the worker are two machines with two disks.

Found on the first live test: every photo and logo is uploaded through the API, so
the cloud worker has never ingested any of them. Its first draft failed with
AssetNotFound, because the "fetch it from storage" fallback in `local_path` sat
behind a check that the file was already ingested on this disk. On one laptop, where
the API and the worker share a disk, that could never happen, so no test saw it.

The promises:
- a machine that never ingested an asset fetches it from storage and uses it;
- it fetches once, then works from its own disk;
- a stored copy that is not the picture its id names is refused, never drawn;
- a fetched copy never gets in the way of ingesting the same photo later;
- every lease names the build of the worker that took it.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from stratum_worker.app import service
from stratum_worker.app.supa import Supa
from stratum_worker.assets import CorruptImage
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import offline_router
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World
from stratum_worker.queue.worker import build_id, new_worker_id

REPO = Path(__file__).resolve().parents[3]


def photo(width: int, height: int, seed: int = 0) -> bytes:
    y, x = np.mgrid[0:height, 0:width]
    on = ((x // 8) + (y // 8) + seed) % 2 == 1
    rgb = np.where(on[..., None], [200, 150, 90], [60, 110, 170]).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


class Machines:
    """One database and one bucket, as on Railway; an API disk and a worker disk."""

    def __init__(self, world: World, agency: str, client_id: str, tmp: Path) -> None:
        router = offline_router([fake.FAKE_COPY] * 100)
        self.client_id = client_id
        self.api = service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=Gateway(router=router, budget=Budget(daily_cap_usd=2.0)),
            agency_id=agency,
        )
        worker_settings = world.settings(REPO).model_copy(
            update={"asset_cache": tmp / "worker-disk"}
        )
        self.worker = service.Context(
            supa=world.factory(agency),
            settings=worker_settings,
            gateway=Gateway(router=router, budget=Budget(daily_cap_usd=2.0)),
            agency_id=agency,
        )

    async def client(self) -> dict[str, Any]:
        [row] = await self.worker.supa.select("clients", id=f"eq.{self.client_id}")
        return row

    async def before_after(self) -> dict[str, Any]:
        [post] = await self.api.supa.insert(
            "posts",
            {
                "agency_id": self.api.agency_id,
                "client_id": self.client_id,
                "slug": "summer-camp",
                "brief": "Summer camp from 5 Nov to 1 Feb 2027. Under-18s get 50% off.",
                "template_id": "before_after_4x5",
                "aspects": ["4:5", "1:1", "9:16", "16:9"],
            },
        )
        return post


@pytest.fixture
async def machines(tmp_path: Path) -> Machines:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)  # the logo arrives through the API
    # The seed also adds one good photo; like the live client, this one starts with none.
    supa = world.factory(agency)
    for row in await supa.select("assets", kind="eq.photo"):
        await supa.update("assets", {"kind": "retired"}, id=f"eq.{row['id']}")
    return Machines(world, agency, client_id, tmp_path)


async def test_the_worker_drafts_with_photos_only_the_api_ever_saw(machines: Machines) -> None:
    """The live failure, exactly: two small web photos uploaded, then a before/after."""
    before = await service.store_image(
        machines.api, machines.client_id, photo(625, 350), kind="photo"
    )
    after = await service.store_image(
        machines.api, machines.client_id, photo(640, 362, 1), kind="photo"
    )
    assert not (machines.worker.settings.asset_cache).exists()

    version = await service.draft(
        machines.worker, await machines.before_after(), await machines.client()
    )

    bound = {getattr(layer, "asset", None) for layer in service.load_spec(version["spec"]).layers}
    assert {before, after} <= bound
    fetched = machines.worker.store.fetched(before)
    assert fetched.is_file()


async def test_an_asset_is_fetched_once_then_read_from_this_disk(
    machines: Machines, monkeypatch: pytest.MonkeyPatch
) -> None:
    [logo] = await machines.api.supa.select("assets", kind="eq.logo")
    calls: list[str] = []
    real = type(machines.worker.supa).download

    async def counting(self: Supa, path: str) -> bytes:
        calls.append(path)
        return await real(self, path)  # type: ignore[arg-type]

    monkeypatch.setattr(type(machines.worker.supa), "download", counting)
    first = await service.local_path(machines.worker, logo["sha256"])
    second = await service.local_path(machines.worker, logo["sha256"])
    assert first == second
    assert len(calls) == 1


async def test_the_api_reads_the_logo_colours_after_a_redeploy_wiped_its_disk(
    machines: Machines,
) -> None:
    fresh_api = machines.worker  # a new container: same bucket, empty disk
    suggestion = await service.brand_suggestion(fresh_api, await machines.client())
    assert suggestion.logo


async def test_a_stored_copy_that_is_not_the_asset_named_is_refused(machines: Machines) -> None:
    [logo] = await machines.api.supa.select("assets", kind="eq.logo")
    sha = logo["sha256"]
    out = io.BytesIO()
    Image.new("RGB", (400, 400), (255, 0, 0)).save(out, "PNG")
    await machines.api.supa.upload(
        f"{machines.api.agency_id}/assets/{sha}.png", out.getvalue(), "image/png"
    )

    with pytest.raises(CorruptImage, match="different picture"):
        await service.local_path(machines.worker, sha)
    assert not machines.worker.store.fetched(sha).exists()


async def test_a_damaged_stored_copy_is_refused(machines: Machines) -> None:
    [logo] = await machines.api.supa.select("assets", kind="eq.logo")
    sha = logo["sha256"]
    await machines.api.supa.upload(
        f"{machines.api.agency_id}/assets/{sha}.png",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        "image/png",
    )
    with pytest.raises(CorruptImage, match="damaged"):
        await service.local_path(machines.worker, sha)


async def test_a_fetched_copy_never_blocks_ingesting_the_same_photo(machines: Machines) -> None:
    data = photo(900, 1100)
    sha = await service.store_image(machines.api, machines.client_id, data, kind="photo")
    await service.local_path(machines.worker, sha)  # fetched onto the worker's disk
    # Later the same photo is ingested on that machine (a re-upload after a move).
    again = await service.store_image(machines.worker, machines.client_id, data, kind="photo")
    assert again == sha
    assert await service.local_path(machines.worker, sha) == str(
        machines.worker.store.path_for(sha, "full").resolve()
    )


# --- which code took the job ------------------------------------------------------------


def test_a_lease_names_the_railway_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "decddc5896982b233ae962d1ce0175428e6ba969")
    assert build_id() == "decddc5"
    assert ":decddc5:" in new_worker_id(2)


def test_a_lease_from_a_laptop_says_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    assert ":local:" in new_worker_id()
