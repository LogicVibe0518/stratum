"""Headline versions through a real draft (M10e): written once, set per design."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from stratum_spec import BrandKit, TextLayer
from stratum_spec import load as load_spec
from stratum_worker.app import service
from stratum_worker.design.copyfit import best_copy, versions
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import TaskName
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
COPY = {
    **fake.FAKE_COPY,
    "headline": "Boiler service, sorted before winter",
    "headline_short": "Boilers sorted",
    "headline_long": "Boiler service sorted before winter, with 20% off every booking this month",
}


async def draft(tmp_path: Path, words: dict[str, Any], template: str) -> tuple[Any, Any]:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    provider = FakeProvider(results=[words])
    ctx = service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=Gateway(
            router=Router(
                providers={provider.name: provider},
                policies={task: TaskPolicy(order=(provider.name,)) for task in TaskName},
            ),
            budget=Budget(daily_cap_usd=2.0),
        ),
        agency_id=agency,
    )
    [client] = await ctx.supa.select("clients", id=f"eq.{client_id}")
    [post] = await ctx.supa.insert(
        "posts",
        {
            "agency_id": agency,
            "client_id": client_id,
            "slug": "versions",
            "brief": "Boiler service, 20% off every booking this month.",
            "template_id": template,
            "aspects": ["4:5"],
        },
    )
    version = await service.draft(ctx, post, client)
    [post] = await ctx.supa.select("posts", id=f"eq.{post['id']}")
    return post, load_spec(version["spec"])


def fake_brand() -> dict[str, Any]:
    return json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text())


def headline(spec: Any) -> str:
    layer = spec.layer("headline")
    assert isinstance(layer, TextLayer)
    return layer.content


async def test_the_post_keeps_every_version(tmp_path: Path) -> None:
    post, _ = await draft(tmp_path, COPY, "ds.panel.1.0010110")
    assert post["words"]["headline_short"] == "Boilers sorted"
    assert post["words"]["headline_long"].startswith("Boiler service sorted before winter, with")


@pytest.mark.parametrize(
    "design", ["ds.panel.1.0010110", "ds.overlay.1.000000", "ds.type.1.000000"]
)
async def test_each_design_sets_the_version_it_suits(tmp_path: Path, design: str) -> None:
    post, spec = await draft(tmp_path, COPY, design)
    world = World(tmp_path / "db.sqlite")
    [logo] = await world.factory(post["agency_id"]).select("assets", kind="eq.logo")
    found = best_copy(
        design,
        versions(post["words"]),
        BrandKit.model_validate(fake_brand()),
        REPO,
        logo_aspect=logo["width"] / logo["height"],
    )
    assert found is not None
    assert headline(spec) == found[0].headline


async def test_a_poster_can_carry_a_version_other_than_the_main_one(tmp_path: Path) -> None:
    chosen = set()
    for design in ("ds.panel.1.0010110", "ds.overlay.1.000000", "ds.type.1.000000"):
        _, spec = await draft(tmp_path / design, COPY, design)
        chosen.add(headline(spec))
    assert len(chosen) > 1 or COPY["headline"] not in chosen


async def test_versions_that_add_nothing_are_dropped(tmp_path: Path) -> None:
    odd = {**COPY, "headline_short": COPY["headline"] + " today", "headline_long": "Boilers"}
    post, _ = await draft(tmp_path, odd, "ds.panel.1.0010110")
    assert post["words"]["headline_short"] == "" and post["words"]["headline_long"] == ""


async def test_copy_from_an_older_model_answer_still_drafts(tmp_path: Path) -> None:
    post, spec = await draft(tmp_path, dict(fake.FAKE_COPY), "ds.panel.1.0010110")
    assert headline(spec) == fake.FAKE_COPY["headline"]
    assert post["words"]["headline_short"] == ""
