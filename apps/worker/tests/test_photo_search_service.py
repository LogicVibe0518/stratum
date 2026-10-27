"""M17a through the product: a photo found by what is in it, on real drafts.

The photo model here is a stand-in that sees only colour (a photo is its average
colour, a description names one), so every answer is known in advance. The real
model's quality is measured in the lab; these test what Stratum does with answers.

What a reviewer should be able to repeat back:
- "use the red one" swaps in the photo that matches, says so, and keeps each photo's
  vector with its row, made once per model;
- when nothing clearly matches, nothing changes: the designer is shown the closest
  two, and picking one in the panel is a plain swap with no model;
- the panel only swaps in this client's own photos;
- "include the blue one too" restyles to two photos with the one asked for;
- a draft takes the photo its note describes, and never starts with a graphic;
- without the model, use_photo offers the client's photos to pick and nothing else changes;
- the chat model is told what kinds of photos the client has.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image
from stratum_spec import ImageLayer
from stratum_worker.app import photo_search, service
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.photo import understand
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World
from test_smarter_tweaks import BRIEF, PANEL, Studio, asked

COLOURS = {
    "red": (200, 40, 40),
    "green": (40, 170, 60),
    "blue": (40, 60, 200),
    "white": (245, 245, 245),
}


class ColourModel:
    """A photo model that sees only colour."""

    id = "stub-colours-1"
    line = 0.5

    def __init__(self) -> None:
        self.photos_seen = 0

    @staticmethod
    def _unit(v: npt.NDArray[np.float64]) -> npt.NDArray[np.float32]:
        norm = float(np.linalg.norm(v))
        return (v / norm if norm else v).astype(np.float32)

    def embed_photos(self, images: Sequence[Image.Image]) -> npt.NDArray[np.float32]:
        self.photos_seen += len(images)
        rows = [
            np.asarray(one.convert("RGB"), dtype=np.float64).mean(axis=(0, 1)) / 255 - 0.5
            for one in images
        ]
        return np.stack([self._unit(row) for row in rows])

    def embed_query(self, description: str) -> npt.NDArray[np.float32]:
        for name, rgb in COLOURS.items():
            if name in description.lower():
                return self._unit(np.asarray(rgb, dtype=np.float64) / 255 - 0.5)
        return np.zeros(3, dtype=np.float32)  # no colour named: matches nothing

    def probability(self, cosines: npt.NDArray[Any]) -> npt.NDArray[np.float64]:
        return 1.0 / (1.0 + np.exp(-(np.asarray(cosines, dtype=np.float64) * 10 - 6)))

    def photo_types(self, vectors: npt.NDArray[np.float32]) -> list[tuple[str | None, float]]:
        # Near white all over reads as a graphic (a flyer); the rest as places.
        return [("graphic", 0.9) if float(v.min()) > 0.5 else ("place", 0.8) for v in vectors]


def tinted(name: str, seed: int = 1) -> bytes:
    """A photo-like picture (texture, so it passes the upload check) in one colour."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:1350, 0:1080]
    texture = 40 * np.sin(x / 7.0) * np.cos(y / 9.0) + rng.integers(-20, 20, size=(1350, 1080))
    rgb = np.stack([c + texture for c in COLOURS[name]], axis=-1)
    out = io.BytesIO()
    Image.fromarray(rgb.clip(0, 255).astype("uint8"), "RGB").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def colours(monkeypatch: pytest.MonkeyPatch) -> ColourModel:
    model = ColourModel()
    monkeypatch.setattr(understand, "installed", lambda: model)
    return model


async def studio(
    tmp_path: Path, *answers: dict[str, object], photos: Sequence[str]
) -> tuple[Studio, dict[str, str]]:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, Path(__file__).resolve().parents[3])
    one = Studio(world, agency, client_id, FakeProvider(results=[fake.FAKE_COPY, *answers]))
    shas = {}
    for seed, name in enumerate(photos, start=1):
        shas[name] = await service.store_image(one.ctx, client_id, tinted(name, seed), kind="photo")
    return one, shas


def shown(spec: Any) -> list[str]:
    return [layer.asset for layer in spec.layers if isinstance(layer, ImageLayer) and layer.asset]


async def test_a_described_photo_is_found_and_swapped_in(
    tmp_path: Path, colours: ColourModel
) -> None:
    red_or_blue = {"ops": [{"op": "use_photo", "description": "PLACEHOLDER"}]}
    one, shas = await studio(tmp_path, red_or_blue, photos=["red", "blue"])
    post = await one.post()
    on_poster = shown(await one.latest_spec(post))
    other = "blue" if shas["red"] in on_poster else "red"
    red_or_blue["ops"][0]["description"] = f"the {other} one"  # type: ignore[index]

    out = await one.tweak(post, f"use the {other} photo")
    assert out["outcome"] == "applied", out
    assert shas[other] in shown(await one.latest_spec(post))
    assert f'best matches "the {other} one"' in out["note"]
    rows = await one.ctx.supa.select("assets", client_id=f"eq.{one.client_id}", kind="eq.photo")
    for row in rows:
        assert row["quality"]["seen_by"] == colours.id and row["quality"]["photo_type"] == "place"
        assert "sharpness" in row["quality"]  # the upload's own numbers are kept
    seen = colours.photos_seen
    await one.tweak(post, "anything")  # the next search reuses the kept vectors
    assert colours.photos_seen == seen


