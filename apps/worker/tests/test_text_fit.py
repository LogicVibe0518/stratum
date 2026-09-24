from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from stratum_worker.render.text import fit, load_metrics, wrap

FONT = Path(__file__).resolve().parents[3] / "assets" / "fonts" / "Anton-Regular.ttf"
BOX_W, BOX_H = 880.0, 200.0


def _metrics():
    return load_metrics(str(FONT))


def test_short_text_gets_the_largest_size() -> None:
    fitted = fit(
        "SALE",
        _metrics(),
        box_w_px=BOX_W,
        box_h_px=BOX_H,
        min_px=20,
        max_px=120,
        max_lines=2,
        line_height=1.1,
    )
    assert fitted.size_px > 100
    assert fitted.lines == ["SALE"]


def test_long_text_wraps_and_shrinks() -> None:
    fitted = fit(
        "Boiler service and full safety check booked within 24 hours",
        _metrics(),
        box_w_px=BOX_W,
        box_h_px=BOX_H,
        min_px=20,
        max_px=120,
        max_lines=3,
        line_height=1.1,
    )
    assert len(fitted.lines) <= 3
    assert fitted.size_px < 120


def test_unbreakable_word_is_truncated_not_overflowed() -> None:
    fitted = fit(
        "A" * 400,
        _metrics(),
        box_w_px=BOX_W,
        box_h_px=BOX_H,
        min_px=20,
        max_px=120,
        max_lines=1,
        line_height=1.1,
    )
    assert len(fitted.lines) == 1
    assert fitted.lines[0].endswith("…")
    assert _metrics().width(fitted.lines[0], fitted.size_px) <= BOX_W


@settings(max_examples=200, deadline=None)
@given(
    words=st.lists(
        st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=14
        ),
        min_size=1,
        max_size=14,
    ),
    max_lines=st.integers(min_value=1, max_value=4),
)
def test_text_never_overflows_its_box(words: list[str], max_lines: int) -> None:
    """The invariant M5 exists to guarantee: nothing spills outside its box."""
    metrics = _metrics()
    fitted = fit(
        " ".join(words),
        metrics,
        box_w_px=BOX_W,
        box_h_px=BOX_H,
        min_px=20,
        max_px=120,
        max_lines=max_lines,
        line_height=1.1,
    )
    assert len(fitted.lines) <= max_lines
    assert fitted.height_px <= BOX_H + 1e-6
    for line in fitted.lines:
        assert metrics.width(line, fitted.size_px) <= BOX_W + 1e-6


def test_wrap_keeps_every_word() -> None:
    text = "twenty percent off every thali this week only"
    lines = wrap(text, _metrics(), 60.0, BOX_W)
    assert " ".join(lines).split() == text.split()
