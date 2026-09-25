"""Preflight: what gets checked after the model has spoken."""

from __future__ import annotations

import pytest
from stratum_spec.client import ClientRules
from stratum_worker.draft.models import PostCopy
from stratum_worker.draft.preflight import check_copy, first_sentence

RULES = ClientRules(
    client_id="kowalski-plumbing",
    services=("boiler service",),
    areas=("Manchester",),
    allowed_hashtags=("#manchester", "#plumbing"),
    max_hashtags=2,
    must_include=("Gas Safe 000000",),
    banned_claims=("cheapest in Manchester",),
)

BRIEF = "20% off boiler servicing in Manchester. GBP 12.50 callout. Gas Safe 000000."


def _copy(**overrides: object) -> PostCopy:
    base = {
        "headline": "20% off boiler servicing",
        "offer_detail": "GBP 12.50 callout. Gas Safe 000000",
        "cta": "Book today",
        "caption": "Boiler service in Manchester, 20% off this month.",
        "hashtags": ("#manchester",),
        "alt_text": "An engineer beside a wall-mounted boiler",
    }
    return PostCopy.model_validate({**base, **overrides})


def _codes(copy: PostCopy) -> set[str]:
    return {flag.code for flag in check_copy(copy, RULES, BRIEF)}


def test_a_clean_post_raises_nothing() -> None:
    assert _codes(_copy()) == set()


@pytest.mark.parametrize("written", ["£12.50", "GBP 12.50", "£12.50", "gbp12.50"])
def test_the_same_money_written_differently_is_not_a_lie(written: str) -> None:
    """The brief says GBP 12.50; a poster saying £12.50 is correct, not invented.

    Flagging these would make the check noise, and noise gets ignored - which is how
    the one real mispricing scrolls past with the rest.
    """
    assert "invented_number" not in _codes(
        _copy(offer_detail=f"{written} callout. Gas Safe 000000")
    )


def test_a_changed_price_is_a_blocker() -> None:
    """The failure this exists for: a perfect-looking poster that is a lie."""
    flags = check_copy(_copy(headline="25% off boiler servicing"), RULES, BRIEF)
    invented = [f for f in flags if f.code == "invented_number"]
    assert invented and invented[0].severity == "blocker"
    assert "25%" in invented[0].message


def test_an_invented_phone_number_is_a_blocker() -> None:
    assert "invented_number" in _codes(_copy(cta="Call 0161 999 1234"))


def test_a_banned_claim_is_a_blocker() -> None:
    copy = _copy(caption="Boiler service in Manchester - cheapest in Manchester, promise.")
    flags = [f for f in check_copy(copy, RULES, BRIEF) if f.code == "banned_claim"]
    assert flags and flags[0].severity == "blocker"


def test_a_missing_required_phrase_is_a_blocker() -> None:
    assert "missing_required_phrase" in _codes(_copy(offer_detail="GBP 12.50 callout"))


def test_search_terms_must_be_in_the_first_sentence_not_just_somewhere() -> None:
    """Instagram weights the caption's opening; terms buried in line four do nothing."""
    late = _copy(caption="Great news this month! We are offering a boiler service in Manchester.")
    assert {"no_service_in_first_sentence", "no_area_in_first_sentence"} <= _codes(late)


def test_hashtags_outside_the_agreed_set_are_flagged() -> None:
    assert "hashtag_not_allowed" in _codes(_copy(hashtags=("#manchester", "#viral")))


def test_too_many_hashtags_is_flagged() -> None:
    assert "too_many_hashtags" in _codes(
        _copy(hashtags=("#manchester", "#plumbing", "#manchester"))
    )


def test_missing_alt_text_is_flagged_not_invented() -> None:
    assert "no_alt_text" in _codes(_copy(alt_text=""))


def test_first_sentence_stops_at_the_first_full_stop() -> None:
    assert first_sentence("Boiler service in Manchester. And more.") == (
        "Boiler service in Manchester."
    )
