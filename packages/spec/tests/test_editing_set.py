"""M10i: the first editing set - schema 7 and its ops.

What a reviewer should be able to repeat back:
- a version-6 poster upgrades to 7 unchanged; fades are one per edge;
- fade, flip, adjust: photos only (fade also a band of colour); length 0 takes a fade
  away; flipping twice is the photo as taken; light and colour back at 0 is no
  adjustment at all;
- align, font, case and effect: text only; a size that set its own alignment or case
  follows an explicit ask, like visibility;
- reorder never sends a part behind a backdrop that covers the poster, and never puts
  words that sit behind a photo's subject below that photo;
- see-through stops at 10% (fainter is hidden: a different op);
- align uses the designs' margin, and a story's safe band, size by size.
"""

from __future__ import annotations

import pytest
from conftest import make_spec
from stratum_spec import (
    Box,
    ImageLayer,
    LayoutOverride,
    PosterSpec,
    ShapeLayer,
    TextLayer,
    apply_op,
    apply_ops,
    load,
)
from stratum_spec.models import SCHEMA_VERSION, Fade
from stratum_spec.ops import (
    ALIGN_MARGIN,
    STORY_SAFE_BOTTOM,
    STORY_SAFE_TOP,
    AdjustPhoto,
    Align,
    Flip,
    OpError,
    Reorder,
    SetAlign,
    SetCase,
    SetEffect,
    SetFade,
    SetFont,
    SetOpacity,
)


def with_base(spec: PosterSpec) -> PosterSpec:
    """The poster with a colour backdrop under everything and a story layout."""
    base = ShapeLayer(id="base", box=Box(x=0, y=0, w=1, h=1), fill="palette.surface")
    bg = spec.layer("bg").model_copy(update={"box": Box(x=0, y=0, w=1, h=0.6)})
    layers = [base, bg, *[one for one in spec.layers if one.id != "bg"]]
    variants = {
        "9:16": {
            "headline": LayoutOverride(box=Box(x=0.1, y=0.5, w=0.8, h=0.1), align="center"),
            "bg": LayoutOverride(fades=(Fade(edge="bottom", length=0.2),)),
        }
    }
    return spec.model_copy(update={"layers": layers, "variants": variants})


# --- schema 7 ----------------------------------------------------------------------------


def test_a_version_6_poster_upgrades_unchanged() -> None:
    stored = make_spec().model_dump(mode="json")
    stored["schema_version"] = 6
    upgraded = load(stored)
    assert upgraded.schema_version == SCHEMA_VERSION == 7
    assert upgraded.layers == make_spec().layers


def test_a_photo_fades_once_per_edge() -> None:
    fade = Fade(edge="left", length=0.3)
    with pytest.raises(ValueError, match="at most once"):
        ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1), fades=(fade, fade))
    with pytest.raises(ValueError, match="at most once"):
        LayoutOverride(fades=(fade, fade))


# --- photos -------------------------------------------------------------------------------


def test_fade_adds_replaces_and_removes_an_edge() -> None:
    spec = make_spec()
    once = apply_op(spec, SetFade(layer="bg", edge="bottom", length=0.3))
    again = apply_op(once, SetFade(layer="bg", edge="bottom", length=0.5))
    both = apply_op(again, SetFade(layer="bg", edge="left", length=0.2))
    photo = both.layer("bg")
    assert isinstance(photo, ImageLayer)
    assert {(f.edge, f.length) for f in photo.fades} == {("bottom", 0.5), ("left", 0.2)}
    gone = apply_op(both, SetFade(layer="bg", edge="bottom", length=0))
    gone_photo = gone.layer("bg")
    assert isinstance(gone_photo, ImageLayer)
    assert [f.edge for f in gone_photo.fades] == ["left"]


def test_a_size_that_fades_its_own_way_follows_the_ask() -> None:
    spec = with_base(make_spec())
    out = apply_op(spec, SetFade(layer="bg", edge="bottom", length=0.4))
    story = out.variants["9:16"]["bg"].fades
    assert story is not None and [(f.edge, f.length) for f in story] == [("bottom", 0.4)]


def test_a_band_of_colour_can_fade_but_words_cannot() -> None:
    spec = with_base(make_spec())
    out = apply_op(spec, SetFade(layer="base", edge="top", length=0.5))
    base = out.layer("base")
    assert isinstance(base, ShapeLayer) and base.fades[0].edge == "top"
    with pytest.raises(OpError, match="cannot fade"):
        apply_op(spec, SetFade(layer="headline", edge="top", length=0.5))


def test_flip_twice_is_the_photo_as_taken() -> None:
    spec = make_spec()
    once = apply_op(spec, Flip(layer="bg"))
    photo = once.layer("bg")
    assert isinstance(photo, ImageLayer) and photo.flip
    assert apply_op(once, Flip(layer="bg")) == spec
    with pytest.raises(OpError, match="not a photo"):
        apply_op(spec, Flip(layer="logo"))  # the client's logo is never mirrored


