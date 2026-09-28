"""Brand kits through the service, on the SQLite stand-in with real drafts.

The promise that matters: changing a client's colours never changes a poster that
already exists. The client approved navy; a download next month must still be navy.
Everything made from now on uses the new colours.

Also: the palette read from a logo is a suggestion until a person saves it; saving
refuses a broken palette with a sentence; two designers saving at once cannot
silently overwrite each other; and the newest logo is the one used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from logo_samples import NAVY, ORANGE, draw, two_colour
from stratum_spec.models import PosterSpec
from stratum_worker.app import service
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
TEAL = {
    "primary": "#19b5b0",
    "secondary": "#ffd23f",
    "surface": "#f4fdfd",
    "text": "#1a1a1a",
    "text_on_primary": "#1a1a1a",
    "text_on_dark": "#ffffff",
    "muted": "#4d7a79",
}


class Shop:
    def __init__(self, ctx: service.Context, client_id: str) -> None:
        self.ctx = ctx
        self.client_id = client_id

    async def client(self) -> dict[str, Any]:
        [row] = await self.ctx.supa.select("clients", id=f"eq.{self.client_id}")
        return row

    async def post(self, slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
        [post] = await self.ctx.supa.insert(
            "posts",
            {
                "agency_id": self.ctx.agency_id,
                "client_id": self.client_id,
                "slug": slug,
                "brief": "20% off boiler servicing this month.",
                "template_id": "offer_4x5",
                "aspects": ["4:5"],
            },
        )
        version = await service.draft(self.ctx, post, await self.client())
        return post, version

    async def save(self, palette: dict[str, str], **kwargs: Any) -> Any:
        client = await self.client()
        expected = kwargs.pop("expected_version", client["brand"]["version"])
        return await service.save_brand(
            self.ctx, client, palette, expected_version=expected, **kwargs
        )


@pytest.fixture
async def shop(tmp_path: Path) -> Shop:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    ctx = service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=fake.gateway(),
        agency_id=agency,
        user_id="designer-1",
    )
    return Shop(ctx, client_id)


def spec_of(version: dict[str, Any]) -> PosterSpec:
    return PosterSpec.model_validate(version["spec"])


# --- old posters keep their colours ------------------------------------------------


async def test_new_colours_never_recolour_an_existing_poster(shop: Shop) -> None:
    post, version = await shop.post("boiler-offer")
    assert spec_of(version).brand_kit_version == 1
    before = await service.render_preview(shop.ctx, version, post, await shop.client(), "4:5")

    saved, _ = await shop.save(TEAL)
    assert saved.version == 2

    # The preview cache is gone (a new server, a cleared disk): the poster is drawn
    # again from its spec, and must come out exactly as the client approved it.
    service.preview_path(shop.ctx.settings, version["id"], "4:5").unlink()
    again = await service.render_preview(shop.ctx, version, post, await shop.client(), "4:5")
    assert again == before

    old_kit = await service.kit_for(shop.ctx, await shop.client(), 1)
    assert old_kit.palette["primary"] == "#12264a"


async def test_posts_made_after_the_change_use_the_new_colours(shop: Shop) -> None:
    await shop.save(TEAL)
    _, version = await shop.post("spring-offer")
    assert spec_of(version).brand_kit_version == 2


async def test_a_quick_change_to_an_old_poster_keeps_its_colours(shop: Shop) -> None:
    post, _ = await shop.post("boiler-offer")
    await shop.save(TEAL)
    changed = await service.quick(shop.ctx, post, await shop.client(), "logo_bigger")
    [row] = await shop.ctx.supa.select("post_versions", id=f"eq.{changed['id']}")
    assert spec_of(row).brand_kit_version == 1


async def test_a_lost_kit_version_is_refused_not_replaced(shop: Shop) -> None:
    with pytest.raises(DraftError, match=r"Version 9 .* is missing"):
        await service.kit_for(shop.ctx, await shop.client(), 9)


# --- saving -----------------------------------------------------------------------------


async def test_saving_keeps_fonts_and_says_how_readable_it_is(shop: Shop) -> None:
    before = await shop.client()
    saved, checks = await shop.save(TEAL)
    assert saved.fonts == service.brand_of(before).fonts
    assert saved.palette["primary"] == "#19b5b0"
    assert [c.what for c in checks] == [
        "Words on the main colour",
        "Words on the page colour",
        "Words on dark bands and photo shading",
    ]
    assert all(c.ok for c in checks)


async def test_saving_the_same_colours_twice_is_one_version(shop: Shop) -> None:
    first, _ = await shop.save(TEAL)
    second, _ = await shop.save(TEAL)
    assert first.version == second.version == 2


async def test_a_designer_saving_over_someone_elses_change_is_stopped(shop: Shop) -> None:
    await shop.save(TEAL)  # someone else, a moment ago: now version 2
    with pytest.raises(service.BrandConflict, match="Someone else changed"):
        await shop.save({**TEAL, "primary": "#000000"}, expected_version=1)
    assert (await shop.client())["brand"]["palette"]["primary"] == "#19b5b0"


async def test_a_stale_client_row_cannot_win_the_race_either(shop: Shop) -> None:
    stale = await shop.client()  # both designers opened the kit at version 1
    await shop.save(TEAL)
    with pytest.raises(service.BrandConflict):
        # The row this caller holds still says version 1; the database no longer does.
        stale["brand"] = {**stale["brand"], "version": 1}
        await service.save_brand(
            shop.ctx, stale, {**TEAL, "primary": "#000000"}, expected_version=1
        )


@pytest.mark.parametrize(
    ("palette", "said"),
    [
        ({k: v for k, v in TEAL.items() if k != "text_on_dark"}, "text_on_dark is missing"),
        ({**TEAL, "primary": "navy"}, "primary must be a colour like #12264a"),
        ({**TEAL, "primary": "#12264"}, "primary must be a colour"),
    ],
)
async def test_a_broken_palette_is_refused_with_a_sentence(
    shop: Shop, palette: dict[str, str], said: str
) -> None:
    with pytest.raises(DraftError, match=said):
        await shop.save(palette)
    assert (await shop.client())["brand"]["version"] == 1


async def test_accepting_a_suggestion_is_recorded_with_what_was_changed(shop: Shop) -> None:
    suggested = {**TEAL, "secondary": "#ff0000"}
    await shop.save(TEAL, suggested=suggested)
    [row] = await shop.ctx.supa.select("decision_log")
    assert row["kind"] == "suggestion_accepted"
    assert row["actor"] == "designer-1"
    assert row["detail"]["what"] == "brand_palette"
    assert row["detail"]["changed"] == ["secondary"]
    assert row["detail"]["version"] == 2


async def test_history_cannot_be_written_around_the_database(shop: Shop) -> None:
    with pytest.raises(fake.SupabaseError, match="permission denied"):
        await shop.ctx.supa.insert(
            "brand_kits",
            {"agency_id": shop.ctx.agency_id, "client_id": shop.client_id, "version": 5},
        )


# --- reading the logo -------------------------------------------------------------------


async def test_the_suggestion_reads_the_newest_logo(shop: Shop) -> None:
    await service.store_image(shop.ctx, shop.client_id, draw(two_colour()), kind="logo")
    made = await service.brand_suggestion(shop.ctx, await shop.client())
    assert made.kit.palette["primary"] == NAVY
    assert made.kit.palette["secondary"] == ORANGE
    # A suggestion saves nothing.
    assert (await shop.client())["brand"]["version"] == 1


async def test_a_draft_uses_the_newest_logo(shop: Shop) -> None:
    newest = await service.store_image(shop.ctx, shop.client_id, draw(two_colour()), kind="logo")
    _, version = await shop.post("boiler-offer")
    logo = next(layer for layer in spec_of(version).layers if layer.type == "logo")
    assert getattr(logo, "asset", None) == newest


async def test_a_client_with_no_logo_is_told_to_upload_one(shop: Shop) -> None:
    [bare] = await shop.ctx.supa.insert(
        "clients",
        {
            "agency_id": shop.ctx.agency_id,
            "slug": "no-logo-yet",
            "name": "No Logo Yet",
            "brand": service.starter_kit("no-logo-yet").model_dump(mode="json"),
            "rules": {"client_id": "no-logo-yet"},
        },
    )
    with pytest.raises(DraftError, match="No Logo Yet has no logo yet"):
        await service.brand_suggestion(shop.ctx, bare)


async def test_only_this_clients_logos_can_be_read(shop: Shop) -> None:
    photos = await shop.ctx.supa.select("assets", kind="eq.photo")
    with pytest.raises(DraftError, match="not one of"):
        await service.brand_suggestion(shop.ctx, await shop.client(), photos[0]["sha256"])
