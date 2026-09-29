"""The design space (M11a): many valid candidate designs per post, laid out around
the real words, carrying their own layout for every size.

What it promises:
- every family and choice, for every fictional brand kit, either lays out cleanly in
  all four sizes (no preflight flag of any kind) or does not exist for that copy;
- a design id round-trips, and a preset is a fixed id;
- the words are measured, so the offer sits one fixed gap under the headline's last
  line whatever the headline says;
- in a story, text and logo stay in the safe band and the bottom fifth is never
  left empty (the M11 finding in two hand-made templates);
- a post made from the design space drafts, tweaks and renders like any other, and
  "Two photos" puts both of the client's photos and the offer on one poster.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from stratum_spec import BrandKit, ImageLayer, PosterSpec, ShapeLayer, TextLayer, retarget
from stratum_worker.app import service
from stratum_worker.design import (
    FAMILIES,
    PRESETS,
    SIZES,
    Copy,
    UnknownDesign,
    decode,
    design_id,
    everything,
    is_design,
    lay_out,
)
from stratum_worker.design.frame import Frame
from stratum_worker.design.typeset import GAP_AFTER_HEADLINE, SLACK
from stratum_worker.draft.bindings import binding_for
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP, check_layout
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
KITS = {
    path.parent.name: BrandKit.model_validate(json.loads(path.read_text()))
    for path in sorted((REPO / "clients").glob("*/brand.json"))
}
KIT = KITS["kowalski-plumbing"]
LIVE = Copy(
    headline="Meditation camp for self-discovery",
    offer="5 Nov 2026 to 1 Feb 2027. Women and under-18s get 50% off.",
    cta="Book your place today",
)


def all_designs(kit: BrandKit, copy: Copy = LIVE) -> list[tuple[str, PosterSpec]]:
    laid = [(d.id, lay_out(d.id, kit, REPO, copy=copy)) for d in everything()]
    return [(i, spec) for i, spec in laid if spec is not None]


# --- the space itself -----------------------------------------------------------------


def test_the_space_offers_hundreds_of_designs_per_post() -> None:
    per_family: dict[str, int] = {}
    for design_id_, _ in all_designs(KIT):
        family = decode(design_id_).family.name
        per_family[family] = per_family.get(family, 0) + 1
    assert set(per_family) == set(FAMILIES)
    assert sum(per_family.values()) >= 600


@pytest.mark.parametrize("client", sorted(KITS))
def test_every_design_passes_every_layout_check_in_every_size(client: str) -> None:
    kit = KITS[client]
    problems: list[str] = []
    for design_id_, spec in all_designs(kit):
        for aspect in SIZES:
            for flag in check_layout(retarget(spec, aspect), kit, root=REPO):
                problems.append(f"{design_id_} {aspect}: {flag.code}")
    assert problems == []


def test_a_design_id_round_trips_and_nonsense_is_refused() -> None:
    for design in list(everything())[::37]:
        again = decode(design_id(design.family, design.choice))
        assert again.choice == design.choice and again.family is design.family
    for bad in ("ds.nope.1.0", "ds.pair.9.0000000", "ds.pair.1.00", "ds.pair.1.9999999"):
        with pytest.raises(UnknownDesign):
            decode(bad)
    assert not is_design("offer_4x5") and is_design("two_photos")


def test_two_photos_is_a_preset_with_both_photos_and_an_offer_line() -> None:
    binding = binding_for("two_photos")
    assert decode(PRESETS["two_photos"]).family.name == "pair"
    assert binding.photos == {"photo_a": "photo", "photo_b": "photo"}
    assert "offer_detail" in binding.texts.values()


def test_every_size_keeps_the_same_layers_and_says_the_same_thing() -> None:
    for _, spec in all_designs(KIT)[::11]:
        base = {layer.id: layer for layer in spec.layers}
        for aspect in SIZES:
            one = retarget(spec, aspect)
            assert [layer.id for layer in one.layers] == list(base)
            for layer in one.layers:
                if isinstance(layer, TextLayer):
                    assert layer.content == base[layer.id].content  # type: ignore[union-attr]
                    assert layer.color == base[layer.id].color  # type: ignore[union-attr]


# --- words measured, not guessed --------------------------------------------------------


def test_the_offer_sits_one_gap_under_the_headline_whatever_it_says() -> None:
    frame = Frame.of("4:5")
    gaps = []
    for headline in ("Summer camp", "Meditation camp for self-discovery, all winter long"):
        spec = lay_out("ds.type.1.000000", KIT, REPO, copy=Copy(headline, LIVE.offer, LIVE.cta))
        assert spec is not None
        head, offer = spec.layer("headline").box, spec.layer("offer").box
        # Boxes carry a little slack around the measured lines; take it back out.
        head_end = (head.y + head.h) * frame.height - head.h * frame.height * (1 - 1 / SLACK) / 2
        offer_start = offer.y * frame.height + offer.h * frame.height * (1 - 1 / SLACK) / 2
        gaps.append(offer_start - head_end)
    assert gaps[0] == pytest.approx(GAP_AFTER_HEADLINE * frame.short, abs=1.5)
    assert gaps[1] == pytest.approx(gaps[0], abs=1.5)


def test_words_that_cannot_fit_mean_no_design_not_cut_off_words() -> None:
    endless = Copy("Supercalifragilisticexpialidocious " * 6, LIVE.offer * 3, LIVE.cta)
    assert lay_out("ds.panel.1.0000000", KIT, REPO, copy=endless) is None


def test_the_logo_box_takes_the_logo_shape() -> None:
    wide = lay_out("ds.type.1.000000", KIT, REPO, copy=LIVE, logo_aspect=4.0)
    tall = lay_out("ds.type.1.000000", KIT, REPO, copy=LIVE, logo_aspect=0.5)
    assert wide is not None and tall is not None
    frame = Frame.of("4:5")
    for spec, aspect in ((wide, 4.0), (tall, 0.5)):
        box = spec.layer("logo").box
        assert (box.w * frame.width) / (box.h * frame.height) == pytest.approx(aspect, rel=0.01)


# --- stories ----------------------------------------------------------------------------


def _covered(spec: PosterSpec, top: float) -> bool:
    """Is the band from `top` to the bottom of the page covered by something opaque?"""
    for layer in spec.layers:
        if not layer.visible or not isinstance(layer, ImageLayer | ShapeLayer):
            continue
        if isinstance(layer, ShapeLayer) and layer.opacity < 0.9:
            continue
        box = layer.box
        if box.x <= 0 and box.x + box.w >= 1 and box.y <= top and box.y + box.h >= 1:
            return True
    parts = sorted(
        (layer.box.x, layer.box.x + layer.box.w)
        for layer in spec.layers
        if layer.visible
        and isinstance(layer, ImageLayer | ShapeLayer)
        and layer.box.y <= top
        and layer.box.y + layer.box.h >= 1
    )
    reach = 0.0
    for left, right in parts:
        if left > reach + 0.02:
            return False
        reach = max(reach, right)
    return reach >= 0.999


def test_a_story_never_leaves_its_bottom_fifth_empty() -> None:
    empty = [
        design_id_
        for design_id_, spec in all_designs(KIT)
        if not _covered(retarget(spec, "9:16"), STORY_SAFE_BOTTOM)
    ]
    assert empty == []


def test_story_text_and_logo_stay_in_the_safe_band() -> None:
    for _, spec in all_designs(KIT)[::7]:
        story = retarget(spec, "9:16")
        for layer in story.layers:
            if layer.visible and layer.type in ("text", "logo"):
                assert layer.box.y >= STORY_SAFE_TOP
                assert layer.box.y + layer.box.h <= STORY_SAFE_BOTTOM


# --- in the product ---------------------------------------------------------------------


def photo(seed: int) -> bytes:
    y, x = np.mgrid[0:1350, 0:1080]
    on = ((x // 9) + (y // 9) + seed) % 2 == 1
    rgb = np.where(on[..., None], [200, 150, 90], [60, 110, 170]).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "PNG")
    return out.getvalue()


class Studio:
    def __init__(self, world: World, agency: str, client_id: str) -> None:
        self.ctx = service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=fake.gateway(),
            agency_id=agency,
        )
        self.client_id = client_id

    async def client(self) -> dict[str, Any]:
        [row] = await self.ctx.supa.select("clients", id=f"eq.{self.client_id}")
        return row

    async def post(self, template: str, brief: str) -> tuple[dict[str, Any], dict[str, Any]]:
        [post] = await self.ctx.supa.insert(
            "posts",
            {
                "agency_id": self.ctx.agency_id,
                "client_id": self.client_id,
                "slug": f"post-{template.replace('.', '-')}",
                "brief": brief,
                "template_id": template,
                "aspects": list(SIZES),
            },
        )
        version = await service.draft(self.ctx, post, await self.client())
        return post, version


@pytest.fixture
async def studio(tmp_path: Path) -> Studio:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)  # a logo and one good photo
    return Studio(world, agency, client_id)


async def test_two_photos_puts_both_photos_and_the_offer_on_one_poster(studio: Studio) -> None:
    second = await service.store_image(studio.ctx, studio.client_id, photo(1), kind="photo")
    _, version = await studio.post("two_photos", "Boiler service, 20% off this month.")
    spec = service.load_spec(version["spec"])
    photos = {layer.id: layer.asset for layer in spec.layers if isinstance(layer, ImageLayer)}
    assert set(photos) == {"photo_a", "photo_b"} and second in photos.values()
    assert len(set(photos.values())) == 2
    assert spec.layer("offer").content == fake.FAKE_COPY["offer_detail"]  # type: ignore[union-attr]
    assert set(spec.variants) == {"1:1", "9:16", "16:9"}  # it carries its own sizes
    flags = await studio.ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    # FAKE_COPY's phone number is not in this brief, so the claims check blocks it -
    # correctly. The design itself must add no blocker of its own.
    blockers = {f["code"] for f in flags if f["severity"] == "blocker"}
    assert blockers <= {"invented_number"}


async def test_two_photos_with_one_photo_says_what_it_needs(studio: Studio) -> None:
    with pytest.raises(DraftError, match=r"shows 2 of .* own photos and has 1 usable"):
        await studio.post("two_photos", "Boiler service, 20% off this month.")


async def test_a_design_space_post_uses_any_design_id(studio: Studio) -> None:
    _, version = await studio.post("ds.overlay.1.010101", "Boiler service, 20% off.")
    spec = service.load_spec(version["spec"])
    assert spec.template_id == "ds.overlay.1.010101"
    renders = await studio.ctx.supa.select("renders", post_version_id=f"eq.{version['id']}")
    assert {row["aspect"] for row in renders} == set(SIZES)


async def test_make_the_logo_bigger_reaches_the_story_of_a_design_space_post(
    studio: Studio,
) -> None:
    post, version = await studio.post("ds.type.1.000000", "Boiler service, 20% off.")
    before = retarget(service.load_spec(version["spec"]), "9:16").layer("logo").box
    after_version = await service.quick(studio.ctx, post, await studio.client(), "logo_bigger")
    after = retarget(service.load_spec(after_version["spec"]), "9:16").layer("logo").box
    assert after.w == pytest.approx(before.w * 1.18, rel=0.01)


async def test_a_poster_from_before_schema_3_still_renders_its_story(studio: Studio) -> None:
    post, version = await studio.post("offer_4x5", "Boiler service, 20% off.")
    stored = dict(version["spec"])
    stored["schema_version"] = 2
    stored.pop("variants")
    old = service.load_spec(stored)
    story = retarget(old, "9:16", service.file_variants(studio.ctx, post["template_id"]))
    carried = retarget(service.load_spec(version["spec"]), "9:16")
    assert story.layer("headline").box == carried.layer("headline").box
