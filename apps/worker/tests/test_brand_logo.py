"""Reading brand colours out of a logo file.

The promises, one per test group:
- a flat logo fill comes back as the exact hex that was drawn, not an average;
- anti-aliased edges, transparent pixels and a plain background are never colours;
- a filled badge keeps its fill, because there the fill IS the brand;
- the answer does not depend on the file's size or padding;
- a file with nothing in it is refused with a sentence, never guessed at.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from logo_samples import (
    CREAM,
    GREEN,
    INK,
    LEAF,
    NAVY,
    ORANGE,
    badge,
    draw,
    gradient,
    two_colour,
    wordmark,
)
from PIL import Image
from stratum_worker.brand.logo import LogoError, read_logo
from stratum_worker.brand.oklab import (
    distance,
    from_lch,
    hex_of,
    in_gamut,
    lab_of,
    lch_of,
    oklab_to_rgb,
    rgb_to_oklab,
)

HEX = r"^#[0-9a-f]{6}$"

# --- OKLab ------------------------------------------------------------------------


def test_oklab_matches_the_published_reference_values() -> None:
    assert np.allclose(lab_of("#ffffff"), [1.0, 0.0, 0.0], atol=1e-6)
    assert np.allclose(lab_of("#000000"), [0.0, 0.0, 0.0], atol=1e-6)
    # sRGB red, from Ottosson's reference implementation.
    assert np.allclose(lab_of("#ff0000"), [0.627955, 0.224863, 0.125846], atol=1e-5)


@given(st.tuples(*[st.integers(0, 255)] * 3))
def test_every_srgb_colour_survives_the_round_trip(rgb: tuple[int, int, int]) -> None:
    back = oklab_to_rgb(rgb_to_oklab(np.array(rgb, dtype=np.float64)))
    assert tuple(int(v) for v in back) == rgb


@given(
    # Below L=0.1, one step of 8-bit sRGB is already bigger than the tolerance.
    st.floats(0.1, 1.0),
    st.floats(0.0, 0.4),
    st.floats(0.0, 360.0, exclude_max=True),
)
def test_a_derived_colour_is_always_displayable_and_keeps_its_lightness(
    lightness: float, chroma: float, hue: float
) -> None:
    made = from_lch(lightness, chroma, hue)
    assert in_gamut(lab_of(made), tolerance=1e-3)
    assert abs(lch_of(made)[0] - lightness) < 0.01


def test_hue_is_kept_when_a_colour_has_to_lose_chroma() -> None:
    # A very light, very saturated blue does not exist on screens. Clipping channels
    # would turn it purple; reducing chroma keeps it blue.
    made = from_lch(0.9, 0.3, 260.0)
    _, chroma, hue = lch_of(made)
    assert chroma < 0.3
    assert abs(hue - 260.0) < 3.0


# --- reading logos ----------------------------------------------------------------


def hexes(data: bytes) -> list[str]:
    return [s.hex for s in read_logo(data).swatches]


def test_a_flat_logo_gives_back_the_exact_colours_drawn() -> None:
    reading = read_logo(draw(two_colour()))
    assert [s.hex for s in reading.swatches] == [NAVY, ORANGE]
    assert reading.swatches[0].share > reading.swatches[1].share
    assert reading.tone == "colour"
    assert reading.background is None


def test_anti_aliased_edges_are_not_counted_as_colours() -> None:
    # The wordmark alone has thousands of navy-to-transparent and navy-to-orange edge
    # pixels. None of them may come back as a third colour.
    image = Image.open(io.BytesIO(draw(two_colour())))
    alpha = np.asarray(image)[..., 3]
    assert ((alpha > 0) & (alpha < 255)).sum() > 1000  # the edges really are there
    assert len(read_logo(draw(two_colour())).swatches) == 2


def test_a_white_background_is_ignored_and_jpeg_noise_is_absorbed() -> None:
    reading = read_logo(draw(two_colour(), background="#ffffff", fmt="JPEG", quality=70))
    assert reading.background == "#ffffff"
    assert len(reading.swatches) == 2
    assert distance(reading.swatches[0].hex, NAVY) < 0.02
    assert distance(reading.swatches[1].hex, ORANGE) < 0.03
    assert any("background" in note for note in reading.notes)


def test_a_filled_badge_keeps_its_fill_as_a_brand_colour() -> None:
    reading = read_logo(draw(badge()))
    assert [s.hex for s in reading.swatches] == [GREEN, LEAF]
    assert reading.background is None
    assert any("fills its whole file" in note for note in reading.notes)


def test_size_does_not_change_the_answer() -> None:
    # Shares move a little with size (a 6px bar loses more of itself to its edges than
    # an 8px one), so they are compared loosely. The colours must not move at all.
    small = read_logo(draw(two_colour(), size=(300, 100)))
    large = read_logo(draw(two_colour(), size=(2400, 800)))
    assert [s.hex for s in small.swatches] == [s.hex for s in large.swatches] == [NAVY, ORANGE]
    for one, two in zip(small.swatches, large.swatches, strict=True):
        assert math.isclose(one.share, two.share, abs_tol=0.05)


def test_transparent_padding_does_not_change_the_answer() -> None:
    plain = read_logo(draw(two_colour()))
    padded = read_logo(draw(two_colour(), pad=300))
    assert [s.hex for s in plain.swatches] == [s.hex for s in padded.swatches]
    for one, two in zip(plain.swatches, padded.swatches, strict=True):
        assert math.isclose(one.share, two.share, abs_tol=0.05)


@pytest.mark.parametrize(
    ("colour", "tone"),
    [(INK, "dark"), (CREAM, "light"), (NAVY, "colour")],
)
def test_the_logo_tone_says_what_it_was_made_to_sit_on(colour: str, tone: str) -> None:
    reading = read_logo(draw(wordmark(colour)))
    assert [s.hex for s in reading.swatches] == [colour]
    assert reading.tone == tone


def test_a_gradient_is_listed_by_its_main_steps_and_says_so() -> None:
    reading = read_logo(draw(gradient()))
    assert 3 <= len(reading.swatches) <= 8
    assert any("gradient" in note for note in reading.notes)


def test_a_blank_file_is_refused_not_guessed() -> None:
    empty = io.BytesIO()
    Image.new("RGBA", (300, 100), (0, 0, 0, 0)).save(empty, "PNG")
    with pytest.raises(LogoError, match="blank"):
        read_logo(empty.getvalue())
    with pytest.raises(LogoError, match="not an image"):
        read_logo(b"%PDF-1.7 not a picture")


colours = st.tuples(*[st.integers(0, 255)] * 3).map(hex_of)
boxes = st.tuples(st.integers(0, 50), st.integers(0, 30), st.integers(20, 120), st.integers(10, 60))


@settings(max_examples=60, deadline=None)
@given(
    shapes=st.lists(st.tuples(colours, boxes), min_size=1, max_size=5),
    backdrop=st.one_of(st.none(), colours),
)
def test_any_flat_logo_reads_as_real_colours_biggest_first(
    shapes: list[tuple[str, tuple[int, int, int, int]]], backdrop: str | None
) -> None:
    fill = (0, 0, 0, 0) if backdrop is None else (*lab_rgb(backdrop), 255)
    image = Image.new("RGBA", (180, 100), fill)
    for colour, (x, y, w, h) in shapes:
        image.paste((*lab_rgb(colour), 255), (x, y, x + w, y + h))
    out = io.BytesIO()
    image.save(out, "PNG")
    try:
        reading = read_logo(out.getvalue())
    except LogoError:
        return  # e.g. a white box on a white page: nothing to read, and it said so
    shares = [s.share for s in reading.swatches]
    assert shares == sorted(shares, reverse=True)
    assert sum(shares) <= 1.0 + 1e-3  # each share is rounded to 4 places
    drawn = {c for c, _ in shapes} | ({backdrop} if backdrop else set())
    for swatch in reading.swatches:
        assert swatch.hex.startswith("#") and len(swatch.hex) == 7
        # Flat fills come back exactly, or as a colour within "the same" of one drawn.
        assert min(distance(swatch.hex, c) for c in drawn) < 0.05


def lab_rgb(hex_colour: str) -> tuple[int, int, int]:
    text = hex_colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
