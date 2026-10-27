"""An edit keeps the poster's facts (edit/facts.py), found on the live site on 26 Oct.

A designer wrote LIVE_MESSAGE on a "30% off home cleaning" poster. On one version the
model put the Black Friday sale into the offer line and kept the 30%; on another it
set the headline to "Black Friday Sale" and the 30% was gone.

What a reviewer should be able to repeat back:
- new words may move a price, a percentage, a date or a phone number, or say it
  differently, but not leave it out unless the message is about that kind of fact;
- when they would, the model is asked once more with the facts named, and its second
  answer is used if it keeps them;
- if it still drops them, nothing changes and the designer is told which, in a sentence;
- "make it 40% off" changes the figure, and hiding a line is not a rewrite.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from stratum_spec import load as load_spec
from stratum_spec.models import TextLayer
from stratum_worker.app import service
from stratum_worker.draft.preflight import facts_missing
from stratum_worker.edit.facts import asked_about, keep_line, refusal
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import EditParseRequest, TaskName
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
LIVE_MESSAGE = (
    "2 times you have written signup and book and nowhere its mentioning about the black "
    "friday sale"
)
# fake.FAKE_COPY on offer_4x5: offer "20% off every service booked this month",
# call to action "Call 0161 496 0000".
DROPS = {"ops": [{"op": "set_text", "layer": "offer", "text": "Black Friday sale"}]}
KEEPS = {
    "ops": [
        {"op": "set_text", "layer": "offer", "text": "Black Friday sale: 20% off every service"}
    ]
}


# --- what counts as a fact, and as asking about one -------------------------------------


def test_a_percentage_date_price_or_phone_number_left_out_is_found() -> None:
    before = "30% off home cleaning\nBook before 31 Oct\nFrom £49\nCall 0161 496 0000"
    after = "Black Friday Sale\nBook now"
    assert facts_missing(before, after) == ["31 Oct", "30%", "£49", "0161 496 0000"]


def test_the_same_fact_said_differently_is_still_there() -> None:
    before = "30% off, book by 31 Oct, call 0161 496 0000, GBP 49"
    after = "Black Friday: 30 % off until 31 October. £49. Ring 0161-496-0000"
    assert facts_missing(before, after) == []


@pytest.mark.parametrize(
    ("message", "fact", "about"),
    [
        (LIVE_MESSAGE, "30%", False),
        ("make it 40% off", "30%", True),
        ("drop the discount", "30%", True),
        ("remove 30%", "30%", True),
        ("the deadline is wrong", "31 Oct", True),
        ("make it November", "31 Oct", True),
        ("shorter call to action", "0161 496 0000", False),
        ("take the phone number off", "0161 496 0000", True),
        ("make the headline bigger", "31 Oct", False),
    ],
)
def test_a_message_is_about_a_fact_only_when_it_says_so(
    message: str, fact: str, about: bool
) -> None:
    assert asked_about(message, fact) is about


def test_the_sentences_name_the_facts() -> None:
    assert keep_line(["30%"]) == "Whatever else changes, the poster must still show '30%'."
    assert refusal(["30%", "31 Oct"]).startswith(
        "That change would take '30%' and '31 Oct' off the poster, so I left it as it was."
    )


# --- in the product ------------------------------------------------------------------------


def photo(seed: int) -> bytes:
    y, x = np.mgrid[0:400, 0:500]
    on = ((x // 8) + (y // 8) + seed) % 2 == 1
    rgb = np.where(on[..., None], [200, 150, 90], [60, 110, 170]).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "PNG")
    return out.getvalue()


class Studio:
    def __init__(self, ctx: service.Context, client_id: str) -> None:
        self.ctx, self.client_id = ctx, client_id

    async def client(self) -> dict[str, Any]:
        [row] = await self.ctx.supa.select("clients", id=f"eq.{self.client_id}")
        return row

    async def post(self, template: str, brief: str) -> tuple[dict[str, Any], dict[str, Any]]:
        [post] = await self.ctx.supa.insert(
            "posts",
            {
                "agency_id": self.ctx.agency_id,
                "client_id": self.client_id,
                "slug": f"post-{template}",
                "brief": brief,
                "template_id": template,
                "aspects": ["4:5"],
            },
        )
        version = await service.draft(self.ctx, post, await self.client())
        [post] = await self.ctx.supa.select("posts", id=f"eq.{post['id']}")
        return post, version


async def studio_answering(
    tmp_path: Path, *answers: dict[str, object]
) -> tuple[Studio, FakeProvider]:
    provider = FakeProvider(results=[fake.FAKE_COPY, *answers])
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    router = Router(
        providers={provider.name: provider},
        policies={task: TaskPolicy(order=(provider.name,)) for task in TaskName},
    )
    ctx = service.Context(
        supa=world.factory(agency),
        settings=world.settings(REPO),
        gateway=Gateway(router=router, budget=Budget(daily_cap_usd=2.0)),
        agency_id=agency,
    )
    studio = Studio(ctx, client_id)
    for seed in (1, 2):
        await service.store_image(ctx, client_id, photo(seed), kind="photo")
    return studio, provider


def words(version: dict[str, Any]) -> str:
    spec = load_spec(version["spec"])
    return " | ".join(
        layer.content for layer in spec.layers if isinstance(layer, TextLayer) and layer.visible
    )


def asked(provider: FakeProvider) -> list[str]:
    return [
        request.message
        for task, request in provider.calls
        if task == TaskName.EDIT_PARSE and isinstance(request, EditParseRequest)
    ]


async def test_new_words_that_drop_the_offer_are_asked_for_again_and_keep_it(
    tmp_path: Path,
) -> None:
    studio, provider = await studio_answering(tmp_path, DROPS, KEEPS)
    post, _ = await studio.post("offer_4x5", "Boiler service, 20% off this month.")
    out = await service.tweak(studio.ctx, post, await studio.client(), LIVE_MESSAGE)
    assert out["outcome"] == "applied"
    assert "Black Friday sale: 20% off" in words(out)
    first, second = asked(provider)
    assert first == LIVE_MESSAGE
    assert second.startswith("Whatever else changes, the poster must still show '20%'.")
    [edit] = await studio.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["ops"] == KEEPS["ops"] and edit["message"] == LIVE_MESSAGE


async def test_if_the_model_drops_them_twice_nothing_changes_and_it_says_which(
    tmp_path: Path,
) -> None:
    studio, _ = await studio_answering(tmp_path, DROPS, DROPS)
    post, before = await studio.post("offer_4x5", "Boiler service, 20% off this month.")
    out = await service.tweak(studio.ctx, post, await studio.client(), LIVE_MESSAGE)
    assert out["outcome"] == "needs_human"
    assert out["why"].startswith("That change would take '20%' off the poster")
    versions = await studio.ctx.supa.select("post_versions", post_id=f"eq.{post['id']}")
    assert [v["id"] for v in versions] == [before["id"]]
    [edit] = await studio.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["outcome"] == "needs_human"


async def test_asking_for_a_new_figure_changes_it_without_asking_again(tmp_path: Path) -> None:
    forty = {"ops": [{"op": "set_text", "layer": "offer", "text": "40% off every service"}]}
    studio, provider = await studio_answering(tmp_path, forty)
    post, _ = await studio.post("offer_4x5", "Boiler service, 20% off this month.")
    out = await service.tweak(studio.ctx, post, await studio.client(), "make it 40% off")
    assert out["outcome"] == "applied" and "40% off" in words(out)
    assert len(asked(provider)) == 1


async def test_hiding_a_line_is_not_a_rewrite(tmp_path: Path) -> None:
    hide = {"ops": [{"op": "set_visible", "layer": "offer", "visible": False}]}
    studio, provider = await studio_answering(tmp_path, hide)
    post, _ = await studio.post("offer_4x5", "Boiler service, 20% off this month.")
    out = await service.tweak(studio.ctx, post, await studio.client(), "hide the offer")
    assert out["outcome"] == "applied" and "20%" not in words(out)
    assert len(asked(provider)) == 1
