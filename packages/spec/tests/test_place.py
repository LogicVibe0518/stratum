"""Editor v2: `place`, the op a drag on the canvas sends.

What a reviewer should be able to repeat back:
- a part dragged in one size moves in that size only; every other size keeps it
  exactly where it was drawn, including sizes that were using the base layout;
- words and a logo stop at the poster's edge; a photo or a shape may bleed off it;
- a text box dragged by a corner carries the size the words should be: the words
  get that size, and can still shrink to fit longer words later;
- a locked part, or a size given to something that is not words, is refused;
- the op is all-or-nothing like every other, and never touches the input spec.
"""

from __future__ import annotations

import pytest
from conftest import make_spec
from stratum_spec import Box, LayoutOverride, PosterSpec, TextLayer, apply_op, retarget
from stratum_spec.ops import SIZES, OpError, Place

STORY_HEADLINE = Box(x=0.1, y=0.5, w=0.8, h=0.1)


def with_story(spec: PosterSpec) -> PosterSpec:
    """The poster with its own story layout for the headline."""
    variants = {"9:16": {"headline": LayoutOverride(box=STORY_HEADLINE, align="center")}}
    return spec.model_copy(update={"variants": variants})


def box_in(spec: PosterSpec, aspect: str, layer: str) -> Box:
    return retarget(spec, aspect).layer(layer).box


def test_a_drag_in_the_story_moves_the_story_only() -> None:
    spec = with_story(make_spec())
    to = Box(x=0.2, y=0.3, w=0.6, h=0.12)
    after = apply_op(spec, Place(layer="headline", aspect="9:16", box=to))
    assert box_in(after, "9:16", "headline") == to
    for aspect in ("4:5", "1:1", "16:9"):
        assert box_in(after, aspect, "headline") == box_in(spec, aspect, "headline"), aspect
    # The story's other layout choices stay.
    assert after.variants["9:16"]["headline"].align == "center"


def test_a_drag_in_the_base_size_leaves_every_other_size_where_it_was() -> None:
    spec = with_story(make_spec())
    before = {aspect: box_in(spec, aspect, "logo") for aspect in SIZES}
    to = Box(x=0.05, y=0.05, w=0.2, h=0.1)
    after = apply_op(spec, Place(layer="logo", aspect="4:5", box=to))
    assert after.layer("logo").box == to
    for aspect in SIZES:
        expected = to if aspect == "4:5" else before[aspect]
        assert box_in(after, aspect, "logo") == expected, aspect


def test_a_size_that_had_its_own_layout_keeps_it_when_the_base_moves() -> None:
    spec = with_story(make_spec())
    after = apply_op(
        spec, Place(layer="headline", aspect="4:5", box=Box(x=0.1, y=0.1, w=0.8, h=0.1))
    )
    assert after.variants["9:16"]["headline"].box == STORY_HEADLINE


def test_words_and_the_logo_stop_at_the_edge() -> None:
    spec = make_spec()
    past = Box(x=0.9, y=-0.2, w=0.3, h=0.1)
    words = apply_op(spec, Place(layer="headline", aspect="1:1", box=past))
    box = box_in(words, "1:1", "headline")
    assert (box.x, box.y, box.w) == (pytest.approx(0.7), 0.0, 0.3)
    wide = apply_op(spec, Place(layer="logo", aspect="4:5", box=Box(x=-0.5, y=0, w=1.6, h=0.1)))
    assert wide.layer("logo").box.x == 0.0 and wide.layer("logo").box.w == 1.0


def test_a_photo_may_bleed_off_the_edge() -> None:
    spec = make_spec()
    bleed = Box(x=-0.1, y=-0.05, w=1.2, h=1.1)
    after = apply_op(spec, Place(layer="bg", aspect="4:5", box=bleed))
    assert after.layer("bg").box == bleed


def test_a_corner_drag_sizes_the_words() -> None:
    spec = make_spec()
    to = Box(x=0.08, y=0.6, w=0.84, h=0.2)
    after = apply_op(spec, Place(layer="headline", aspect="4:5", box=to, text_size=0.15))
    headline = after.layer("headline")
    assert isinstance(headline, TextLayer)
    assert headline.max_size == 0.15 and headline.min_size == 0.02
    # The other sizes keep the size range they were drawn with.
    story = retarget(after, "9:16").layer("headline")
    assert isinstance(story, TextLayer) and story.max_size == 0.12


def test_smaller_words_lower_the_floor_too() -> None:
    spec = with_story(make_spec())
    after = apply_op(
        spec, Place(layer="headline", aspect="9:16", box=STORY_HEADLINE, text_size=0.01)
    )
    story = retarget(after, "9:16").layer("headline")
    assert isinstance(story, TextLayer) and (story.min_size, story.max_size) == (0.01, 0.01)
    base = after.layer("headline")
    assert isinstance(base, TextLayer) and base.max_size == 0.12


def test_a_narrower_column_may_take_more_lines_in_that_size_only() -> None:
    spec = with_story(make_spec())
    narrow = Box(x=0.1, y=0.5, w=0.4, h=0.3)
    after = apply_op(
        spec, Place(layer="headline", aspect="9:16", box=narrow, text_size=0.05, max_lines=6)
    )
    story = retarget(after, "9:16").layer("headline")
    assert isinstance(story, TextLayer) and story.max_lines == 6
    base = retarget(after, "4:5").layer("headline")
    assert isinstance(base, TextLayer) and base.max_lines == 3
    in_base = apply_op(spec, Place(layer="headline", aspect="4:5", box=narrow, max_lines=5))
    assert in_base.layer("headline").max_lines == 5  # type: ignore[union-attr]
    kept = retarget(in_base, "1:1").layer("headline")
    assert isinstance(kept, TextLayer) and kept.max_lines == 3


@pytest.mark.parametrize("words", [{"text_size": 0.1}, {"max_lines": 2}])
def test_only_words_have_a_size(words: dict[str, float]) -> None:
    with pytest.raises(OpError, match="no words"):
        apply_op(
            make_spec(),
            Place(layer="logo", aspect="4:5", box=Box(x=0, y=0, w=0.2, h=0.1), **words),  # type: ignore[arg-type]
        )


def test_a_locked_part_does_not_move() -> None:
    spec = make_spec()
    locked = spec.layer("logo").model_copy(update={"locked": True})
    spec = spec.model_copy(
        update={"layers": [locked if one.id == "logo" else one for one in spec.layers]}
    )
    with pytest.raises(OpError, match="locked"):
        apply_op(spec, Place(layer="logo", aspect="9:16", box=Box(x=0, y=0, w=0.2, h=0.1)))


def test_the_input_is_never_changed() -> None:
    spec = with_story(make_spec())
    dumped = spec.model_dump()
    apply_op(spec, Place(layer="headline", aspect="4:5", box=Box(x=0, y=0, w=0.5, h=0.1)))
    apply_op(spec, Place(layer="headline", aspect="9:16", box=Box(x=0, y=0, w=0.5, h=0.1)))
    assert spec.model_dump() == dumped


def test_a_placed_poster_still_validates_and_round_trips() -> None:
    spec = with_story(make_spec())
    after = apply_op(
        spec,
        Place(layer="headline", aspect="16:9", box=Box(x=0.5, y=0.2, w=0.4, h=0.3), text_size=0.2),
    )
    again = PosterSpec.model_validate(after.model_dump())
    assert again == after
    for aspect in SIZES:
        retarget(again, aspect)  # every size still lays out
