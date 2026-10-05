"""The chat eval (M10h): fixed posters, scored in code, the product's own functions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from stratum_worker.edit import chateval
from stratum_worker.edit.chateval import Answer, Case, answer_of, matches, score
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.prompts import load
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.providers.registry import offline_router
from stratum_worker.gateway.tasks import EditParseRequest, EditParseResult, TaskName


def gateway(results: list[dict[str, Any]]) -> tuple[Gateway, FakeProvider]:
    router = offline_router(results)
    provider = router.providers["fake"]
    assert isinstance(provider, FakeProvider)
    return Gateway(router=router, budget=Budget(daily_cap_usd=5.0)), provider


def case(expect: dict[str, Any], **more: Any) -> Case:
    return Case(id="x", poster="panel", message="m", expect=expect, **more)


def test_ops_match_on_kind_layer_and_direction() -> None:
    bigger = {"op": "scale", "layer": "logo", "factor": ">1"}
    assert matches(bigger, {"op": "scale", "layer": "logo", "factor": 1.2})
    assert not matches(bigger, {"op": "scale", "layer": "logo", "factor": 0.8})
    assert not matches(bigger, {"op": "scale", "layer": "headline", "factor": 1.2})
    assert matches(
        {"op": "move", "layer": ["cta", "logo"], "dx": "<0"},
        {"op": "move", "layer": "cta", "dx": -0.1},
    )
    assert matches(
        {"op": "set_text", "text_has": ["0161"]}, {"op": "set_text", "text": "Call 0161 1"}
    )
    restyle = {"op": "restyle", "changes": {"family": "pair"}}
    assert matches(restyle, {"op": "restyle", "changes": {"family": "pair", "align": "left"}})
    either = {"any": [bigger, restyle]}
    assert matches(either, {"op": "restyle", "changes": {"family": "pair"}})


def test_a_case_passes_on_outcome_ops_and_touching_nothing_else() -> None:
    want = {
        "outcome": "change",
        "ops": [{"op": "scale", "layer": "logo", "factor": ">1"}],
        "only": True,
    }
    good = Answer("change", [{"op": "scale", "layer": "logo", "factor": 1.2}], True)
    assert score(case(want), good)["passed"]
    extra = Answer("change", [*good.ops, {"op": "dim", "layer": "bg", "amount": 0.2}], True)
    assert score(case(want), extra)["extra"] == ["bg"]
    assert not score(case(want), Answer("ask", [], True))["passed"]
    assert score(case({"outcome": "change_or_ask"}), Answer("ask", [], True))["passed"]
    assert score(case({"outcome": ["refuse", "ask"]}), Answer("refuse", [], True))["passed"]


def test_answers_are_read_by_the_products_rules() -> None:
    ask = {
        "op": "ask",
        "question": "Which?",
        "choices": [
            {"label": "Make it gold", "kind": "tweak"},
            {"label": "Make it navy", "kind": "tweak"},
        ],
    }
    assert answer_of(EditParseResult(ops=[ask])).outcome == "ask"
    assert answer_of(EditParseResult(ops=[], needs_human="No.")).outcome == "refuse"
    assert answer_of(EditParseResult(ops=[{"op": "scale", "layer": "logo"}])).outcome == "invalid"
    restyle = {"op": "restyle", "changes": {"align": "center"}}
    assert answer_of(EditParseResult(ops=[restyle])).outcome == "change"


def test_every_case_names_parts_its_poster_has() -> None:
    cases = chateval.load_cases()
    assert len(cases) >= 75 and len({one.id for one in cases}) == len(cases)
    for one in cases:
        ids = {layer.id for layer in chateval.poster(one.poster).spec.layers}
        assert one.pointed is None or one.pointed in ids, one.id
        for want in one.expect.get("ops", []):
            for alt in want.get("any", [want]):
                layer = alt.get("layer")
                named = layer if isinstance(layer, list) else [layer] if layer else []
                assert set(named) <= ids, one.id


def test_a_pointed_short_message_is_answered_without_the_model() -> None:
    cases = [one for one in chateval.load_cases() if one.id in ("pt-bigger", "pt-orange")]
    gate, provider = gateway(
        [{"ops": [{"op": "set_color", "layer": "offer", "color": "palette.secondary"}]}]
    )
    report = asyncio.run(chateval.run(gate, cases))
    assert [row["score"]["passed"] for row in report.rows] == [True, True]
    assert [row["score"]["model"] for row in report.rows] == [False, True]
    [(task, request)] = provider.calls
    assert task is TaskName.EDIT_PARSE and isinstance(request, EditParseRequest)
    assert request.pointed.startswith("offer: ")
    assert report.summary()["without_a_model"] == 1
    json.dumps(report.as_dict())


def test_the_baseline_runs_prompt_v5_with_no_pointing_then_restores_v6() -> None:
    cases = [one for one in chateval.load_cases() if one.id == "pt-bigger"]
    gate, provider = gateway([{"ops": [], "needs_human": "Which part?"}])
    report = asyncio.run(chateval.run(gate, cases, baseline=True))
    assert report.version == 5 and not report.pointing
    [(_, request)] = provider.calls
    assert isinstance(request, EditParseRequest) and request.pointed == ""
    assert report.rows[0]["score"]["outcome"] == "refuse" and not report.rows[0]["score"]["passed"]
    assert load(TaskName.EDIT_PARSE).version == 6
    assert "edit.parse v5 (pointing off)" in chateval.table(report)


def test_a_case_that_fails_is_scored_and_the_run_goes_on() -> None:
    wanted = ("logo-bigger", "live-logo-bigger")  # both want a bigger logo
    cases = [one for one in chateval.load_cases() if one.id in wanted]
    broken = {"ops": "not a list"}  # invalid twice: the gateway retries once
    logo = {"ops": [{"op": "scale", "layer": "logo", "factor": 1.2}]}
    gate, _ = gateway([broken, broken, logo])
    report = asyncio.run(chateval.run(gate, cases))
    assert report.rows[0]["score"]["outcome"] == "failed"
    assert report.rows[1]["score"]["passed"]


def test_a_logo_recolour_is_refused_and_an_op_that_cannot_apply_is_invalid() -> None:
    spec = chateval.poster("panel").spec
    logo = {"op": "set_color", "layer": "logo", "color": "palette.secondary"}
    refused = answer_of(EditParseResult(ops=[logo]), spec)
    assert refused.outcome == "refuse" and "never recoloured" in refused.note
    both = answer_of(EditParseResult(ops=[logo, {**logo, "layer": "headline"}]), spec)
    assert both.outcome == "change" and [one["layer"] for one in both.ops] == ["headline"]
    ghost = {"op": "scale", "layer": "nope", "factor": 1.2}
    assert answer_of(EditParseResult(ops=[ghost]), spec).outcome == "invalid"


def test_a_setting_sent_as_a_number_is_read_as_its_text() -> None:
    """Chat eval, 6 Oct: "make it pop" came back as share 0.5 (a number) and the
    whole answer was thrown away."""
    restyle = {"op": "restyle", "changes": {"share": 0.5, "scale": "calm"}}
    assert answer_of(EditParseResult(ops=[restyle])).outcome == "change"
    from stratum_worker.edit.actions import Restyle

    assert Restyle.model_validate(restyle).changes == {"share": "0.5", "scale": "calm"}
