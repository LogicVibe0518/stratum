"""Which designs go side by side on a Taste Lab screen (design/lab.py)."""

from __future__ import annotations

import random
from collections import Counter

from stratum_worker.design.choose import Candidate
from stratum_worker.design.lab import TOP, lab_screen

FAMILIES = ("panel", "overlay", "pair", "type")


def ranked(count: int, families: tuple[str, ...] = FAMILIES) -> list[Candidate]:
    return [
        Candidate(
            design_id=f"ds.{families[i % len(families)]}.1.{i:07d}",
            family=families[i % len(families)],
            spec=None,  # type: ignore[arg-type]
            score=float(count - i),
            parts={},
        )
        for i in range(count)
    ]


def test_a_screen_has_four_different_designs() -> None:
    screen = lab_screen(ranked(100), random.Random(1))
    assert screen is not None and len(screen) == 4
    assert len({one.candidate.design_id for one in screen}) == 4


def test_two_come_from_the_top_and_of_different_families() -> None:
    for seed in range(30):
        screen = lab_screen(ranked(100), random.Random(seed))
        assert screen is not None
        top = [one for one in screen if one.bucket == "top"]
        assert len(top) == 2 and all(one.rank < TOP for one in top)
        assert top[0].candidate.family != top[1].candidate.family


def test_one_from_the_middle_and_one_from_anywhere() -> None:
    screen = lab_screen(ranked(100), random.Random(3))
    assert screen is not None
    buckets = Counter(one.bucket for one in screen)
    assert buckets == {"top": 2, "middle": 1, "any": 1}
    middle = next(one for one in screen if one.bucket == "middle")
    assert TOP <= middle.rank < 50


def test_the_order_on_screen_is_shuffled() -> None:
    firsts = {
        lab_screen(ranked(100), random.Random(seed))[0].bucket  # type: ignore[index]
        for seed in range(40)
    }
    assert len(firsts) >= 3


def test_the_same_seed_gives_the_same_screen() -> None:
    one = lab_screen(ranked(100), random.Random(7))
    two = lab_screen(ranked(100), random.Random(7))
    assert one is not None and two is not None
    assert [d.candidate.design_id for d in one] == [d.candidate.design_id for d in two]


def test_a_short_ranking_still_makes_a_screen_and_one_design_does_not() -> None:
    small = lab_screen(ranked(3), random.Random(0))
    assert small is not None and len(small) == 3
    assert lab_screen(ranked(1), random.Random(0)) is None


def test_one_family_only_still_works() -> None:
    screen = lab_screen(ranked(40, ("type",)), random.Random(0))
    assert screen is not None and len(screen) == 4
