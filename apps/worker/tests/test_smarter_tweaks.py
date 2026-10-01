"""Smarter tweaks (M10c), on real drafts with a scripted model.

Live, 30 Sep: "change the colour of the text to green" on a brand with no green came
back as a technical refusal, with no hint of what the brand does have. The promises:
- a colour the brand does not have is a question naming the brand's colours, with
  changes to click, never an error and never a silent substitute;
- the model is told colours by name, the nearest brand colours to the one asked for,
  the design's style settings and the last few changes;
- "words on the left", "cream panel", "words only" switch the design and keep the
  words, the photos and the logo; the switch is logged as a taste signal;
- "undo" needs no model and brings back the version before;
- the photo can be panned, zoomed and darkened inside its frame;
- an inferred change says what it did, and a refusal is a sentence a designer reads.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from stratum_spec import ImageLayer, ShapeLayer, TextLayer
from stratum_spec import load as load_spec
from stratum_worker.app import service
from stratum_worker.design import decode
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import EditParseRequest, TaskName
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World

REPO = Path(__file__).resolve().parents[3]
PANEL = "ds.panel.1.0010110"  # photo above, an off-white panel below, words left
BRIEF = "Boiler service, 20% off this month."
CLIENT = "Kowalski Plumbing & Heating"


def photo(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:1350, 0:1080]
    base = 120 + 60 * np.sin(x / (7.0 + seed)) * np.cos(y / 9.0)
    noise = rng.integers(0, 40, size=(1350, 1080))
    rgb = np.stack([base + noise, base * 0.8 + 30, 230 - base * 0.4], axis=-1)
    out = io.BytesIO()
    Image.fromarray(rgb.clip(0, 255).astype("uint8"), "RGB").save(out, "PNG")
    return out.getvalue()


class Studio:
    def __init__(self, world: World, agency: str, client_id: str, provider: FakeProvider) -> None:
        router = Router(
            providers={provider.name: provider},
            policies={task: TaskPolicy(order=(provider.name,)) for task in TaskName},
        )
        self.provider = provider
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

    async def post(self, template: str = PANEL) -> dict[str, Any]:
        [post] = await self.ctx.supa.insert(
            "posts",
            {
                "agency_id": self.ctx.agency_id,
                "client_id": self.client_id,
                "slug": "smarter-tweaks",
                "brief": BRIEF,
                "template_id": template,
                "aspects": ["4:5"],
            },
        )
        await service.draft(self.ctx, post, await self.client())
        return await self.fresh(post)

    async def fresh(self, post: dict[str, Any]) -> dict[str, Any]:
        [row] = await self.ctx.supa.select("posts", id=f"eq.{post['id']}")
        return row

    async def tweak(self, post: dict[str, Any], message: str) -> dict[str, Any]:
        return await service.tweak(self.ctx, await self.fresh(post), await self.client(), message)

    async def versions(self, post: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.ctx.supa.select(
            "post_versions", post_id=f"eq.{post['id']}", order="version.asc"
        )

    async def latest_spec(self, post: dict[str, Any]) -> Any:
        return load_spec((await self.versions(post))[-1]["spec"])


async def studio(tmp_path: Path, *answers: dict[str, object], photos: int = 2) -> Studio:
    """A client with a logo and 1 + `photos` photos, and a model that says `answers`."""
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    one = Studio(world, agency, client_id, FakeProvider(results=[fake.FAKE_COPY, *answers]))
    for seed in range(photos):
        await service.store_image(one.ctx, client_id, photo(seed + 1), kind="photo")
    return one


def asked(provider: FakeProvider) -> EditParseRequest:
    task, request = provider.calls[-1]
    assert task is TaskName.EDIT_PARSE and isinstance(request, EditParseRequest)
    return request


# --- colours ---------------------------------------------------------------------------


async def test_a_colour_the_brand_lacks_is_a_question_with_choices(tmp_path: Path) -> None:
    green = {"ops": [{"op": "set_color", "layer": "headline", "color": "palette.green"}]}
    one = await studio(tmp_path, green)
    post = await one.post()
    out = await one.tweak(post, "change the colour of the text to green")

    assert out["outcome"] == "needs_human"
    assert out["question"] == (
        f"Green isn't one of {CLIENT}'s brand colours (navy, orange, off-white, white, slate blue)."
    )
    labels = [(choice["label"], choice["kind"]) for choice in out["choices"]]
    assert labels[-1] == ("Add green to the brand colours", "brand")
    assert all(label.startswith("Make the headline ") for label, _ in labels[:-1])
    assert len(labels) == 3
    assert "You could say: 'Make the headline" in out["why"]
    assert len(await one.versions(post)) == 1  # nothing changed
    [edit] = await one.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    assert edit["outcome"] == "needs_human"


async def test_the_model_is_told_colours_by_name_and_what_was_asked(tmp_path: Path) -> None:
    one = await studio(tmp_path, {"ops": [], "needs_human": "no"})
    post = await one.post()
    await service.quick(one.ctx, post, await one.client(), "logo_bigger")
    await one.tweak(post, "make the text green")

    request = asked(one.provider)
    assert request.colours["palette.secondary"].startswith("orange #ff6a00")
    assert "used by the headline" in request.colours["palette.text"]
    assert request.asked[0].startswith("green: NOT a brand colour")
    assert 'the "Photo and panel" design' in request.design
    assert request.recent == ["Make the logo bigger -> applied"]
    assert request.layers["headline"].startswith("text (headline), ")
    assert "can move" in request.layers["bg"]


async def test_a_question_from_the_model_is_passed_on(tmp_path: Path) -> None:
    question = {
        "ops": [
            {
                "op": "ask",
                "question": "Which text should be orange?",
                "choices": [
                    {"label": "Make the headline orange", "kind": "tweak"},
                    {"label": "Make the offer line orange", "kind": "tweak"},
                ],
            }
        ]
    }
    one = await studio(tmp_path, question)
    post = await one.post()
    out = await one.tweak(post, "make the text orange")
    assert out["question"] == "Which text should be orange?"
    assert [choice["label"] for choice in out["choices"]] == [
        "Make the headline orange",
        "Make the offer line orange",
    ]
    assert len(await one.versions(post)) == 1


# --- restyle ---------------------------------------------------------------------------


async def test_restyle_switches_the_design_and_keeps_the_words(tmp_path: Path) -> None:
    words = {"ops": [{"op": "set_text", "layer": "headline", "text": "Boilers fixed fast"}]}
    dark = {"ops": [{"op": "restyle", "changes": {"fill": "dark"}}]}
    one = await studio(tmp_path, words, dark)
    post = await one.post()
    await one.tweak(post, "headline: Boilers fixed fast")
    out = await one.tweak(post, "make the panel dark")

    assert out["outcome"] == "applied"
    assert out["note"].startswith("Switched to Photo and panel (")
    now = await one.fresh(post)
    assert decode(now["template_id"]).choice == {**decode(PANEL).choice, "fill": "dark"}
    spec = await one.latest_spec(post)
    assert spec.template_id == now["template_id"]
    panel = spec.layer("panel")
    assert isinstance(panel, ShapeLayer) and panel.fill == "palette.text"
    headline = spec.layer("headline")
    assert isinstance(headline, TextLayer) and headline.content == "Boilers fixed fast"
    [decision] = await one.ctx.supa.select(
        "decision_log", post_id=f"eq.{post['id']}", kind="eq.edit_request"
    )
    assert decision["detail"]["what"] == "restyle"
    assert decision["detail"]["from"] == PANEL and decision["detail"]["to"] == now["template_id"]


async def test_restyle_to_two_photos_brings_in_the_next_photo(tmp_path: Path) -> None:
    pair = {"ops": [{"op": "restyle", "changes": {"family": "pair"}}]}
    one = await studio(tmp_path, pair)
    post = await one.post()
    out = await one.tweak(post, "show two photos")

    assert out["outcome"] == "applied", out
    assert "Added another of" in out["note"]
    spec = await one.latest_spec(post)
    first, second = spec.layer("photo_a"), spec.layer("photo_b")
    assert isinstance(first, ImageLayer) and isinstance(second, ImageLayer)
    assert first.asset and second.asset and first.asset != second.asset
    assert decode((await one.fresh(post))["template_id"]).family.name == "pair"


async def test_two_photos_with_one_photo_says_what_to_do(tmp_path: Path) -> None:
    pair = {"ops": [{"op": "restyle", "changes": {"family": "pair"}}]}
    one = await studio(tmp_path, pair, photos=0)
    post = await one.post()
    out = await one.tweak(post, "show two photos")
    assert out["outcome"] == "needs_human"
    assert out["why"] == (
        f'The "Two photos" design needs 2 of {CLIENT}\'s own photos and there is 1. '
        "Upload another, then ask again."
    )
    assert "NOT available" in asked(one.provider).design


async def test_a_setting_that_does_not_exist_is_a_sentence(tmp_path: Path) -> None:
    gold = {"ops": [{"op": "restyle", "changes": {"fill": "gold"}}]}
    one = await studio(tmp_path, gold)
    post = await one.post()
    out = await one.tweak(post, "gold panel")
    assert out == {"outcome": "needs_human", "why": "fill can be primary, surface, dark; not gold."}


# --- undo ------------------------------------------------------------------------------


async def test_undo_needs_no_model_and_brings_back_the_version_before(tmp_path: Path) -> None:
    words = {"ops": [{"op": "set_text", "layer": "headline", "text": "Boilers fixed fast"}]}
    one = await studio(tmp_path, words)
    post = await one.post()
    await one.tweak(post, "headline: Boilers fixed fast")
    calls = len(one.provider.calls)

    out = await one.tweak(post, "Undo that.")

    assert out["outcome"] == "applied" and out["version"] == 3
    assert len(one.provider.calls) == calls  # no model
    first, _, third = await one.versions(post)
    assert load_spec(third["spec"]) == load_spec(first["spec"])
    assert third["note"] == "undo: back to version 1"


async def test_undo_after_a_restyle_brings_the_design_back(tmp_path: Path) -> None:
    dark = {"ops": [{"op": "restyle", "changes": {"fill": "dark"}}]}
    one = await studio(tmp_path, dark)
    post = await one.post()
    await one.tweak(post, "dark panel")
    assert (await one.fresh(post))["template_id"] != PANEL
    await one.tweak(post, "undo")
    assert (await one.fresh(post))["template_id"] == PANEL


async def test_undo_on_a_first_draft_says_there_is_nothing_to_undo(tmp_path: Path) -> None:
    one = await studio(tmp_path)
    post = await one.post()
    out = await one.tweak(post, "undo")
    assert out == {
        "outcome": "needs_human",
        "why": "There is nothing to undo yet: this is the first version.",
    }


# --- the photo inside its frame ------------------------------------------------------------


async def test_the_photo_can_be_panned_zoomed_and_darkened(tmp_path: Path) -> None:
    move = {
        "ops": [
            {"op": "zoom", "layer": "bg", "factor": 1.25},
            {"op": "pan", "layer": "bg", "dx": 0, "dy": 0.1},
            {"op": "dim", "layer": "bg", "amount": 0.3},
        ]
    }
    one = await studio(tmp_path, move)
    post = await one.post()
    before = (await one.latest_spec(post)).layer("bg")
    out = await one.tweak(post, "zoom in, show more of the top and darken it")
    assert out["outcome"] == "applied"
    after = (await one.latest_spec(post)).layer("bg")
    assert isinstance(before, ImageLayer) and isinstance(after, ImageLayer)
    assert after.crop is not None and after.crop.zoom == 1.25 * (
        before.crop.zoom if before.crop else 1
    )
    assert after.dim == 0.3


# --- what the designer reads -----------------------------------------------------------------


async def test_an_op_on_the_wrong_kind_of_layer_is_said_plainly(tmp_path: Path) -> None:
    wrong = {"ops": [{"op": "set_text", "layer": "logo", "text": "Kowalski"}]}
    one = await studio(tmp_path, wrong)
    post = await one.post()
    out = await one.tweak(post, "change the logo text")
    assert out == {"outcome": "failed", "why": "The logo has no words to change."}


async def test_an_op_the_editor_does_not_know_is_a_sentence(tmp_path: Path) -> None:
    unknown = {"ops": [{"op": "set_font", "layer": "headline", "font": "Comic"}]}
    one = await studio(tmp_path, unknown)
    post = await one.post()
    out = await one.tweak(post, "comic sans please")
    assert out["outcome"] == "failed"
    assert out["why"] == "I could not turn that into a change. Try saying it another way."


async def test_an_inferred_change_says_what_it_did(tmp_path: Path) -> None:
    pop = {
        "ops": [{"op": "scale", "layer": "headline", "factor": 1.1}],
        "note": "Made the headline a little bigger so it stands out.",
    }
    one = await studio(tmp_path, pop)
    post = await one.post()
    out = await one.tweak(post, "make it pop")
    assert out["outcome"] == "applied"
    assert out["note"] == "Made the headline a little bigger so it stands out."
    assert out["why"] == out["note"] and out["unable"] == ""
