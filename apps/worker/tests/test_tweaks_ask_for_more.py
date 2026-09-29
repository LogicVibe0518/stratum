"""Tweaks that ask for more than the poster has room for, found on the live site.

A designer typed, into "Change something", a message with four asks in it: add the
50% offer, add the dates, keep the brand colours, and make a second post. Both
providers failed after 18-22 s and the page said "edit.parse: every provider failed".
Separately, the draft itself had quietly dropped the offer and the dates: the model
wrote them into the offer line, and the before/after design has no offer line.

The promises:
- a poster that leaves out the brief's offer or dates says so, and says where they are;
- a change no model could read is a sentence and a History row, never a 503;
- an answer cut off at the output limit is named as that, in the trace and the log;
- edit.parse has room to answer a request with several changes in it.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from stratum_worker.app import service
from stratum_worker.draft.preflight import check_brief_on_poster
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.errors import ProviderUnavailable
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers._http import CUT_OFF, UNPARSEABLE, parse_chat
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import TIMEOUTS_S, TaskName
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
BRIEF = (
    "meditation camp from 5th nov 2026 to 1st feb 2027; women and under 18s will have "
    "50% off; a never before experience of self discovery for everyone"
)
LIVE_MESSAGE = (
    "you never added that there will be 50% off for women and children below 18 like "
    "this post is fine but i neeed another one where i have this message printed take "
    "care of the brand colours also u never told that the camp begins from 5th nov 2026 "
    "to 1st feb 2027"
)
CUT = {UNPARSEABLE: CUT_OFF}


def photo(seed: int) -> bytes:
    y, x = np.mgrid[0:400, 0:500]
    on = ((x // 8) + (y // 8) + seed) % 2 == 1
    rgb = np.where(on[..., None], [200, 150, 90], [60, 110, 170]).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "PNG")
    return out.getvalue()


# --- the draft says when the brief's offer and dates are not on the poster --------------


def test_the_live_before_after_poster_is_flagged_with_what_it_left_out() -> None:
    poster = "A meditation camp for self-discovery\nJoin us for the camp\nBEFORE\nAFTER"
    caption = "Our camp runs from 5 November 2026 to 1 February 2027. Women get 50% off."
    [flag] = check_brief_on_poster(BRIEF, poster, caption, has_offer_line=False)
    assert flag.code == "brief_not_on_poster"
    assert "'5th nov', '1st feb' and '50%'" in flag.message
    assert "The caption has them." in flag.message
    assert "no line for them" in flag.message


def test_a_poster_that_shows_them_in_other_words_is_not_flagged() -> None:
    poster = "Meditation camp\n5 Nov to 1 February. Women and under-18s: 50 % off"
    assert check_brief_on_poster(BRIEF, poster, "", has_offer_line=True) == ()


def test_money_in_other_notation_counts_as_shown() -> None:
    assert check_brief_on_poster("Deposit GBP 20.", "£20 deposit", "", has_offer_line=True) == ()


def test_a_design_with_an_offer_line_is_told_how_to_add_them() -> None:
    [flag] = check_brief_on_poster(BRIEF, "Meditation camp", "", has_offer_line=True)
    assert "type a change such as 'add 5th nov to the offer line'" in flag.message
    assert "caption" not in flag.message


def test_two_dates_side_by_side_are_two_dates() -> None:
    [flag] = check_brief_on_poster("5th nov 1st feb", "", "", has_offer_line=True)
    assert "'5th nov' and '1st feb'" in flag.message
    assert "nov 1st" not in flag.message


def test_a_brief_with_no_offer_or_dates_asks_for_nothing() -> None:
    assert check_brief_on_poster("A calm place to breathe.", "", "", has_offer_line=True) == ()


# --- on real drafts, tweaks and quick changes --------------------------------------------


class Studio:
    def __init__(self, world: World, agency: str, client_id: str, router: Router) -> None:
        self.ctx = service.Context(
            supa=world.factory(agency),
            settings=world.settings(REPO),
            gateway=Gateway(router=router, budget=Budget(daily_cap_usd=2.0)),
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
                "slug": f"post-{template}",
                "brief": brief,
                "template_id": template,
                "aspects": ["4:5"],
            },
        )
        version = await service.draft(self.ctx, post, await self.client())
        [post] = await self.ctx.supa.select("posts", id=f"eq.{post['id']}")
        return post, version

    async def codes(self, version: dict[str, Any]) -> set[str]:
        rows = await self.ctx.supa.select("flags", post_version_id=f"eq.{version['id']}")
        return {row["code"] for row in rows}


async def studio_with(tmp_path: Path, provider: FakeProvider) -> Studio:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    router = Router(
        providers={provider.name: provider},
        policies={task: TaskPolicy(order=(provider.name,)) for task in TaskName},
    )
    studio = Studio(world, agency, client_id, router)
    for seed in (1, 2):
        await service.store_image(studio.ctx, client_id, photo(seed), kind="photo")
    return studio


async def test_a_before_after_draft_says_the_offer_and_dates_are_missing(tmp_path: Path) -> None:
    studio = await studio_with(tmp_path, FakeProvider(results=[fake.FAKE_COPY]))
    _, version = await studio.post("before_after_4x5", BRIEF)
    assert "brief_not_on_poster" in await studio.codes(version)


async def test_an_offer_design_that_prints_the_offer_is_not_flagged(tmp_path: Path) -> None:
    studio = await studio_with(tmp_path, FakeProvider(results=[fake.FAKE_COPY]))
    # FAKE_COPY's offer line is "20% off every service booked this month".
    _, version = await studio.post("offer_4x5", "Boiler service, 20% off this month.")
    assert "brief_not_on_poster" not in await studio.codes(version)


async def test_the_flag_stays_after_a_quick_change_until_the_poster_says_it(
    tmp_path: Path,
) -> None:
    studio = await studio_with(tmp_path, FakeProvider(results=[fake.FAKE_COPY]))
    post, _ = await studio.post("before_after_4x5", BRIEF)
    after = await service.quick(studio.ctx, post, await studio.client(), "hide_logo")
    assert "brief_not_on_poster" in await studio.codes(after)


# --- a change no model could read ---------------------------------------------------------


async def test_a_cut_off_answer_is_a_sentence_and_a_history_row_not_a_503(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = FakeProvider(name="luna", results=[fake.FAKE_COPY, CUT, CUT, CUT])
    studio = await studio_with(tmp_path, provider)
    post, before = await studio.post("before_after_4x5", BRIEF)

    with caplog.at_level(logging.WARNING, logger="stratum.gateway"):
        out = await service.tweak(studio.ctx, post, await studio.client(), LIVE_MESSAGE)

    assert out["outcome"] == "failed"
    assert "shorter changes" in out["why"]
    assert "every provider failed" not in out["why"]
    [edit] = await studio.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["outcome"] == "failed" and edit["message"] == LIVE_MESSAGE
    versions = await studio.ctx.supa.select("post_versions", post_id=f"eq.{post['id']}")
    assert [v["id"] for v in versions] == [before["id"]]  # the poster is untouched
    assert f"edit.parse via luna: invalid_output ({CUT_OFF})" in caplog.text
    assert "50%" not in caplog.text  # the designer's words never reach the log


async def test_a_provider_outage_says_try_again_and_that_instant_changes_work(
    tmp_path: Path,
) -> None:
    down = ProviderUnavailable("luna: HTTP 502: bad gateway")
    provider = FakeProvider(name="luna", results=[fake.FAKE_COPY], failures=[None, down, down])
    studio = await studio_with(tmp_path, provider)
    post, _ = await studio.post("before_after_4x5", BRIEF)
    out = await service.tweak(studio.ctx, post, await studio.client(), "logo bigger")
    assert out["outcome"] == "failed"
    assert "not answering right now" in out["why"]
    assert "instant changes above still work" in out["why"]


# --- the budgets and the cut-off signal ---------------------------------------------------


def test_edit_parse_has_room_and_time_for_several_changes() -> None:
    config = load_config(REPO / "config" / "gateway.toml")
    assert config.max_output_tokens(TaskName.EDIT_PARSE) >= 1200
    assert TIMEOUTS_S[TaskName.EDIT_PARSE] >= 20


def test_an_answer_cut_off_at_the_limit_is_named_as_that() -> None:
    body = {
        "choices": [{"message": {"content": '{"ops": [{"op": "set_te'}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 1200},
    }
    assert parse_chat(body, "openrouter").result == {UNPARSEABLE: CUT_OFF}


def test_a_complete_answer_is_not_mistaken_for_a_cut_off_one() -> None:
    body = {"choices": [{"message": {"content": '{"ops": []}'}, "finish_reason": "length"}]}
    assert parse_chat(body, "openrouter").result == {"ops": []}


# --- a change that changes nothing (live: "i need both the images to be there") ----------


async def test_a_change_that_changes_nothing_is_not_called_applied(tmp_path: Path) -> None:
    both = {
        "ops": [
            {"op": "set_visible", "layer": "bg", "visible": True},
            {"op": "set_visible", "layer": "logo", "visible": True},
        ]
    }
    studio = await studio_with(tmp_path, FakeProvider(results=[fake.FAKE_COPY, both]))
    post, before = await studio.post("full_bleed", BRIEF)
    out = await service.tweak(
        studio.ctx, post, await studio.client(), "i need both the images to be there"
    )
    assert out["outcome"] == "needs_human"
    assert out["why"] == service.NOTHING_CHANGED
    versions = await studio.ctx.supa.select("post_versions", post_id=f"eq.{post['id']}")
    assert [v["id"] for v in versions] == [before["id"]]
    [edit] = await studio.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["outcome"] == "needs_human"


async def test_the_model_is_told_which_layers_are_hidden(tmp_path: Path) -> None:
    provider = FakeProvider(results=[fake.FAKE_COPY, {"ops": [], "needs_human": "no"}])
    studio = await studio_with(tmp_path, provider)
    post, _ = await studio.post("full_bleed", BRIEF)
    await service.quick(studio.ctx, post, await studio.client(), "hide_logo")
    await service.tweak(studio.ctx, post, await studio.client(), "show everything")
    _, request = provider.calls[-1]
    layers = getattr(request, "layers", {})
    assert ", hidden" in layers["logo"]
    assert ", hidden" not in layers["bg"]


# --- one flag per problem, naming the sizes ------------------------------------------------


def test_the_same_problem_in_two_sizes_is_one_flag_that_names_them() -> None:
    from stratum_worker.draft.models import Flag

    edge = Flag(code="logo_too_close_to_edge", message="The logo is near the edge.")
    story = Flag(code="inside_story_safe_zone", message="Behind the buttons.")
    merged = service.per_size({"4:5": (), "1:1": (edge,), "9:16": (edge, story), "16:9": ()})
    assert [f.message for f in merged] == [
        "The logo is near the edge. (1:1 and 9:16 only)",
        "Behind the buttons. (9:16 only)",
    ]


def test_a_problem_in_every_size_says_nothing_about_sizes() -> None:
    from stratum_worker.draft.models import Flag

    edge = Flag(code="logo_too_close_to_edge", message="The logo is near the edge.")
    merged = service.per_size({"4:5": (edge,), "1:1": (edge,)})
    assert [f.message for f in merged] == ["The logo is near the edge."]