async def test_no_clear_match_changes_nothing_and_shows_the_closest(
    tmp_path: Path, colours: ColourModel
) -> None:
    green = {"ops": [{"op": "use_photo", "description": "a green lawn"}]}
    one, shas = await studio(tmp_path, green, photos=["red", "blue"])
    post = await one.post()
    out = await one.tweak(post, "use the photo of the lawn")
    assert out["outcome"] == "needs_human"
    assert 'clearly shows "a green lawn"' in out["question"]
    assert {one["sha256"] for one in out["photos"]} == {shas["red"], shas["blue"]}
    assert out["layer"] == "bg"
    assert len(await one.versions(post)) == 1

    picked = await service.edit(
        one.ctx,
        await one.fresh(post),
        await one.client(),
        version=1,
        raw_ops=[{"op": "swap_asset", "layer": "bg", "asset": out["photos"][1]["sha256"]}],
        restyle=None,
    )
    assert picked["outcome"] == "applied"
    assert out["photos"][1]["sha256"] in shown(await one.latest_spec(post))
    edits = await one.ctx.supa.select("edits", post_id=f"eq.{post['id']}", order="created_at")
    assert "another photo" in edits[-1]["message"]


async def test_the_panel_swaps_in_this_clients_photos_only(tmp_path: Path) -> None:
    one, _ = await studio(tmp_path, photos=["red", "blue"])
    post = await one.post()
    with pytest.raises(DraftError, match="not one of"):
        await service.edit(
            one.ctx,
            await one.fresh(post),
            await one.client(),
            version=1,
            raw_ops=[{"op": "swap_asset", "layer": "bg", "asset": "f" * 64}],
            restyle=None,
        )
    assert len(await one.versions(post)) == 1


async def test_include_it_too_restyles_with_the_photo_asked_for(
    tmp_path: Path, colours: ColourModel
) -> None:
    use = {"op": "use_photo", "description": "PLACEHOLDER", "add": True}
    answer = {"ops": [{"op": "restyle", "changes": {"family": "pair"}}, use]}
    one, shas = await studio(tmp_path, answer, photos=["red", "green", "blue"])
    post = await one.post()
    before = shown(await one.latest_spec(post))
    # The photo the draft's own order would add second is not the one asked for.
    wanted = [name for name, sha in shas.items() if sha not in before][-1]
    use["description"] = f"the {wanted} one"
    out = await one.tweak(post, f"include the {wanted} one too")
    assert out["outcome"] == "applied", out
    after = shown(await one.latest_spec(post))
    assert shas[wanted] in after and before[0] in after
    assert f'Added the photo that best matches "the {wanted} one"' in out["note"]
    assert "Added another of" not in out["note"]


async def test_add_without_room_says_so_and_changes_nothing(
    tmp_path: Path, colours: ColourModel
) -> None:
    use = {"op": "use_photo", "description": "PLACEHOLDER", "add": True}
    one, shas = await studio(tmp_path, {"ops": [use]}, photos=["red", "blue"])
    post = await one.post()
    before = shown(await one.latest_spec(post))
    use["description"] = "the red one" if shas["red"] not in before else "the blue one"
    out = await one.tweak(post, "add that one too")
    assert out["outcome"] == "needs_human"
    assert out["why"].startswith("This design has room for 1 photo.")
    assert len(await one.versions(post)) == 1


async def test_the_draft_takes_the_photo_its_note_describes(
    tmp_path: Path, colours: ColourModel
) -> None:
    for wanted in ("red", "blue"):
        one, shas = await studio(tmp_path / wanted, photos=["red", "blue"])
        [post] = await one.ctx.supa.insert(
            "posts",
            {
                "agency_id": one.ctx.agency_id,
                "client_id": one.client_id,
                "slug": f"note-{wanted}",
                "brief": BRIEF,
                "photo_note": f"the {wanted} one",
                "template_id": PANEL,
                "aspects": ["4:5"],
            },
        )
        await service.draft(one.ctx, post, await one.client())
        assert shown(await one.latest_spec(post)) == [shas[wanted]]


async def test_a_graphic_goes_last(monkeypatch: pytest.MonkeyPatch, colours: ColourModel) -> None:
    rows = [{"sha256": "a" * 64}, {"sha256": "b" * 64}, {"sha256": "c" * 64}]
    vectors = np.eye(3, dtype=np.float32)

    async def library(ctx: Any, client_id: str, model: Any) -> photo_search.Library:
        return photo_search.Library(
            ("a" * 64, "b" * 64, "c" * 64), vectors, ("graphic", "place", None)
        )

    monkeypatch.setattr(photo_search, "library", library)
    ordered = await service._understood(None, "client", rows[:2], rows[2:], wish="")  # type: ignore[arg-type]
    assert [row["sha256"][0] for row in ordered] == ["b", "c", "a"]


async def test_without_the_model_use_photo_offers_every_photo_to_pick(tmp_path: Path) -> None:
    """Live, 26 Oct: "replace the right side image with image of mop" ended at "not
    switched on yet". Without the model the answer is the client's photos, to click."""
    red = {"ops": [{"op": "use_photo", "description": "the red one"}]}
    one, _ = await studio(tmp_path, red, photos=["red", "blue"])
    post = await one.post()
    out = await one.tweak(post, "use the red photo")
    assert out["outcome"] == "needs_human"
    assert out["why"].startswith("I cannot search the photos by what is in them yet.")
    assert "shows the red one" in out["why"]
    assert len(out["photos"]) >= 2  # every usable photo of the client's, best first
    assert all(one["probability"] is None for one in out["photos"])
    assert out["layer"] is not None and out["choices"] == []
    assert asked(one.provider).library == ""


async def test_the_chat_model_is_told_what_photos_there_are(
    tmp_path: Path, colours: ColourModel
) -> None:
    nothing = {"ops": [], "needs_human": "Which part?"}
    one, _ = await studio(tmp_path, nothing, photos=["red", "blue", "white"])
    post = await one.post()
    await one.tweak(post, "hmm")
    # The fictional client's own photo (fake.seed) and the three here.
    assert asked(one.provider).library == "4 photos: 1 graphic, 3 place"
