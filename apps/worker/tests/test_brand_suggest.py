"""From a logo to a whole palette.

The promises:
- the logo decides the main colour and the accent whenever it has them, exactly;
- every derived colour clears its contrast floor, for ANY logo - so a suggested kit
  can never produce a poster whose words cannot be read;
- a light logo lends its own colour to the light text, as brands already do;
- a logo with no colour, or one colour, is said out loud, not papered over;
- fonts, the version and any extra tokens in the kit are left alone.
"""

from __future__ import annotations

import io

from hypothesis import given, settings
from hypothesis import strategies as st
from logo_samples import CREAM, INK, NAVY, ORANGE, TEAL, draw, gradient, two_colour, wordmark
from PIL import Image
from stratum_spec import BrandKit
from stratum_spec.colour import contrast_ratio
from stratum_worker.brand import REQUIRED, LogoError, read_logo, starter_kit, suggest
from stratum_worker.brand.oklab import distance, hex_of, lch_of


def kit_for(data: bytes) -> tuple[BrandKit, dict[str, str], tuple[str, ...]]:
    made = suggest(read_logo(data), starter_kit("acme-plumbing"))
    return made.kit, {k: v.source for k, v in made.tokens.items()}, made.notes


def test_a_two_colour_logo_sets_the_main_colour_and_the_accent_exactly() -> None:
    kit, source, _ = kit_for(draw(two_colour()))
    assert kit.palette["primary"] == NAVY
    assert kit.palette["secondary"] == ORANGE
    assert source["primary"] == source["secondary"] == "logo"
    # Navy is dark enough to be the text colour too, as it is in the hand-made kit.
    assert kit.palette["text"] == NAVY
    assert kit.palette["text_on_primary"] == "#ffffff"


def test_a_one_colour_logo_gets_a_derived_accent_and_says_why() -> None:
    made = suggest(read_logo(draw(wordmark(TEAL))), starter_kit("x"))
    assert made.kit.palette["primary"] == TEAL
    assert made.tokens["secondary"].source == "derived"
    assert "only one colour" in made.tokens["secondary"].why
    # Same hue family, clearly different, so it reads as the brand and not a new one.
    assert abs(lch_of(made.kit.palette["secondary"])[2] - lch_of(TEAL)[2]) < 5
    assert distance(made.kit.palette["secondary"], TEAL) > 0.05


def test_teal_takes_dark_words_because_white_would_be_unreadable() -> None:
    kit, _, _ = kit_for(draw(wordmark(TEAL)))
    assert contrast_ratio("#ffffff", TEAL) < 4.5  # the trap
    assert contrast_ratio(kit.palette["text_on_primary"], TEAL) >= 4.5
    assert lch_of(kit.palette["text_on_primary"])[0] < 0.4


def test_a_light_logo_lends_its_colour_to_the_light_words() -> None:
    made = suggest(read_logo(draw(wordmark(CREAM))), starter_kit("x"))
    assert made.kit.palette["text_on_primary"] == CREAM
    assert made.kit.palette["text_on_dark"] == CREAM
    assert made.tokens["primary"].source == "derived"
    assert any("single light colour" in note for note in made.notes)


def test_a_black_logo_leads_with_black_and_asks_for_an_accent() -> None:
    kit, source, notes = kit_for(draw(wordmark(INK)))
    assert kit.palette["primary"] == INK
    assert source["secondary"] == "derived"
    assert any("no colour of its own" in note for note in notes)


def test_a_logo_the_same_colour_as_its_backdrop_is_called_out() -> None:
    # A navy logo on a navy band would vanish. The check says so, and what is done about it.
    made = suggest(read_logo(draw(two_colour())), starter_kit("x"))
    logo_on_main = next(c for c in made.checks if c.what == "The logo on the main colour")
    assert not logo_on_main.ok
    assert any("gets a panel" in note for note in made.notes)


def test_a_gradient_takes_its_far_end_as_the_accent() -> None:
    made = suggest(read_logo(draw(gradient())), starter_kit("x"))
    steps = [s.hex for s in made.reading.swatches]
    primary, secondary = made.kit.palette["primary"], made.kit.palette["secondary"]
    assert distance(secondary, primary) == max(distance(s, primary) for s in steps)


def test_fonts_version_and_extra_tokens_are_left_alone() -> None:
    base = starter_kit("acme-plumbing")
    base = base.model_copy(
        update={"version": 4, "palette": {**base.palette, "highlight": "#ffee00"}}
    )
    made = suggest(read_logo(draw(two_colour())), base)
    assert made.kit.fonts == base.fonts
    assert made.kit.version == 4
    assert made.kit.client_id == "acme-plumbing"
    assert made.kit.palette["highlight"] == "#ffee00"


def test_the_starter_kit_is_complete_and_readable() -> None:
    kit = starter_kit("new-client")
    assert set(REQUIRED) <= set(kit.palette)
    assert contrast_ratio(kit.palette["text_on_primary"], kit.palette["primary"]) >= 4.5
    assert contrast_ratio(kit.palette["text"], kit.palette["surface"]) >= 7


colours = st.tuples(*[st.integers(0, 255)] * 3).map(hex_of)
boxes = st.tuples(st.integers(0, 50), st.integers(0, 30), st.integers(20, 120), st.integers(10, 60))


@settings(max_examples=80, deadline=None)
@given(
    shapes=st.lists(st.tuples(colours, boxes), min_size=1, max_size=4),
    backdrop=st.one_of(st.none(), colours),
)
def test_any_logo_gives_a_complete_palette_that_is_always_readable(
    shapes: list[tuple[str, tuple[int, int, int, int]]], backdrop: str | None
) -> None:
    def rgb(hex_colour: str) -> tuple[int, int, int]:
        return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]

    image = Image.new(
        "RGBA", (180, 100), (0, 0, 0, 0) if backdrop is None else (*rgb(backdrop), 255)
    )
    for colour, (x, y, w, h) in shapes:
        image.paste((*rgb(colour), 255), (x, y, x + w, y + h))
    out = io.BytesIO()
    image.save(out, "PNG")
    try:
        reading = read_logo(out.getvalue())
    except LogoError:
        return
    made = suggest(reading, starter_kit("x"))
    palette = made.kit.palette
    assert set(REQUIRED) <= set(palette)
    assert contrast_ratio(palette["text_on_primary"], palette["primary"]) >= 4.5
    assert contrast_ratio(palette["text"], palette["surface"]) >= 7
    assert contrast_ratio(palette["text_on_dark"], palette["text"]) >= 4.5
    assert contrast_ratio(palette["muted"], palette["surface"]) >= 4.5
    assert all(c.ok for c in made.checks if not c.what.startswith("The logo"))
    # Every token says where it came from.
    assert set(made.tokens) == set(REQUIRED)
    assert all(choice.why for choice in made.tokens.values())
