"""A smarter brief box (draft/brief.py): what a brief is missing, while it is typed."""

from __future__ import annotations

from stratum_worker.draft.brief import MAX_GAPS, gaps

SERVICES = ("house cleaning", "deep clean", "move-out cleaning")
AREAS = ("Tampa", "South Tampa", "Brandon")


def codes(
    brief: str, services: tuple[str, ...] = SERVICES, areas: tuple[str, ...] = AREAS
) -> list[str]:
    return [one.code for one in gaps(brief, services=services, areas=areas)]


def test_the_live_briefs_get_the_questions_that_would_have_helped() -> None:
    assert codes("first time booking is free", (), ()) == ["no_end", "no_profile"]
    vague = "normal advertisement about the brand and that they do their job really well"
    assert codes(vague, (), ()) == ["no_detail", "no_profile"]


def test_a_complete_brief_has_nothing_to_ask() -> None:
    assert codes("Deep clean 20% off until Friday in Brandon") == []
    assert codes("Weekly house cleaning from $99, same cleaner every time") == []


def test_an_offer_asks_for_an_end_date_and_a_place() -> None:
    assert codes("Deep clean 20% off") == ["no_end", "no_area"]
    assert codes("Deep clean 20% off this weekend") == ["no_area"]


def test_a_short_brief_asks_for_more_and_never_more_than_three_things() -> None:
    found = gaps("hi", services=SERVICES, areas=AREAS)
    assert found[0].code == "too_short"
    assert len(found) <= MAX_GAPS


def test_questions_name_the_clients_own_services_and_areas() -> None:
    assert "no_service" not in codes("move-out cleaning, get your deposit back")
    assert "no_service" in codes("great value cleaning")  # which of the three?
    found = gaps("we are hiring, good pay", services=SERVICES, areas=AREAS)
    asked = next(one for one in found if one.code == "no_service")
    assert "house cleaning" in asked.question and asked.example == "house cleaning"
    assert gaps("", services=SERVICES, areas=AREAS)[0].as_dict()["code"] == "too_short"
