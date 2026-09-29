"""What real clients send, found by the first live test: web images, AVIF files,
small photos, and no photos at all.

The promises:
- an AVIF image is accepted like a JPEG; an iPhone HEIC gets a sentence, never a 500;
- a small or soft photo of the real business is used (and flagged) before anything
  is invented; only thumbnails too small to fill a poster are skipped;
- with no usable photo, the background is painted for what THIS post is about,
  and the prompt carries no prices or dates for the model to paint;
- a before/after design is never filled with invented photos.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from stratum_worker.app import service
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import offline_router
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


def photo(width: int, height: int, fmt: str = "PNG", seed: int = 0) -> bytes:
    """A sharp synthetic photo of any size: hard-edged checks read as in focus."""
    y, x = np.mgrid[0:height, 0:width]
    on = ((x // 8) + (y // 8) + seed) % 2 == 1
    rgb = np.where(on[..., None], [200, 150, 90], [60, 110, 170]).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, fmt)
    return out.getvalue()


def background_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1080, 1350), (180, 200, 210)).save(out, "PNG")
    return out.getvalue()


class Studio:
    """An agency with one client and no photos yet."""

    def __init__(self, world: World, agency: str, client_id: str, gateway: Gateway) -> None:
        self.ctx = service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=gateway,
            agency_id=agency,
        )
        self.client_id = client_id

    async def client(self) -> dict[str, Any]:
        [row] = await self.ctx.supa.select("clients", id=f"eq.{self.client_id}")
        return row

    async def add_photo(self, data: bytes) -> str:
        return await service.store_image(self.ctx, self.client_id, data, kind="photo")

    async def post(self, template: str, brief: str = "Summer retreat, 50% off.") -> Any:
        [post] = await self.ctx.supa.insert(
            "posts",
            {
                "agency_id": self.ctx.agency_id,
                "client_id": self.client_id,
                "slug": f"post-{template.replace('_', '-')}",
                "brief": brief,
                "template_id": template,
                "aspects": ["4:5"],
            },
        )
        return post, await service.draft(self.ctx, post, await self.client())


@pytest.fixture
async def studio(tmp_path: Path) -> Studio:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    # The seed adds a good photo; this studio starts with none.
    supa = world.factory(agency)
    for row in await supa.select("assets", kind="eq.photo"):
        await supa.update("assets", {"kind": "retired"}, id=f"eq.{row['id']}")
    router = offline_router(
        [fake.FAKE_COPY, {"image_png": background_png(), "width": 1080, "height": 1350}]
    )
    return Studio(
        world, agency, client_id, Gateway(router=router, budget=Budget(daily_cap_usd=2.0))
    )


# --- file formats ---------------------------------------------------------------------


async def test_an_avif_image_is_accepted_like_a_jpeg(studio: Studio) -> None:
    sha = await studio.add_photo(photo(900, 1100, "AVIF"))
    [row] = await studio.ctx.supa.select("assets", sha256=f"eq.{sha}")
    assert row["kind"] == "photo" and row["width"] == 900


# --- choosing photos --------------------------------------------------------------------


async def test_small_real_photos_are_used_and_flagged_before_anything_is_invented(
    studio: Studio,
) -> None:
    before = await studio.add_photo(photo(500, 400, seed=0))
    after = await studio.add_photo(photo(500, 400, seed=1))
    _, version = await studio.post("before_after_4x5")
    bound = {getattr(layer, "asset", None) for layer in service.load_spec(version["spec"]).layers}
    assert {before, after} <= bound
    flags = await studio.ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    weak = [f for f in flags if f["code"] == "photo_weak"]
    assert weak and "small" in weak[0]["message"]


async def test_good_photos_are_chosen_before_weak_ones(studio: Studio) -> None:
    await studio.add_photo(photo(500, 400, seed=0))  # small
    good = await studio.add_photo(photo(1080, 1350, seed=1))
    picked, flags = await service.pick_photos(studio.ctx, studio.client_id, {"bg": "background"})
    assert picked == {"bg": good}
    assert not [f for f in flags if f.code == "photo_weak"]


async def test_a_thumbnail_too_small_for_a_poster_is_never_used(studio: Studio) -> None:
    await studio.add_photo(photo(200, 150))
    picked, flags = await service.pick_photos(studio.ctx, studio.client_id, {"bg": "background"})
    assert picked == {}
    assert [f.code for f in flags] == ["photo_unusable"]


async def test_before_and_after_is_never_filled_with_invented_photos(studio: Studio) -> None:
    with pytest.raises(DraftError, match=r"real photos .* has 0 usable"):
        await studio.post("before_after_4x5")


# --- no photos at all -------------------------------------------------------------------


async def test_with_no_photos_the_background_is_painted_for_this_post(studio: Studio) -> None:
    _, version = await studio.post("offer_4x5", brief="Summer meditation retreat, 50% off.")
    spec = service.load_spec(version["spec"])
    bg = next(layer for layer in spec.layers if layer.id == "bg")
    [row] = await studio.ctx.supa.select("assets", sha256=f"eq.{getattr(bg, 'asset', '')}")
    assert row["kind"] == "generated"
    flags = await studio.ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    assert "generated_background" in {f["code"] for f in flags}


def test_the_background_prompt_describes_the_post_without_figures() -> None:
    client = {"name": "Lotus Meditation", "trade": "meditation_centre"}
    post = {"brief": "Summer camp 5 Nov to 1 Feb 2027. Under-18s get 50% off, £20 deposit."}
    prompt = service.background_prompt(client, post)
    assert "Lotus Meditation, a meditation centre" in prompt
    assert "Summer camp" in prompt
    assert not re.search(r"[0-9£$€%]", prompt)
    assert prompt.endswith("Absolutely no words, letters, numbers, logos, signs or watermarks.")
