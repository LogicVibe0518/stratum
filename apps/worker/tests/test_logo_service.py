"""Logo panels through a real draft on the SQLite stand-in.

A navy logo drafted onto a design that would hide it comes out on a panel, with no
"hard to see" blocker. The fictional client's white logo, which already shows,
comes out exactly as before. Hiding and showing the logo keeps the panel's choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from logo_samples import draw, two_colour
from stratum_spec import LogoLayer, load
from stratum_worker.app import service
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
async def shop(tmp_path: Path) -> tuple[service.Context, str]:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    ctx = service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
    )
    return ctx, client_id


async def draft(ctx: service.Context, client_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    [post] = await ctx.supa.insert(
        "posts",
        {
            "agency_id": ctx.agency_id,
            "client_id": client_id,
            "slug": "boiler-offer",
            "brief": "20% off boiler servicing this month.",
            "template_id": "offer_4x5",
            "aspects": ["4:5", "9:16"],
        },
    )
    [client] = await ctx.supa.select("clients", id=f"eq.{client_id}")
    return post, await service.draft(ctx, post, client)


async def test_a_logo_that_would_vanish_is_drafted_on_a_panel(
    shop: tuple[service.Context, str],
) -> None:
    ctx, client_id = shop
    await service.store_image(ctx, client_id, draw(two_colour()), kind="logo")
    _, version = await draft(ctx, client_id)
    spec = load(version["spec"])
    logo = spec.layer("logo")
    assert isinstance(logo, LogoLayer) and logo.plate is not None
    assert spec.layer("logo_chip").visible is False
    flags = await ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
    assert "logo_hard_to_see" not in {flag["code"] for flag in flags}


async def test_a_logo_that_already_shows_is_drafted_as_before(
    shop: tuple[service.Context, str],
) -> None:
    ctx, client_id = shop  # the seeded fictional logo is white, on a dark chip
    _, version = await draft(ctx, client_id)
    spec = load(version["spec"])
    logo = spec.layer("logo")
    assert isinstance(logo, LogoLayer) and logo.plate is None
    assert spec.layer("logo_chip").visible is True


async def test_showing_the_logo_again_does_not_bring_back_its_old_chip(
    shop: tuple[service.Context, str],
) -> None:
    ctx, client_id = shop
    await service.store_image(ctx, client_id, draw(two_colour()), kind="logo")
    post, _ = await draft(ctx, client_id)
    [client] = await ctx.supa.select("clients", id=f"eq.{client_id}")
    await service.quick(ctx, post, client, "hide_logo")
    shown = await service.quick(ctx, post, client, "show_logo")
    [row] = await ctx.supa.select("post_versions", id=f"eq.{shown['id']}")
    spec = load(row["spec"])
    assert spec.layer("logo").visible is True
    assert spec.layer("logo_chip").visible is False
