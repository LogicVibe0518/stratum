"""post.copy v3 (M7b): an offer line that repeats the headline is sent back once;
questions for the business come back with the words; the eval runs the same code."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from stratum_spec import load_template
from stratum_worker.app.service import ask_for_copy, repeated_lines
from stratum_worker.draft import copyeval
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.providers.registry import offline_router
from stratum_worker.gateway.tasks import PostCopyRequest, PostCopyResult, TaskName

REPO = Path(__file__).resolve().parents[3]

ECHO = {
    "headline": "First time booking is free",
    "offer_detail": "Your first time booking is free.",
    "cta": "Book your first time",
    "caption": "Your first time booking with Sparkle Squad Cleaning is free.",
}
GOOD = {
    "headline": "Your first clean is free",
    "offer_detail": "House cleaning across Tampa and Brandon",
    "cta": "Book your free clean",
    "caption": "House cleaning in Tampa: your first clean with us is free.",
    "missing": ["Is this for new customers only?", "Until when?", "Which areas?", "Price?"],
}
REQUEST = PostCopyRequest(
    brief="first time booking is free",
    client_name="Sparkle Squad Cleaning",
    services=("house cleaning",),
    areas=("Tampa", "Brandon"),
)


def gateway(results: list[dict[str, Any]]) -> tuple[Gateway, FakeProvider]:
    router = offline_router(results)
    provider = router.providers["fake"]
    assert isinstance(provider, FakeProvider)
    return Gateway(router=router, budget=Budget(daily_cap_usd=1.0)), provider


def ask(results: list[dict[str, Any]]) -> tuple[PostCopyResult, int, FakeProvider]:
    gate, provider = gateway(results)
    result, calls = asyncio.run(ask_for_copy(gate, REQUEST, agency_id="a"))
    return result, calls, provider


def test_an_offer_that_repeats_the_headline_is_sent_back_once_with_the_reason() -> None:
    result, calls, provider = ask([ECHO, GOOD])
    assert calls == 2 and result.offer_detail == GOOD["offer_detail"]
    first, second = (request for _, request in provider.calls)
    assert isinstance(first, PostCopyRequest) and isinstance(second, PostCopyRequest)
    assert first.feedback == "" and "same as your headline" in second.feedback
    assert "Your first time booking is free." in second.feedback


def test_good_words_take_one_call() -> None:
    result, calls, _ = ask([GOOD])
    assert calls == 1 and result.headline == GOOD["headline"]


def test_if_the_second_try_is_no_better_the_first_is_kept() -> None:
    worse = {**ECHO, "offer_detail": "First time booking is free!"}
    result, calls, _ = ask([ECHO, worse])
    assert calls == 2 and result.offer_detail == ECHO["offer_detail"]


def test_the_prompt_tells_the_second_try_what_was_wrong() -> None:
    first = render(TaskName.POST_COPY, REQUEST)
    assert "this is the first draft" in first.user
    again = render(TaskName.POST_COPY, REQUEST.model_copy(update={"feedback": "Too samey."}))
    assert "sent back: Too samey." in again.user
    assert "Every line on the image does a different job" in first.system
    assert "missing" in first.system


def test_questions_come_back_lenient_and_are_cut_to_three() -> None:
    result = PostCopyResult.model_validate(GOOD)
    assert len(result.missing) == 4  # a fourth question is not a failed call
    from stratum_worker.draft.models import PostCopy

    words = PostCopy(**{**GOOD, "missing": tuple(result.missing)[:3]})
    assert len(words.missing) == 3


def test_a_poster_whose_offer_line_repeats_its_headline_is_flagged(offer: Any) -> None:
    def with_words(headline: str, offer_line: str) -> Any:
        layers = [
            one.model_copy(update={"content": {"headline": headline, "offer": offer_line}[one.id]})
            if one.id in ("headline", "offer")
            else one
            for one in offer.layers
        ]
        return offer.model_copy(update={"layers": layers})

    repeated = repeated_lines(with_words(ECHO["headline"], ECHO["offer_detail"]))
    assert [flag.code for flag in repeated] == ["copy_repeats"]
    assert repeated_lines(with_words(GOOD["headline"], GOOD["offer_detail"])) == []


# --- the eval -------------------------------------------------------------------------------


def test_every_eval_case_is_a_real_fictional_client() -> None:
    cases = copyeval.load_cases()
    assert len(cases) == 30 and len({case.id for case in cases}) == 30
    for case in cases:
        request, rules = copyeval.request_for(case)
        assert request.client_name
        if case.profile == "empty":
            assert not rules.services and not rules.areas
        else:
            assert rules.services and rules.areas


def test_the_eval_scores_what_the_product_writes() -> None:
    cases = [case for case in copyeval.load_cases() if case.id.startswith("live-")]
    gate, _ = gateway([ECHO, GOOD, GOOD, GOOD])
    report = asyncio.run(copyeval.run(gate, cases))
    assert [row["id"] for row in report.rows] == [case.id for case in cases]
    first = report.rows[0]["score"]
    assert first["calls"] == 2 and not first["echo"] and first["missing"] == 3
    summary = report.summary()
    assert summary["cases"] == 3 and summary["second_tries"] == 1
    assert set(summary["rates"]) == set(copyeval.PROBLEMS)
    assert "post.copy v4" in copyeval.table(report)
    json.dumps(report.as_dict())  # the report is plain JSON


def test_a_case_that_fails_is_recorded_and_the_run_goes_on() -> None:
    cases = copyeval.load_cases()[:2]
    gate, _ = gateway([{"headline": "only a headline"}, GOOD])
    report = asyncio.run(copyeval.run(gate, cases))
    assert len(report.failed) == 1 and len(report.rows) == 1


def test_the_template_fixture_has_the_lines_the_flag_reads() -> None:
    template = load_template(REPO / "templates" / "offer_4x5.json")
    assert {"headline", "offer"} <= {one.id for one in template.layers}


def test_the_baseline_runs_the_old_prompt_once_and_then_restores_the_new() -> None:
    from stratum_worker.gateway.prompts import load

    cases = [case for case in copyeval.load_cases() if case.id.startswith("live-")][:1]
    gate, provider = gateway([ECHO, GOOD])
    report = asyncio.run(copyeval.run(gate, cases, baseline=True))
    assert report.version == 2 and report.rows[0]["score"]["calls"] == 1
    assert report.rows[0]["score"]["echo"]  # the old product kept the echo
    assert len(provider.calls) == 1
    assert load(TaskName.POST_COPY).version == 4
    assert "different job" in load(TaskName.POST_COPY).system


def test_a_banned_phrase_from_the_brief_is_sent_back_too() -> None:
    """5 Oct eval: Lone Star bans "free roof"; the brief said "Free roof inspection" and
    v3 wrote it. The second try is told, and kept because it fixed it."""
    request = REQUEST.model_copy(
        update={"brief": "Free roof inspection this week", "banned_claims": ("free roof",)}
    )
    said = {
        "headline": "Hail in Stone Oak?",
        "offer_detail": "Free roof inspection this week for storm damage.",
        "cta": "Book a free roof inspection",
        "caption": "Hail hit Stone Oak.",
    }
    fixed = {
        **said,
        "offer_detail": "Roof inspections this week for storm damage.",
        "cta": "Book a roof inspection",
    }
    gate, provider = gateway([said, fixed])
    result, calls = asyncio.run(ask_for_copy(gate, request, agency_id="a"))
    assert calls == 2 and "free roof" not in result.offer_detail.lower()
    second = provider.calls[1][1]
    assert isinstance(second, PostCopyRequest) and "must never say (free roof)" in second.feedback
