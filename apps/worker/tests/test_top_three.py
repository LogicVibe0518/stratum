"""M12b: the top three.

What a reviewer should be able to repeat back:
- the candidates are the designs this post can actually fill: no two-photo design
  without two real photos, nothing photo-shaped with no photo and no background;
- the three shown are different families when there are three to choose from, and
  a design whose words would sit on a face loses to one that leaves it clear;
- every option carries the probability the policy had of showing it, and that
  number is right - 1 for the two the ranking always shows, and for the third the
  share of greedy plus exploration;
- the same post always gets the same options, in the same order;
- in the product: a draft keeps three options and makes the best one version 1,
  picking another makes it the poster and logs exactly what was shown, and picking
  the same thing twice is one decision.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from stratum_spec import BrandKit, ImageLayer
from stratum_worker.design import choose as chooser
from stratum_worker.design.choose import (
    AUTO,
    Candidate,
    Material,
    candidates,
    choose,
    eligible,
    family_of,
    fill,
    sample,
    seed_for,
)
from stratum_worker.photo.faces import Face
from stratum_worker.photo.frame import framing_cost
from stratum_worker.photo.read import PhotoRead, read_image

REPO = Path(__file__).resolve().parents[3]
WORDS = {
    "headline": "Winter boiler checks",
    "offer_detail": "20% off until 31 October. Gas Safe engineers.",
    "cta": "Book today",
}
A, B, LOGO = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture(scope="module")
def kit() -> BrandKit:
    return BrandKit.model_validate(
        json.loads((REPO / "clients" / "kowalski-plumbing" / "brand.json").read_text())
    )


def flat_read(size: tuple[int, int] = (1500, 1000), faces: tuple[Face, ...] = ()) -> PhotoRead:
    rows, cols = 32, 48
    return PhotoRead(
        size=size,
        faces=faces,
        salience=np.full((rows, cols), 1.0 / (rows * cols)),
        busy=np.zeros((rows, cols)),
        strength=0.0,
    )


def material(kit: BrandKit, photos: tuple[str, ...], **reads: PhotoRead) -> Material:
    return Material(
        words=WORDS,
        kit=kit,
        photos=photos,
        background=None,
        logo=LOGO,
        logo_aspect=2.0,
        ink=None,
        reads={A: reads.get("a", flat_read()), B: reads.get("b", flat_read())},
    )


@pytest.fixture
def few(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer candidates, so each ranking takes a fraction of a second."""
    monkeypatch.setattr(chooser, "MAX_CANDIDATES", 48)
    monkeypatch.setattr(chooser, "SHORTLIST", 10)
    monkeypatch.setattr(chooser, "FAMILY_FLOOR", 2)


# --- candidates ----------------------------------------------------------------------


def test_only_designs_the_material_can_fill_are_candidates(kit: BrandKit) -> None:
    none = {family_of(d) for d in eligible(material(kit, ()))}
    one = {family_of(d) for d in eligible(material(kit, (A,)))}
    two = {family_of(d) for d in eligible(material(kit, (A, B)))}
    assert none == {"type"}  # no photo and no background: words only
    assert "pair" not in one and {"panel", "overlay", "type"} <= one
    assert "pair" in two
    with_background = Material(**{**material(kit, ()).__dict__, "background": A})
    assert {"panel", "overlay"} <= {family_of(d) for d in eligible(with_background)}


def test_the_sample_is_even_across_families_and_repeatable() -> None:
    ids = [f"ds.panel.1.{n}" for n in range(50)] + [f"ds.type.1.{n}" for n in range(5)]
    first = sample(ids, random.Random(7), 20)
    again = sample(ids, random.Random(7), 20)
    assert first == again
    assert sum(family_of(d) == "type" for d in first) == 5  # all of the small family
    assert len(first) == 20


def test_a_two_photo_design_gets_both_photos_and_the_logo(kit: BrandKit) -> None:
    pair = next(d for d in eligible(material(kit, (A, B))) if family_of(d) == "pair")
    spec = fill(pair, material(kit, (A, B)), ["4:5"], REPO)
    assert spec is not None
    shown = [layer.asset for layer in spec.layers if isinstance(layer, ImageLayer)]
    assert sorted(shown) == [A, B]
    assert any(getattr(layer, "asset", None) == LOGO for layer in spec.layers)


@pytest.mark.usefixtures("few")
def test_the_ranking_is_the_same_for_the_same_post(kit: BrandKit) -> None:
    stuff = material(kit, (A, B))
    one = candidates(stuff, ["4:5"], REPO, seed=seed_for("post-1"))
    two = candidates(stuff, ["4:5"], REPO, seed=seed_for("post-1"))
    assert [c.design_id for c in one] == [c.design_id for c in two]
    assert [o.candidate.design_id for o in choose(one, seed=1)] == [
        o.candidate.design_id for o in choose(two, seed=1)
    ]


