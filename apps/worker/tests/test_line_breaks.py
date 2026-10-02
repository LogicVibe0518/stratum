"""Line breaks by meaning (M10e, spec schema 5).

Live, 2 Oct: "A LITTLE PEACE FOR YOUR / MIND". Greedy wrapping fills each line and
leaves what is left for the last. Balanced wrapping keeps greedy's number of lines -
so every height, every layout and every capacity stays exactly as it was - and shares
the words out evenly, breaking where the meaning breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from stratum_spec import BrandKit
from stratum_worker.render.text import break_cost, fit, load_metrics, wrap, wrap_balanced

REPO = Path(__file__).resolve().parents[3]
KIT = BrandKit.model_validate(
    json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text())
)
DISPLAY = load_metrics(str(REPO / KIT.font("font.display").file))
BODY = load_metrics(str(REPO / KIT.font("font.body").file))


@pytest.mark.parametrize(
    ("text", "metrics", "size", "width", "lines"),
    [
        ("A LITTLE PEACE FOR YOUR MIND", DISPLAY, 85.6, 920, ["A LITTLE PEACE", "FOR YOUR MIND"]),
        (
            "Women and under-18s get 20% off for 2 months from 5 November 2026.",
            BODY,
            41,
            920,
            ["Women and under-18s get 20% off", "for 2 months from 5 November 2026."],
        ),
        ("Call 0161 496 0000", BODY, 40, 300, ["Call", "0161 496 0000"]),
    ],
)
def test_lines_break_where_the_meaning_breaks(
    text: str, metrics: object, size: float, width: float, lines: list[str]
) -> None:
    assert wrap_balanced(text, metrics, size, width) == lines  # type: ignore[arg-type]


def test_a_line_that_fits_is_left_alone() -> None:
    assert wrap_balanced("Boilers sorted", DISPLAY, 60, 920) == ["Boilers sorted"]


@pytest.mark.parametrize(
    ("words", "index", "sign"),
    [
        (["peace,", "for"], 0, -1),  # after a comma: a good place
        (["sorted", "for"], 0, -1),  # before "for": a phrase starts
        (["the", "roof"], 0, 1),  # after "the": never
        (["your", "mind"], 0, 1),
        (["20%", "off"], 0, 1),  # keep the offer in one piece
        (["5", "November"], 0, 1),
        (["0161", "496"], 0, 1),
        (["under", "18"], 0, 1),
    ],
)
def test_what_a_break_costs(words: list[str], index: int, sign: int) -> None:
    assert break_cost(words, index) * sign > 0


WORDS = st.lists(
    st.sampled_from(
        [
            "a",
            "the",
            "for",
            "your",
            "boiler",
            "service",
            "sorted",
            "winter",
            "20%",
            "off",
            "5",
            "Nov",
            "call",
            "today",
            "peace",
            "mind",
            "free",
            "quote",
            "local",
            "plumber",
            "Manchester",
            "every",
            "booking",
            "this",
            "month",
        ]
    ),
    min_size=1,
    max_size=14,
)


@settings(max_examples=150, deadline=None)
@given(words=WORDS, size=st.floats(30, 120), width=st.floats(250, 1000))
def test_balanced_keeps_greedys_line_count_and_every_line_fits(
    words: list[str], size: float, width: float
) -> None:
    text = " ".join(words)
    greedy = wrap(text, DISPLAY, size, width)
    balanced = wrap_balanced(text, DISPLAY, size, width)
    assert len(balanced) == len(greedy)
    assert " ".join(balanced).split() == text.split()  # no word lost, none moved
    for line in balanced:
        if len(line.split()) > 1:
            assert DISPLAY.width(line, size) <= width + 1e-6


def test_fitting_sets_the_same_size_either_way() -> None:
    args = {
        "box_w_px": 920.0,
        "box_h_px": 190.0,
        "min_px": 60.0,
        "max_px": 120.0,
        "max_lines": 3,
        "line_height": 1.05,
    }
    greedy = fit("A LITTLE PEACE FOR YOUR MIND", DISPLAY, **args)  # type: ignore[arg-type]
    balanced = fit("A LITTLE PEACE FOR YOUR MIND", DISPLAY, balanced=True, **args)  # type: ignore[arg-type]
    assert balanced.size_px == greedy.size_px
    assert len(balanced.lines) == len(greedy.lines)
    assert balanced.lines != greedy.lines
