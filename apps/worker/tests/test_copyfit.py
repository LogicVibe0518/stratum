"""Copy that fits (M10e): each design sets the headline version that suits it best."""

from __future__ import annotations

import json
from pathlib import Path

from stratum_spec import BrandKit
from stratum_worker.design.copyfit import Fitted, best_copy, pick, versions
from stratum_worker.design.space import lay_out
from stratum_worker.design.typeset import Copy

REPO = Path(__file__).resolve().parents[3]
KIT = BrandKit.model_validate(
    json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text())
)
WORDS = {
    "headline": "Boiler service, sorted before winter",
    "headline_short": "Boilers sorted",
    "headline_long": "Boiler service sorted before winter, with 20% off every booking this month",
    "offer_detail": "20% off every boiler service booked before 31 October.",
    "cta": "Call 0161 496 0000",
}


def fitted(headline: str, size: float, lines: int, main: bool = False) -> Fitted:
    return Fitted(Copy(headline, "", ""), None, size, lines, main)  # type: ignore[arg-type]


def test_versions_come_main_first_each_once() -> None:
    found = versions({**WORDS, "headline_short": WORDS["headline"]})
    assert [(copy.headline, main) for copy, main in found] == [
        (WORDS["headline"], True),
        (WORDS["headline_long"], False),
    ]
    assert all(copy.offer == WORDS["offer_detail"] for copy, _ in found)


def test_copy_with_one_headline_has_one_version() -> None:
    assert len(versions({"headline": "Boilers sorted", "headline_short": ""})) == 1


def test_the_main_headline_is_kept_unless_another_is_clearly_better() -> None:
    main = fitted("Boiler service, sorted before winter", 86, 2, main=True)
    short = fitted("Boilers sorted", 117, 1)  # bigger, but the main one is not cramped
    long = fitted("Boiler service sorted before winter, with 20% off", 59, 3)
    assert pick([main, short, long]) is main


def test_a_cramped_main_headline_gives_way_to_a_much_bigger_short_one() -> None:
    main = fitted("Boiler service, sorted before winter", 80, 3, main=True)
    assert pick([main, fitted("Boilers sorted", 101, 1)]).copy.headline == "Boilers sorted"
    assert pick([main, fitted("Boilers sorted", 95, 1)]) is main  # not a quarter bigger


def test_a_fuller_headline_at_the_same_impact_wins() -> None:
    main = fitted("Boiler service, sorted before winter", 59, 1, main=True)
    long = fitted("Boiler service sorted before winter, with 20% off", 76, 3)
    assert pick([main, long]) is long
    assert pick([main, fitted(long.copy.headline, 50, 3)]) is main  # too much smaller


def test_when_the_main_headline_does_not_fit_the_biggest_that_does_is_used() -> None:
    assert pick([fitted("short", 90, 1), fitted("longer one", 70, 2)]).size_px == 90


def test_on_real_designs_the_choice_follows_the_room() -> None:
    def chosen(design: str) -> str:
        found = best_copy(design, versions(WORDS), KIT, REPO, logo_aspect=2.0)
        assert found is not None
        return found[0].headline

    assert chosen("ds.panel.1.0010110") == WORDS["headline"]
    assert chosen("ds.overlay.1.000000") == WORDS["headline"]
    assert chosen("ds.pair.1.0000000") == WORDS["headline_long"]


def test_one_version_is_laid_out_once_exactly_as_before() -> None:
    copy = Copy(WORDS["headline"], WORDS["offer_detail"], WORDS["cta"])
    found = best_copy("ds.panel.1.0010110", [(copy, True)], KIT, REPO, logo_aspect=2.0)
    assert found is not None
    assert found[1] == lay_out("ds.panel.1.0010110", KIT, REPO, copy=copy, logo_aspect=2.0)


def test_nothing_fits_is_none() -> None:
    huge = Copy("word " * 60, "offer " * 60, "call")
    assert best_copy("ds.overlay.1.000000", [(huge, True)], KIT, REPO) is None
