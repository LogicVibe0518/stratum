"""Each line of a post earns its space (draft/copycheck.py, post.copy v3).

The examples are the live words of 5 Oct, from posts Rushil made."""

from __future__ import annotations

from stratum_worker.draft.copycheck import (
    ECHO_MAX,
    content,
    copied_run,
    cta_verb,
    echo,
    feedback,
    problems,
    stem,
)


def test_the_live_offer_that_said_the_headline_again_is_an_echo() -> None:
    assert echo("First time booking is free", "Your first time booking is free.") == 1.0
    eight = "We\u2019ve been doing roofing for the past 8 years and are experienced."
    assert echo("8 years of roofing experience", eight) > ECHO_MAX


def test_an_offer_that_adds_something_is_not() -> None:
    assert echo("Boiler service, sorted", "20% off every service booked this month") < 0.3
    assert echo("Your first clean is free", "House cleaning across Tampa and Brandon") < 0.3
    assert echo("Anything", "") == 0.0


def test_words_match_across_their_forms() -> None:
    assert stem("booking") == stem("booked") == stem("book")
    assert stem("years") == stem("year")
    assert stem("experienced") == stem("experience")
    assert content("We've been doing it") == set()  # only little words


def test_copied_runs_count_words_in_a_row_from_the_brief() -> None:
    brief = "normal advertisement about the brand and that they do their job really well"
    assert copied_run(brief, "We take pride in doing our job really well.") == 3
    assert copied_run("first time booking is free", "Your first time booking is free.") == 5
    assert copied_run("boiler service", "Nothing alike here") == 0


def test_a_call_to_action_starts_with_a_verb() -> None:
    assert cta_verb("Book your free clean") and cta_verb("Get in touch")
    assert cta_verb("Call 0161 496 0000")
    assert not cta_verb("Your free quote") and not cta_verb("")


def test_problems_are_found_and_only_an_echo_is_sent_back() -> None:
    found = problems(
        "first time booking is free",
        "First time booking is free",
        "Your first time booking is free.",
        "Free quote",
    )
    assert [one.code for one in found] == ["echo", "copied", "copied", "cta_verb"]
    told = feedback(found, "First time booking is free", "Your first time booking is free.")
    assert "same as your headline" in told and "service and an area" in told
    copied_only = problems("20% off boilers", "20% off boilers this week", "Book a service", "Call")
    assert feedback(copied_only, "20% off boilers this week", "Book a service") == ""