@pytest.mark.usefixtures("few")
def test_a_design_that_puts_words_on_a_face_ranks_below_one_that_does_not(
    kit: BrandKit,
) -> None:
    # A square photo with the face in the lower half. In a 4:5 box it cannot move up
    # or down, so a design with its words at the bottom must cover the face.
    face = Face(x=0.40, y=0.62, w=0.2, h=0.2, score=0.95)
    square = flat_read((1000, 1000), faces=(face,))
    stuff = material(kit, (A,), a=square)
    covering = [
        design_id
        for design_id in eligible(stuff)
        if family_of(design_id) == "overlay"
        and (spec := fill(design_id, stuff, ["4:5"], REPO)) is not None
        and framing_cost(spec, stuff.reads, kit, REPO) > 5
    ]
    assert covering  # some designs really would put words on this face
    ranked = candidates(stuff, ["4:5"], REPO, seed=seed_for("faces"))
    assert not {c.design_id for c in ranked} & set(covering)
    top = choose(ranked, seed=seed_for("faces"))
    assert all(option.candidate.parts["framing_cost"] < 5 for option in top)


# --- choosing three ------------------------------------------------------------------


def fake_ranked(families: list[str]) -> list[Candidate]:
    return [
        Candidate(
            design_id=f"ds.{family}.1.{n}",
            family=family,
            spec=None,  # type: ignore[arg-type]
            score=10.0 - n,
            parts={},
        )
        for n, family in enumerate(families)
    ]


def test_three_different_families_when_there_are_three() -> None:
    ranked = fake_ranked(["panel", "panel", "overlay", "panel", "pair", "type"])
    picks = choose(ranked, seed=3, explore=0.0)
    assert [o.candidate.design_id for o in picks] == [
        "ds.panel.1.0",
        "ds.overlay.1.2",
        "ds.pair.1.4",
    ]
    assert sorted(o.position for o in picks) == [0, 1, 2]
    assert [o.slot for o in picks] == [1, 2, 3]


def test_the_propensities_are_what_the_policy_really_had() -> None:
    ranked = fake_ranked(["panel", "overlay", "pair", "type", "pair", "type"])
    greedy = choose(ranked, seed=3, explore=0.0)
    assert [o.propensity for o in greedy] == [1.0, 1.0, 1.0]
    # Exploration always on: the third is drawn from the designs of other families
    # (pair, type, pair, type) - four of them - so each had a 1 in 4 chance.
    drawn = choose(ranked, seed=3, explore=1.0)
    assert drawn[2].propensity == pytest.approx(0.25)
    # Exploration at 15%, not taken: greedy's share plus its chance of being drawn.
    for seed in range(50):
        third = choose(ranked, seed=seed, explore=0.15)[2]
        if not third.explored and third.candidate.design_id == "ds.pair.1.2":
            assert third.propensity == pytest.approx(0.85 + 0.15 / 4)
            break
    else:
        pytest.fail("no unexplored draw in 50 seeds")


def test_with_fewer_than_three_families_the_next_best_fill_in() -> None:
    picks = choose(fake_ranked(["type", "type", "type", "type"]), seed=1, explore=0.0)
    assert [o.candidate.design_id for o in picks] == ["ds.type.1.0", "ds.type.1.1", "ds.type.1.2"]


def test_exploration_happens_about_as_often_as_it_says() -> None:
    ranked = fake_ranked(["panel", "overlay", "pair", "type", "pair", "type", "pair"])
    explored = sum(choose(ranked, seed=seed)[2].explored for seed in range(2000))
    # 15% exploring, and 1 time in 5 the draw lands on the greedy choice anyway.
    assert 0.08 < explored / 2000 < 0.16


# --- the product ---------------------------------------------------------------------


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def scene(at: float) -> Image.Image:
    image = Image.new("RGB", (1500, 1000), (200, 205, 210))
    ImageDraw.Draw(image).ellipse(
        [int(at * 1500) - 150, 350, int(at * 1500) + 150, 650], fill=(190, 40, 40)
    )
    return image


@pytest.fixture
async def studio(tmp_path: Path) -> Any:
    from stratum_worker.app import service
    from stratum_worker.gateway.budget import Budget
    from stratum_worker.gateway.gateway import Gateway
    from stratum_worker.gateway.providers.registry import offline_router
    from stratum_worker.queue import fake
    from stratum_worker.queue.fake import World

    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    supa = world.factory(agency)
    ctx = service.Context(
        supa=supa,
        settings=world.settings(REPO),
        gateway=Gateway(router=offline_router([fake.FAKE_COPY] * 8), budget=Budget(2.0)),
        agency_id=agency,
        user_id="00000000-0000-0000-0000-000000000001",
    )
    await service.store_image(ctx, client_id, _png(scene(0.7)), kind="photo")
    [client] = await supa.select("clients", id=f"eq.{client_id}")
    [post] = await supa.insert(
        "posts",
        {
            "agency_id": agency,
            "client_id": client_id,
            "slug": "best-three",
            "brief": "Winter boiler checks, 20% off.",
            "template_id": AUTO,
            "aspects": ["4:5", "9:16"],
        },
    )
    return ctx, post, client


