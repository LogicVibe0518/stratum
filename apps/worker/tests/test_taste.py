"""M15c: critic v2's data - Taste Lab answers as rankings, and the measures.

What a reviewer should be able to repeat back:
- an answer is a small ranking: best 3, would send 2, unmarked 1, never send 0 (an
  older answer without "would send": picked 2, never 0, the rest 1), and an answer
  that orders nothing is skipped;
- which designs are good enough is known for every design of an answer with "would
  send" ticks and of "none of these"; for an older answer only the pick and the
  never-sends are known;
- repeats are only for measuring self-consistency, and a post's screens are always on
  the same side of a fold;
- designers' answers weigh more and can be scored on their own (the answer key);
- the measures are right on scorers whose answers we know, and a paired bootstrap
  says how sure a difference is;
- trained on a rater whose taste is known, a lambdarank model recovers it out of fold.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
from stratum_worker.critic.features import FEATURES
from stratum_worker.critic.taste import (
    BAD,
    OK,
    UNKNOWN,
    Answer,
    agreement,
    bar_rows,
    chance,
    consistency,
    experts,
    folds,
    marks,
    paired,
    read_export,
    row_weights,
    simulate,
    stack,
    training,
    with_range,
)

N = len(FEATURES)


def one(
    picked: int | None,
    never: tuple[int, ...] = (),
    send: tuple[int, ...] | None = None,
    *,
    post: str = "p1",
    screen: str = "s1",
    rater: int = 1,
    repeat: bool = False,
    role: str = "",
) -> Answer:
    return Answer(
        screen=screen,
        post=post,
        rater=rater,
        picked=picked,
        never=never,
        features=np.arange(4 * N, dtype=np.float64).reshape(4, N),
        product=np.zeros(4),
        families=("panel",) * 4,
        repeat=repeat,
        send=send,
        role=role,
    )


# --- labels ---------------------------------------------------------------------------


def test_an_older_answer_is_picked_over_the_rest_over_never() -> None:
    labels = one(2, never=(0,)).labels
    assert labels is not None and labels.tolist() == [0, 1, 2, 1]


def test_would_send_ticks_add_a_level_between_the_best_and_the_rest() -> None:
    labels = one(1, never=(3,), send=(0, 1)).labels
    assert labels is not None and labels.tolist() == [2, 3, 1, 0]


@pytest.mark.parametrize("send", [None, ()])
def test_none_of_these_without_a_never_send_orders_nothing(send: tuple[int, ...] | None) -> None:
    assert one(None, send=send).labels is None
    labels = one(None, never=(2,), send=send).labels
    assert labels is not None and labels.tolist() == [1, 1, 0, 1]


def test_what_is_known_about_good_enough() -> None:
    assert one(1, never=(3,), send=(0, 1)).acceptable.tolist() == [OK, OK, BAD, BAD]
    assert one(None).acceptable.tolist() == [BAD] * 4  # none of these
    assert one(None, send=()).acceptable.tolist() == [BAD] * 4
    # Older answer: not chosen is not the same as not good enough.
    assert one(2, never=(0,)).acceptable.tolist() == [BAD, UNKNOWN, OK, UNKNOWN]


# --- reading the export ---------------------------------------------------------------


def line(**fields: Any) -> str:
    row = {
        "screen": "s1",
        "post": "p1",
        "rater": 1,
        "picked": 0,
        "never": [],
        "designs": [{"score": 0.5, "family": "panel"}, {"score": 0.1, "family": "type"}],
        "features": [[0.0] * N, [1.0] * N],
        **fields,
    }
    return json.dumps(row)


def test_the_export_is_read_with_ticks_and_roles() -> None:
    answers = read_export(
        [
            line(send=[0], role=None),
            "",
            line(screen="s2", features=None),  # before measurements were exported
            line(screen="s3", role="designer"),
        ]
    )
    assert [a.screen for a in answers] == ["s1", "s3"]
    assert answers[0].send == (0,) and answers[1].send is None
    assert answers[0].product.tolist() == [0.5, 0.1] and answers[0].families == ("panel", "type")
    # A rater's role is what they last said: their earlier answers take it too.
    assert [a.role for a in answers] == ["designer", "designer"]
    assert all(a.expert and a.weight == 3.0 for a in answers)


def test_measurements_of_the_wrong_width_are_refused() -> None:
    with pytest.raises(ValueError, match=f"expected {N}"):
        read_export([line(features=[[0.0] * 3, [1.0] * 3])])


def test_the_answer_key_is_designers_plus_any_raters_named() -> None:
    answers = [one(0, rater=1, role="designer"), one(0, rater=2), one(0, rater=3, role="other")]
    assert [a.rater for a in experts(answers)] == [1]
    assert [a.rater for a in experts(answers, raters=[2])] == [1, 2]


# --- what is trained on, and how it is split ------------------------------------------


def test_repeats_and_answers_that_order_nothing_are_not_trained_on() -> None:
    answers = [one(0), one(None), one(1, repeat=True), one(2, never=(1,))]
    assert len(training(answers)) == 2


def test_consistency_counts_repeats_against_the_first_answer() -> None:
    answers = [
        one(0, screen="a"),
        one(1, screen="b"),
        one(0, screen="a", repeat=True),
        one(2, screen="b", repeat=True),
    ]
    assert consistency(answers) == (2, 0.5)
    assert consistency([one(0)]) == (0, None)


def test_agreement_is_between_raters_on_the_same_screen() -> None:
    answers = [
        one(0, screen="a", rater=1),
        one(0, screen="a", rater=2),
        one(None, screen="a", rater=3),
        one(1, screen="b", rater=1),
        one(2, screen="a", rater=1, repeat=True),  # a repeat is not a second opinion
    ]
    assert agreement(answers) == (3, pytest.approx(1 / 3))
    assert agreement([one(0)]) == (0, None)


def test_a_posts_screens_are_always_on_the_same_side_of_a_fold() -> None:
    answers = [one(0, post=f"p{i % 7}", screen=f"s{i}") for i in range(40)]
    tested: list[int] = []
    for train, test in folds(answers, k=5):
        assert not {answers[i].post for i in train} & {answers[i].post for i in test}
        tested += test
    assert sorted(tested) == list(range(40))  # every answer is tested exactly once


def test_rows_labels_groups_and_weights_line_up() -> None:
    answers = [one(0, role="designer"), one(None), one(1, never=(2,), role="design_student")]
    x, y, groups = stack(answers)
    weights = row_weights(answers)
    assert x.shape == (8, N) and len(y) == 8 and groups == [4, 4]
    assert weights.tolist() == [3.0] * 4 + [2.0] * 4


def test_the_bar_learns_only_from_designs_whose_fate_is_known() -> None:
    answers = [one(2, never=(0,)), one(None, post="p2"), one(1, send=(0, 1)), one(0, repeat=True)]
    x, bad, weight, source = bar_rows(answers)
    # older: pick + never (2); none of these: all 4; with ticks: all 4; repeat: none.
    assert len(bad) == 10 and x.shape == (10, N) and len(weight) == 10
    assert bad.tolist() == [1, 0, 1, 1, 1, 1, 0, 0, 1, 1]
    assert source == [0, 0, 1, 1, 1, 1, 2, 2, 2, 2]


# --- the measures ----------------------------------------------------------------------


def oracle(answer: Answer) -> np.ndarray:
    labels = answer.labels
    out = np.zeros(4) if labels is None else labels.astype(np.float64)
    return out + np.array([0.0, 0.001, 0.002, 0.003])  # no ties


def test_a_scorer_that_agrees_with_every_answer_gets_full_marks() -> None:
    answers = [one(1, never=(3,), send=(0, 1)), one(2, never=(0,)), one(0)]
    got = marks([oracle(a) for a in answers], answers)
    assert got["top1"] == 1.0 and got["pairs"] == 1.0 and got["never_last"] == 1.0
    assert got["top_ok"] == 1.0 and got["top_ok_n"] == 1.0
    backwards = marks([-oracle(a) for a in answers], answers)
    assert backwards["top1"] == 0.0 and backwards["pairs"] == 0.0
    assert backwards["never_last"] == 0.0 and backwards["top_ok"] == 0.0


def test_chance_is_one_in_the_number_of_designs_shown() -> None:
    assert chance([one(0), one(None), one(3)]) == pytest.approx(0.25)


def test_ranges_and_a_paired_difference() -> None:
    rng = np.random.default_rng(1)
    answers = [one(int(rng.integers(0, 4)), screen=f"s{i}") for i in range(60)]
    good = [oracle(a) for a in answers]
    noise = [rng.normal(size=4) for _ in answers]
    low, value, high = with_range(good, answers, repeats=200)["top1"]
    assert low <= value <= high and value == 1.0
    sure = paired(good, noise, answers, key="top1", repeats=200)
    assert sure["wins"] > 0.99 and sure["low"] > 0 and sure["difference"] > 0.5
    same = paired(good, good, answers, key="top1", repeats=50)
    assert same["difference"] == 0.0 and same["wins"] == 0.0


# --- a rater whose taste is known ----------------------------------------------------


def test_a_simulated_rater_is_mostly_self_consistent_and_ticks_what_clears_their_bar() -> None:
    taste = np.zeros(N)
    taste[0] = 1.0
    answers = simulate(200, taste, noise=0.3, bar=0.5, role="designer", seed=3)
    repeats, rate = consistency(answers)
    assert repeats > 5 and rate is not None and rate > 0.6
    first = [a for a in answers if not a.repeat]
    assert all(a.send is not None and a.role == "designer" for a in first)
    assert any(a.picked is None for a in first)  # sometimes none clears the bar


def test_lambdarank_recovers_a_known_taste_out_of_fold() -> None:
    lgb = pytest.importorskip("lightgbm")
    taste = np.zeros(N)
    taste[[0, 5, 9]] = [1.5, -1.0, 0.8]
    answers = training(simulate(400, taste, noise=0.4, posts=40, seed=5))
    scores: list[np.ndarray] = [np.zeros(4)] * len(answers)
    for train, test in folds(answers, k=4):
        x, y, groups = stack([answers[i] for i in train])
        data = lgb.Dataset(x, y, group=groups)
        params = {"objective": "lambdarank", "verbose": -1, "min_data_in_leaf": 5, "seed": 0}
        booster = lgb.train(params, data, num_boost_round=60)
        for i in test:
            scores[i] = booster.predict(answers[i].features)
    got = marks(scores, answers)
    assert got["top1"] > 2 * chance(answers)
    assert got["pairs"] > 0.75
