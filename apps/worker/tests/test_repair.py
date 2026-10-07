"""M15c: the quality bar in the product, and what Stratum tries when nothing clears it.

What a reviewer should be able to repeat back:
- with no bar shipped nothing changes: same ranking, no extra work, no flag;
- designs that clear the bar go before those that do not, whatever the critic said;
- when none clears it, the repair steps run in a fixed order and stop at the first
  that works; if none works the closest attempt is kept and the post is flagged
  `below_bar`, saying what was tried - nothing is sent or held back by the bar;
- repairs stop starting when time runs out;
- in a real draft, each option records which critic scored it and what was tried.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw
from stratum_spec import BrandKit
from stratum_worker.critic.bar import Bar
from stratum_worker.critic.features import FEATURES, INDEX
from stratum_worker.design import choose as chooser
from stratum_worker.design import repair
from stratum_worker.design.choose import AUTO, Material, candidates
from stratum_worker.photo.read import PhotoRead

REPO = Path(__file__).resolve().parents[3]
A, B, LOGO = "a" * 64, "b" * 64, "c" * 64
N = len(FEATURES)
WORDS = {
    "headline": "Winter boiler checks for every home in town",
    "headline_short": "Boiler checks",
    "offer_detail": "20% off until 31 October. Gas Safe engineers.",
    "cta": "Book today",
}
STEPS = [
    "as drafted",
    "the other photo first",
    "the shortest headline",
    "words-only designs on equal terms",
    "a wider search",
]


@pytest.fixture(scope="module")
def kit() -> BrandKit:
    return BrandKit.model_validate(
        json.loads((REPO / "clients" / "kowalski-plumbing" / "brand.json").read_text())
    )


@pytest.fixture(autouse=True)
def few(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chooser, "MAX_CANDIDATES", 40)
    monkeypatch.setattr(chooser, "SHORTLIST", 8)
    monkeypatch.setattr(chooser, "FAMILY_FLOOR", 2)


def flat() -> PhotoRead:
    return PhotoRead(
        size=(1500, 1000),
        faces=(),
        salience=np.full((32, 48), 1.0 / (32 * 48)),
        busy=np.zeros((32, 48)),
        strength=0.0,
    )


def material(kit: BrandKit, words: dict[str, str] = WORDS) -> Material:
    return Material(
        words=words,
        kit=kit,
        photos=(A, B),
        background=None,
        logo=LOGO,
        logo_aspect=2.0,
        ink=None,
        reads={A: flat(), B: flat()},
    )


def on(feature: str, weight: float, bias: float) -> Bar:
    """A bar that looks at one measurement only."""
    coef = np.zeros(N)
    coef[INDEX[feature]] = weight
    return Bar(mean=np.zeros(N), scale=np.ones(N), coef=coef, bias=bias)


NEVER = on("photo_share", 0.0, 10.0)  # every design is under it
PHOTOS_FAIL = on("photo_share", 60.0, -5.0)  # only designs without a photo clear it


@dataclass(frozen=True)
class Scripted(Bar):
    """Fails everything for the first `fails` rankings it judges, then clears all."""

    fails: int = 0
    calls: list[int] = field(default_factory=list)

    def p_bad(self, x: np.ndarray) -> np.ndarray:
        self.calls.append(1)
        n = len(np.atleast_2d(x))
        return np.full(n, 0.9 if len(self.calls) <= self.fails else 0.1)


def scripted(fails: int) -> Scripted:
    return Scripted(np.zeros(N), np.ones(N), np.zeros(N), 0.0, fails=fails)


def ranked_for(kit: BrandKit, words: dict[str, str] = WORDS) -> list[chooser.Candidate]:
    return candidates(material(kit, words), ["4:5"], REPO, seed=7)


def test_with_no_bar_nothing_changes(kit: BrandKit, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repair, "active_bar", lambda: None)
    ranked = ranked_for(kit)
    out = repair.improve(material(kit), ["4:5"], REPO, seed=7, ranked=ranked)
    assert out.ranked == ranked and out.tried == () and out.flag is None


def test_designs_that_clear_the_bar_go_first(
    kit: BrandKit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repair, "TOP", 100)  # measure every candidate
    ranked = ranked_for(kit)
    assert ranked[0].family != "type"  # the critic put a photo design first
    out = repair.improve(material(kit), ["4:5"], REPO, seed=7, ranked=ranked, bar=PHOTOS_FAIL)
    assert out.cleared and [one.step for one in out.tried] == ["as drafted"]
    assert out.ranked[0].family == "type"
    p = [one.parts["p_bad"] for one in out.ranked]
    first_under = next(i for i, value in enumerate(p) if value > PHOTOS_FAIL.threshold)
    assert all(value > PHOTOS_FAIL.threshold for value in p[first_under:])  # clear ones first
    assert sorted(one.design_id for one in out.ranked) == sorted(one.design_id for one in ranked)


def test_when_nothing_clears_every_step_runs_and_the_post_is_flagged(kit: BrandKit) -> None:
    out = repair.improve(material(kit), ["4:5"], REPO, seed=7, ranked=ranked_for(kit), bar=NEVER)
    assert not out.cleared and out.ranked
    assert [one.step for one in out.tried] == STEPS
    flag = out.flag
    assert flag is not None and flag.code == "below_bar" and flag.severity == "warning"
    assert "the other photo first, the shortest headline" in flag.message
    assert "and a wider search." in flag.message


def test_repairs_stop_at_the_first_step_that_works(kit: BrandKit) -> None:
    bar = scripted(fails=2)  # as drafted and the other photo first both fail
    out = repair.improve(material(kit), ["4:5"], REPO, seed=7, ranked=ranked_for(kit), bar=bar)
    assert out.cleared and out.flag is None
    assert [one.step for one in out.tried] == STEPS[:3]
    # The shortest headline is now the only headline any design can use.
    texts = {
        layer.content
        for one in out.ranked
        for layer in one.spec.layers
        if hasattr(layer, "content")
    }
    assert "Boiler checks" in texts and WORDS["headline"] not in texts


def test_steps_that_cannot_help_are_not_tried(kit: BrandKit) -> None:
    words = {key: value for key, value in WORDS.items() if key != "headline_short"}
    one_photo = Material(**{**material(kit, words).__dict__, "photos": (A,)})
    ranked = candidates(one_photo, ["4:5"], REPO, seed=7)
    out = repair.improve(one_photo, ["4:5"], REPO, seed=7, ranked=ranked, bar=NEVER)
    assert [one.step for one in out.tried] == [
        "as drafted",
        "words-only designs on equal terms",
        "a wider search",
    ]


def test_out_of_time_stops_starting_repairs(kit: BrandKit) -> None:
    out = repair.improve(
        material(kit), ["4:5"], REPO, seed=7, ranked=ranked_for(kit), bar=NEVER, budget_s=0.0
    )
    assert [one.step for one in out.tried] == ["as drafted"]
    assert list(out.skipped) == STEPS[1:]
    assert out.flag is not None and "also tried" not in out.flag.message


# --- in a real draft ------------------------------------------------------------------


def _png(at: float) -> bytes:
    image = Image.new("RGB", (1500, 1000), (200, 205, 210))
    ImageDraw.Draw(image).ellipse([int(at * 1500) - 150, 350, int(at * 1500) + 150, 650], "red")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


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
    await service.store_image(ctx, client_id, _png(0.7), kind="photo")
    [client] = await supa.select("clients", id=f"eq.{client_id}")
    [post] = await supa.insert(
        "posts",
        {
            "agency_id": agency,
            "client_id": client_id,
            "slug": "below-the-bar",
            "brief": "Winter boiler checks, 20% off.",
            "template_id": AUTO,
            "aspects": ["4:5"],
        },
    )
    return ctx, post, client


async def test_a_draft_below_the_bar_is_flagged_and_says_what_was_tried(
    studio: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stratum_worker.app import service

    monkeypatch.setattr(repair, "active_bar", lambda: NEVER)
    ctx, post, client = studio
    version = await service.draft(ctx, post, client)
    flags = await ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    assert "below_bar" in {one["code"] for one in flags}
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}")
    for row in rows:
        assert row["detail"]["critic_model"] == "critic-v1"
        assert row["detail"]["repairs"][0] == "as drafted"
    # Only the top of the ranking is measured against the bar; an explored option (the
    # seed comes from the post's random id) may come from further down (I-38).
    [best] = [row for row in rows if row["slot"] == 1]
    assert best["detail"]["p_bad"] > 0.5
    assert all(row["detail"]["p_bad"] > 0.5 for row in rows if "p_bad" in row["detail"])


async def test_a_draft_with_no_bar_records_the_critic_and_nothing_else(
    studio: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stratum_worker.app import service

    monkeypatch.setattr(repair, "active_bar", lambda: None)
    ctx, post, client = studio
    version = await service.draft(ctx, post, client)
    flags = await ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    assert "below_bar" not in {one["code"] for one in flags}
    rows = await ctx.supa.select("post_options", post_id=f"eq.{post['id']}")
    assert all(
        row["detail"]["critic_model"] == "critic-v1"
        and "repairs" not in row["detail"]
        and "p_bad" not in row["detail"]
        for row in rows
    )