def test_adjust_sets_what_it_names_and_back_to_zero_is_no_adjustment() -> None:
    spec = make_spec()
    brighter = apply_op(spec, AdjustPhoto(layer="bg", brightness=0.2))
    warmer = apply_op(brighter, AdjustPhoto(layer="bg", warmth=0.1))
    photo = warmer.layer("bg")
    assert isinstance(photo, ImageLayer) and photo.adjust is not None
    assert (photo.adjust.brightness, photo.adjust.warmth) == (0.2, 0.1)
    back = apply_ops(warmer, [AdjustPhoto(layer="bg", brightness=0, warmth=0)])
    assert back == spec
    with pytest.raises(OpError, match="names no setting"):
        apply_op(spec, AdjustPhoto(layer="bg"))
    with pytest.raises(ValueError):
        AdjustPhoto(layer="bg", brightness=0.9)  # beyond what a real photo allows


# --- text ---------------------------------------------------------------------------------


def test_text_ops_change_the_words_look_and_follow_sizes_that_chose() -> None:
    spec = with_base(make_spec())
    out = apply_ops(
        spec,
        [
            SetAlign(layer="headline", align="right"),
            SetFont(layer="headline", font="font.body"),
            SetCase(layer="headline", upper=True),
            SetEffect(layer="headline", kind="outline"),
        ],
    )
    headline = out.layer("headline")
    assert isinstance(headline, TextLayer)
    assert (headline.align, headline.font, headline.uppercase) == ("right", "font.body", True)
    assert headline.effect is not None and headline.effect.kind == "outline"
    assert out.variants["9:16"]["headline"].align == "right"  # it had chosen centre
    plain = apply_op(out, SetEffect(layer="headline", kind="none"))
    plain_headline = plain.layer("headline")
    assert isinstance(plain_headline, TextLayer) and plain_headline.effect is None
    for op in (SetAlign(layer="bg", align="left"), SetEffect(layer="logo", kind="shadow")):
        with pytest.raises(OpError, match="not a text layer"):
            apply_op(spec, op)


# --- arrangement --------------------------------------------------------------------------


def order(spec: PosterSpec) -> list[str]:
    return [one.id for one in spec.layers]


def test_reorder_moves_a_part_but_never_behind_the_backdrop() -> None:
    spec = with_base(make_spec())
    assert order(spec) == ["base", "bg", "headline", "logo"]
    assert order(apply_op(spec, Reorder(layer="logo", to="back"))) == [
        "base",
        "logo",
        "bg",
        "headline",
    ]
    assert order(apply_op(spec, Reorder(layer="bg", to="front")))[-1] == "bg"
    assert order(apply_op(spec, Reorder(layer="bg", to="backward"))) == order(spec)
    assert order(apply_op(spec, Reorder(layer="headline", to="forward")))[-1] == "headline"


def test_words_behind_a_subject_stay_above_that_photo() -> None:
    spec = with_base(make_spec())
    headline = spec.layer("headline").model_copy(update={"behind": "bg"})
    behind = spec.model_copy(
        update={"layers": [headline if one.id == "headline" else one for one in spec.layers]}
    )
    with pytest.raises(OpError, match="must stay above that photo"):
        apply_op(behind, Reorder(layer="headline", to="back"))


def test_see_through_stops_at_ten_percent() -> None:
    out = apply_op(make_spec(), SetOpacity(layer="logo", opacity=0.5))
    assert out.layer("logo").opacity == 0.5
    with pytest.raises(ValueError):
        SetOpacity(layer="logo", opacity=0.05)


def test_align_uses_the_margin_and_a_storys_safe_band() -> None:
    spec = with_base(make_spec())
    out = apply_op(spec, Align(layer="headline", x="left", y="bottom"))
    box = out.layer("headline").box
    width, height = spec.canvas.size
    assert box.x == pytest.approx(ALIGN_MARGIN * width / width)
    assert box.y + box.h == pytest.approx(1 - ALIGN_MARGIN * width / height)
    story = out.variants["9:16"]["headline"].box
    assert story is not None
    assert story.y + story.h == pytest.approx(STORY_SAFE_BOTTOM)
    centred = apply_op(spec, Align(layer="logo", x="center", y="top"))
    logo = centred.layer("logo").box
    assert logo.x + logo.w / 2 == pytest.approx(0.5)
    tall = apply_op(spec, Align(layer="headline", y="top")).variants["9:16"]["headline"].box
    assert tall is not None and tall.y == pytest.approx(STORY_SAFE_TOP)
    with pytest.raises(OpError, match="no direction"):
        apply_op(spec, Align(layer="logo"))
