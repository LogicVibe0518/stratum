"""Schema 3: a poster carries its own layout for every size.

Before it, the story and landscape versions were read from the template file at
render time. Two things followed, both seen on the live site: an edit such as "make
the logo bigger" changed the feed post but not the story, whose template gave the
logo a box of its own; and editing a template file would have moved every old
poster's story without anyone asking.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from stratum_spec import (
    Box,
    LayoutOverride,
    Move,
    PosterSpec,
    Scale,
    SetVisible,
    apply_op,
    embed,
    retarget,
)
from stratum_spec.ops import ALIGN_MARGIN

STORY_LOGO = {"x": 0.7, "y": 0.2, "w": 0.2, "h": 0.06}
TEMPLATE_VARIANTS: dict[str, dict[str, dict[str, object]]] = {
    "9:16": {"logo": {"box": STORY_LOGO}, "headline": {"max_size": 0.07}},
    "16:9": {"headline": {"visible": False}},
}


def carrying(spec: PosterSpec) -> PosterSpec:
    return embed(spec, TEMPLATE_VARIANTS)


def test_a_poster_made_from_a_template_carries_its_sizes(spec: PosterSpec) -> None:
    own = carrying(spec)
    logo = own.variants["9:16"]["logo"]
    assert logo.box == Box.model_validate(STORY_LOGO)
    assert logo.changes() == {"box": {**STORY_LOGO, "rotation": 0.0}}


def test_its_own_layout_wins_over_the_template_file(spec: PosterSpec) -> None:
    own = carrying(spec)
    edited_template = {"9:16": {"logo": {"box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}}}}
    story = retarget(own, "9:16", edited_template)
    assert story.layer("logo").box == Box.model_validate(STORY_LOGO)


def test_a_poster_from_before_schema_3_still_uses_the_template_file(spec: PosterSpec) -> None:
    story = retarget(spec, "9:16", TEMPLATE_VARIANTS)
    assert story.layer("logo").box == Box.model_validate(STORY_LOGO)


def test_one_size_laid_out_has_no_other_sizes(spec: PosterSpec) -> None:
    assert retarget(carrying(spec), "9:16").variants == {}


def test_a_size_cannot_name_a_layer_the_poster_does_not_have(spec: PosterSpec) -> None:
    with pytest.raises(ValidationError, match="does not have: ghost"):
        PosterSpec.model_validate(
            {**spec.model_dump(), "variants": {"9:16": {"ghost": {"visible": False}}}}
        )


def test_a_size_cannot_change_what_a_layer_says_or_its_colour() -> None:
    with pytest.raises(ValidationError):
        LayoutOverride.model_validate({"content": "50% off"})
    with pytest.raises(ValidationError):
        LayoutOverride.model_validate({"color": "palette.primary"})


# --- edits reach every size -----------------------------------------------------------


def test_make_the_logo_bigger_reaches_the_story(spec: PosterSpec) -> None:
    own = carrying(spec)
    bigger = apply_op(own, Scale(layer="logo", factor=1.5))
    base, story = bigger.layer("logo").box, bigger.variants["9:16"]["logo"].box
    assert base.w == pytest.approx(0.3)
    assert story is not None
    assert story.w == pytest.approx(0.3) and story.h == pytest.approx(0.09)
    # In its own place: the same height on the poster, and grown inwards from the
    # story's right margin rather than past it (I-37).
    assert story.y + story.h / 2 == pytest.approx(STORY_LOGO["y"] + STORY_LOGO["h"] / 2)
    assert story.x + story.w == pytest.approx(1 - ALIGN_MARGIN)
    assert retarget(bigger, "9:16").layer("logo").box == story


def test_a_move_reaches_every_size_that_places_the_layer_itself(spec: PosterSpec) -> None:
    moved = apply_op(carrying(spec), Move(layer="logo", dx=0.05, dy=-0.02))
    story = moved.variants["9:16"]["logo"].box
    assert story is not None
    assert (story.x, story.y) == pytest.approx((0.75, 0.18))


def test_a_size_that_uses_the_base_box_follows_it(spec: PosterSpec) -> None:
    moved = apply_op(carrying(spec), Move(layer="headline", dx=0.1, dy=0.0))
    # The story sets only a type size for the headline, so it takes the moved base box.
    assert retarget(moved, "9:16").layer("headline").box.x == pytest.approx(0.18)


def test_showing_a_layer_shows_it_in_sizes_that_had_hidden_it(spec: PosterSpec) -> None:
    shown = apply_op(carrying(spec), SetVisible(layer="headline", visible=True))
    assert retarget(shown, "16:9").layer("headline").visible is True
    hidden = apply_op(carrying(spec), SetVisible(layer="logo", visible=False))
    assert all(not retarget(hidden, a).layer("logo").visible for a in ("4:5", "9:16", "16:9"))
