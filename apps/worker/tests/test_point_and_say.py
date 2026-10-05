"""Point and say (M10h): the part tapped or picked before typing.

Live, 5 Oct: vague tweaks failed because the model had to guess which part was meant.
The promises:
- a tap means the topmost visible part there, text by its letters (a tap beside a
  short headline on a photo means the photo);
- a short message about the pointed part ("bigger", "hide", "a bit darker", "up")
  is carried out with no model: no wait, no cost, nothing to misread;
- anything longer goes to the model, which is told what was pointed at, and "this"
  means that part;
- pointing at nothing, or at a part that is not there, changes nothing about how a
  message is read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from stratum_spec import TextLayer
from stratum_spec.ops import Dim, Move, Scale, SetVisible, Zoom
from stratum_worker.app import service
from stratum_worker.edit import chateval
from stratum_worker.edit.context import describe_layers, edit_request
from stratum_worker.edit.pointing import (
    MAX_DIM,
    Pointing,
    layer_at,
    pointed_layer,
    quick_ops,
    said,
)
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.tasks import EditParseRequest, TaskName
from test_smarter_tweaks import asked, studio

REPO = Path(__file__).resolve().parents[3]


def panel() -> chateval.Poster:
    return chateval.poster("panel")


# --- taps -------------------------------------------------------------------------------


def test_a_tap_on_the_headline_letters_is_the_headline() -> None:
    shown = panel()
    headline = next(one for one in shown.spec.layers if one.id == "headline")
    assert isinstance(headline, TextLayer)
    box = headline.box
    found = layer_at(shown.spec, box.x + 0.05, box.y + 0.03, shown.kit, REPO)
    assert found == "headline"


def test_a_tap_on_the_photo_is_the_photo_and_off_the_poster_is_nothing() -> None:
    shown = panel()
    assert layer_at(shown.spec, 0.5, 0.25, shown.kit, REPO) == "bg"
    assert layer_at(shown.spec, 1.5, 0.25, shown.kit, REPO) is None


def test_a_tap_beside_a_short_headline_means_what_is_under_it() -> None:
    """The headline's box is wider than its words: a tap in its empty right end hits
    the panel behind, not the headline."""
    shown = panel()
    spec = shown.spec.model_copy(
        update={
            "layers": [
                one.model_copy(update={"content": "Sorted"}) if one.id == "headline" else one
                for one in shown.spec.layers
            ]
        }
    )
    headline = next(one for one in spec.layers if one.id == "headline")
    right = headline.box.x + headline.box.w - 0.01
    assert layer_at(spec, right, headline.box.y + 0.03, shown.kit, REPO) == "panel"


def test_a_part_picked_by_name_must_be_on_the_poster() -> None:
    shown = panel()
    assert pointed_layer(shown.spec, shown.kit, REPO, layer="logo") == "logo"
    assert pointed_layer(shown.spec, shown.kit, REPO, layer="nope") is None
    assert pointed_layer(shown.spec, shown.kit, REPO) is None
    assert Pointing(layer="logo").parts()["layer"] == "logo"


# --- short messages, no model -----------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "meant"),
    [
        ("bigger", "bigger"),
        ("make this a bit bigger please", "bigger"),
        ("hide it", "hide"),
        ("a little darker", "darker"),
        ("move it up", "up"),
        ("zoom in", "closer"),
        ("bigger and darker", None),  # two things: the model
        ("make this orange", None),  # not a short instruction: the model
        ("", None),
    ],
)
def test_a_short_message_says_one_thing_or_nothing(message: str, meant: str | None) -> None:
    assert said(message) == meant


def test_short_messages_become_ops_for_the_part_pointed_at() -> None:
    spec = panel().spec
    assert quick_ops(spec, "logo", "bigger") == [Scale(layer="logo", factor=1.15)]
    assert quick_ops(spec, "headline", "smaller") == [Scale(layer="headline", factor=1 / 1.15)]
    assert quick_ops(spec, "cta", "hide this") == [SetVisible(layer="cta", visible=False)]
    assert quick_ops(spec, "logo", "up a bit") == [Move(layer="logo", dx=0.0, dy=-0.03)]
    [dim] = quick_ops(spec, "bg", "darker") or []
    assert isinstance(dim, Dim) and 0 < dim.amount <= MAX_DIM
    assert quick_ops(spec, "bg", "bigger") == [Zoom(layer="bg", factor=1.15)]


def test_what_does_not_suit_the_part_goes_to_the_model() -> None:
    spec = panel().spec
    assert quick_ops(spec, "bg", "up") is None  # a photo pans; the model decides how
    assert quick_ops(spec, "bg", "lighter") is None  # not dimmed: nothing to lighten
    assert quick_ops(spec, "logo", "darker") is None  # a logo is never recoloured
    assert quick_ops(spec, "nope", "bigger") is None


# --- the model is told ------------------------------------------------------------------


def test_the_model_is_told_what_was_pointed_at() -> None:
    shown = panel()
    request = edit_request(
        shown.spec,
        shown.kit,
        {},
        {},
        template_id=shown.design_id,
        photos=2,
        message="make this orange",
        pointed="offer",
    )
    layers = describe_layers(shown.spec, shown.kit, {}, {})
    assert request.pointed == f"offer: {layers['offer']}"
    rendered = render(TaskName.EDIT_PARSE, request)
    assert f"<pointed>\noffer: {layers['offer']}\n</pointed>" in rendered.user
    assert '"this", "it", "here"' in rendered.system
    nothing = render(TaskName.EDIT_PARSE, request.model_copy(update={"pointed": ""}))
    assert "<pointed>\n(nothing)\n</pointed>" in nothing.user


def test_the_prompt_puts_a_phone_number_in_the_call_to_action() -> None:
    request = EditParseRequest(message="add our number", layers={"cta": "text"})
    system = " ".join(render(TaskName.EDIT_PARSE, request).system.split())
    assert "goes in the call to action" in system and "never in the headline" in system


# --- through the real tweak ---------------------------------------------------------------


async def test_a_pointed_short_message_changes_the_poster_with_no_model(tmp_path: Path) -> None:
    one = await studio(tmp_path)
    post = await one.post()
    calls = len(one.provider.calls)
    out = await service.tweak(
        one.ctx, await one.fresh(post), await one.client(), "bigger", Pointing(layer="logo")
    )
    assert out["outcome"] == "applied"
    assert len(one.provider.calls) == calls  # no model
    before, after = (await one.versions(post))[-2:]
    old = next(x for x in before["spec"]["layers"] if x["id"] == "logo")["box"]["w"]
    new = next(x for x in after["spec"]["layers"] if x["id"] == "logo")["box"]["w"]
    assert new > old
    [edit] = await one.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["outcome"] == "applied" and edit["cost_usd"] == 0
    assert edit["ops"] == [{"op": "scale", "layer": "logo", "factor": 1.15}]


async def test_a_longer_message_goes_to_the_model_with_the_part(tmp_path: Path) -> None:
    orange = {"ops": [{"op": "set_color", "layer": "offer", "color": "palette.secondary"}]}
    one = await studio(tmp_path, orange)
    post = await one.post()
    out = await service.tweak(
        one.ctx,
        await one.fresh(post),
        await one.client(),
        "make this orange",
        Pointing(layer="offer"),
    )
    assert out["outcome"] == "applied"
    assert asked(one.provider).pointed.startswith("offer: text (offer line)")


async def test_a_tap_is_read_in_the_size_it_was_made_in(tmp_path: Path) -> None:
    one = await studio(tmp_path, {"ops": [], "needs_human": "Which part?"})
    post = await one.post()
    await service.tweak(
        one.ctx,
        await one.fresh(post),
        await one.client(),
        "make this pop",
        Pointing(x=0.5, y=0.2, aspect="4:5"),
    )
    assert asked(one.provider).pointed.startswith("bg: photo")
