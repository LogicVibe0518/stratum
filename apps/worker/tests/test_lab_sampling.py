"""Which designs go side by side on a Taste Lab screen (design/lab.py)."""

from __future__ import annotations

import random
from collections import Counter

from stratum_worker.design.choose import Candidate
from stratum_worker.design.lab import MIDDLE_END, TOP, lab_screen

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
    assert TOP <= middle.rank < round(MIDDLE_END * 100)


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


# --- designs a person can tell apart (chooser 2, 5 Oct) ------------------------------------

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from stratum_worker.app.lab import photo_sets  # noqa: E402
from stratum_worker.design.lab import (  # noqa: E402
    FAMILY_TOP,
    MIN_APART,
    Shown,
    apart,
    cielab,
    closest,
)


def flat(grey: float, shape: tuple[int, int] = (12, 10)) -> np.ndarray:
    return cielab(np.full((*shape, 3), grey))


def twins(count: int) -> list[np.ndarray]:
    """Designs that come in look-alike pairs: 0 and 1 look the same, 2 and 3, ..."""
    return [flat(0.1 + 0.8 * (i // 2) / max(1, count // 2)) for i in range(count)]


def test_apart_is_zero_for_the_same_look_and_100_for_black_against_white() -> None:
    assert apart(flat(0.5), flat(0.5)) == 0.0
    assert apart(flat(0.0), flat(1.0)) == pytest.approx(100.0, abs=0.01)
    assert apart(flat(0.2), flat(0.7)) == pytest.approx(apart(flat(0.7), flat(0.2)))
    assert apart(flat(0.5), flat(0.5, (10, 10))) == float("inf")  # different sizes


def test_white_is_lightness_100_with_no_colour() -> None:
    lab = cielab(np.ones((1, 1, 3)))[0, 0]
    assert lab == pytest.approx([100.0, 0.0, 0.0], abs=0.01)


def test_look_alikes_never_share_a_screen_when_anything_else_is_left() -> None:
    designs = ranked(40)
    looks = twins(40)
    for seed in range(40):
        screen = lab_screen(designs, random.Random(seed), looks=looks)
        assert screen is not None and len(screen) == 4
        assert closest(screen, looks) >= MIN_APART


def test_when_everything_looks_alike_the_screen_still_has_the_least_alike() -> None:
    designs = ranked(10)
    looks = [flat(0.5 + 0.001 * i) for i in range(10)]
    screen = lab_screen(designs, random.Random(0), looks=looks)
    assert screen is not None and len(screen) == 4
    assert closest(screen, looks) < MIN_APART  # nothing better existed


def test_a_family_the_critic_ranks_low_still_reaches_the_top() -> None:
    """The ranking's first twelve are all panels (the 5 Oct batch); each other family's
    best two count as the top too, so the second top pick is another family."""
    designs = ranked(12, ("panel",)) + ranked(20, ("cover", "type"))
    for seed in range(20):
        screen = lab_screen(designs, random.Random(seed))
        assert screen is not None
        top = [one for one in screen if one.bucket == "top"]
        assert len({one.candidate.family for one in top}) == 2


def test_a_posts_screens_rotate_through_the_families_and_use_new_designs() -> None:
    designs = ranked(12, ("panel",)) + ranked(30, ("cover", "type", "overlay", "pair"))
    shown = Shown.nothing()
    tops: list[str] = []
    for index in range(6):
        screen = lab_screen(designs, random.Random(index), looks=twins(42), shown=shown)
        assert screen is not None
        tops += [one.candidate.family for one in screen if one.bucket == "top"]
    assert set(tops) == {"panel", "cover", "type", "overlay", "pair"}
    assert Counter(tops)["panel"] <= 4  # the ranking's favourite: before, all twelve
    assert sum(shown.designs.values()) == 24 and max(shown.designs.values()) <= 2


def test_each_familys_best_two_join_the_top() -> None:
    designs = ranked(12, ("panel",)) + ranked(10, ("type",))
    tops = {
        one.rank
        for seed in range(60)
        for one in lab_screen(designs, random.Random(seed)) or []
        if one.bucket == "top"
    }
    assert {12, 13} <= tops and max(tops) < 12 + FAMILY_TOP


def test_one_look_per_design_or_an_error() -> None:
    with pytest.raises(ValueError, match="one look per ranked design"):
        lab_screen(ranked(10), random.Random(0), looks=twins(9))


def test_photo_sets_are_the_best_two_then_the_next_two() -> None:
    assert photo_sets(["a", "b", "c", "d", "e"]) == [("a", "b"), ("c", "d")]
    assert photo_sets(["a", "b", "c"]) == [("a", "b"), ("c",)]
    assert photo_sets(["a"]) == [("a",)]
    assert photo_sets([]) == []