@pytest.mark.usefixtures("few")
async def test_a_best_three_draft_keeps_three_options_and_shows_the_best(studio: Any) -> None:
    from stratum_worker.app import options, service

    ctx, post, client = studio
    version = await service.draft(ctx, post, client)
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}", order="slot.asc")
    assert len(rows) == 3 and len({row["batch"] for row in rows}) == 1
    assert sorted(row["position"] for row in rows) == [0, 1, 2]
    assert len({family_of(row["design_id"]) for row in rows}) == 3
    assert rows[0]["propensity"] == 1.0 and 0 < rows[2]["propensity"] <= 1
    # Version 1 is the ranking's best, and the post now IS that design.
    assert service.load_spec(version["spec"]).model_dump() == rows[0]["spec"]
    [saved] = await ctx.supa.select("posts", id=f"eq.{post['id']}")
    assert saved["template_id"] == rows[0]["design_id"]
    assert [one["id"] for one in version["options"]] == [
        row["id"] for row in sorted(rows, key=lambda row: row["position"])
    ]
    listed = await options.latest_options(ctx, post["id"])
    assert [one["position"] for one in listed] == [0, 1, 2]
    assert {"id", "name", "title", "design_id", "preview"} <= set(listed[0])


@pytest.mark.usefixtures("few")
async def test_picking_an_option_makes_it_the_poster_and_logs_what_was_shown(
    studio: Any,
) -> None:
    from stratum_worker.app import options, service

    ctx, post, client = studio
    await service.draft(ctx, post, client)
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}", order="slot.asc")
    third = rows[2]
    version = await options.pick_option(ctx, post, client, third["id"])
    assert version["version"] == 2
    assert service.load_spec(version["spec"]).model_dump() == third["spec"]
    [saved] = await ctx.supa.select("posts", id=f"eq.{post['id']}")
    assert saved["template_id"] == third["design_id"]
    [pick] = await ctx.supa.select("decision_log", post_id=f"eq.{post['id']}")
    assert pick["kind"] == "designer_pick" and pick["version_id"] == version["id"]
    detail = pick["detail"]
    assert detail["picked"] == third["id"] and detail["position"] == third["position"]
    assert {one["id"] for one in detail["shown"]} == {row["id"] for row in rows}
    assert all("propensity" in one for one in detail["shown"])

    # The same pick again: the same version, and still one decision.
    again = await options.pick_option(ctx, post, client, third["id"])
    assert again["id"] == version["id"]
    assert len(await ctx.supa.select("decision_log", post_id=f"eq.{post['id']}")) == 1


@pytest.mark.usefixtures("few")
async def test_picking_the_option_already_shown_is_logged_without_a_new_version(
    studio: Any,
) -> None:
    from stratum_worker.app import options, service

    ctx, post, client = studio
    first = await service.draft(ctx, post, client)
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}", order="slot.asc")
    version = await options.pick_option(ctx, post, client, rows[0]["id"])
    assert version["id"] == first["id"]
    [pick] = await ctx.supa.select("decision_log", post_id=f"eq.{post['id']}")
    assert pick["detail"]["slot"] == 1


def _layer_for(design_id: str, field: str) -> str:
    from stratum_worker.draft.bindings import binding_for

    return next(layer for layer, name in binding_for(design_id).texts.items() if name == field)


@pytest.mark.usefixtures("few")
async def test_picking_another_option_keeps_the_words_the_designer_changed(
    studio: Any,
) -> None:
    """Live, 26 Oct: the headline and offer were fixed on option A, option C was picked,
    and C came back with the first draft's words. The edits are the person's work."""
    from stratum_worker.app import options, service

    ctx, post, client = studio
    await service.draft(ctx, post, client)
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}", order="slot.asc")
    [post] = await ctx.supa.select("posts", id=f"eq.{post['id']}")
    mine = "Boiler checks: 20% off"
    await service.edit(
        ctx,
        post,
        client,
        version=1,
        raw_ops=[
            {"op": "set_text", "layer": _layer_for(rows[0]["design_id"], "headline"), "text": mine}
        ],
        restyle=None,
    )
    third = rows[2]
    version = await options.pick_option(ctx, post, client, third["id"])
    spec = service.load_spec(version["spec"])
    assert spec.layer(_layer_for(third["design_id"], "headline")).content == mine  # type: ignore[union-attr]
    assert version["note"].endswith("kept your words")

    # Picking the design the poster now is changes nothing: no new version.
    [post] = await ctx.supa.select("posts", id=f"eq.{post['id']}")
    again = await options.pick_option(ctx, post, client, third["id"])
    assert again["id"] == version["id"]


async def test_an_option_of_another_post_cannot_be_picked(studio: Any) -> None:
    from stratum_worker.app import options
    from stratum_worker.draft.pipeline import DraftError

    ctx, post, client = studio
    with pytest.raises(DraftError, match="does not belong"):
        await options.pick_option(ctx, post, client, "00000000-0000-0000-0000-00000000dead")


def test_the_read_of_a_real_photo_feeds_the_ranking(kit: BrandKit) -> None:
    # Smoke: the ranking accepts reads straight from the photo reader.
    read = read_image(scene(0.3))
    stuff = material(kit, (A,), a=read)
    spec = fill(next(iter(eligible(stuff))), stuff, ["4:5"], REPO)
    assert spec is not None
