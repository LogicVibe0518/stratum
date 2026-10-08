"""M10i: the collage family (two photos, no hard seam) and the offer badge.

What a reviewer should be able to repeat back:
- a collage needs two real photos; the second photo fades into the first across the
  seam, and one faded band melts both into the words' colour - toward the words,
  which are below the photos in a feed post and beside them in a banner;
- the words never sit on the photos or the melt;
- a badge is a burst and its words, in the brand's accent with the colour that
  stands out most; one per poster (a second replaces it), at most 14 characters;
- "auto" puts it where it covers the least of the words and the logo, and a size
  where that corner would sit on the words takes its own best corner; a corner
  asked for is kept everywhere;
- the feature walk covers every op the control panel can send, and walks clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from stratum_spec import BrandKit, ImageLayer, ShapeLayer, TextLayer, retarget
from stratum_spec.colour import contrast_ratio
from stratum_worker.app import service
from stratum_worker.design.choose import Material, eligible, family_of
from stratum_worker.design.space import SIZES, everything, lay_out
from stratum_worker.edit import badge, chateval, walk
from stratum_worker.edit.pointing import quick_ops

REPO = Path(__file__).resolve().parents[3]
COLLAGE = "ds.collage.1.0000000"


@pytest.fixture(scope="module")
def kit() -> BrandKit:
    return chateval.poster("pair").kit


def material(kit: BrandKit, photos: tuple[str, ...]) -> Material:
    return Material(
        words={"headline": "Winter boiler checks", "offer_detail": "20% off", "cta": "Book"},
        kit=kit,
        photos=photos,
        background=None,
        logo="c" * 64,
        logo_aspect=2.0,
        ink=None,
        reads={},
    )


# --- the collage ------------------------------------------------------------------------------


def test_a_collage_needs_two_real_photos(kit: BrandKit) -> None:
    assert sum(1 for one in everything(("collage",))) == 192
    one = {family_of(d) for d in eligible(material(kit, ("a" * 64,)))}
    two = {family_of(d) for d in eligible(material(kit, ("a" * 64, "b" * 64)))}
    assert "collage" not in one and "collage" in two


def test_the_photos_blend_and_melt_toward_the_words_in_every_size(kit: BrandKit) -> None:
    laid = lay_out(COLLAGE, kit, REPO, copy=chateval.WORDS, logo_aspect=2.0)
    assert laid is not None
    feed, banner = retarget(laid, "4:5"), retarget(laid, "16:9")
    for spec, seam, melt in ((feed, "left", "top"), (banner, "top", "left")):
        second, band = spec.layer("photo_b"), spec.layer("melt")
        assert isinstance(second, ImageLayer) and isinstance(band, ShapeLayer)
        assert [f.edge for f in second.fades] == [seam]
        assert [f.edge for f in band.fades] == [melt]
        photos_end = max(
            spec.layer(one).box.y + spec.layer(one).box.h
            if spec is feed
            else spec.layer(one).box.x + spec.layer(one).box.w
            for one in ("photo_a", "photo_b", "melt")
        )
        for words in ("headline", "offer", "cta"):
            box = spec.layer(words).box
            start = box.y if spec is feed else box.x
            assert start >= photos_end, (words, start, photos_end)


# --- the badge ----------------------------------------------------------------------------------


def test_a_badge_is_a_burst_and_its_words_readable_on_it(kit: BrandKit) -> None:
    spec = badge.place(chateval.poster("panel").spec, kit, "20% off")
    burst, words = spec.layer("badge"), spec.layer("badge_text")
    assert isinstance(burst, ShapeLayer) and burst.shape == "burst"
    assert isinstance(words, TextLayer) and words.content == "20% OFF"
    assert contrast_ratio(kit.color(words.color), kit.color(burst.fill)) >= 3.0
    again = badge.place(spec, kit, "New")
    assert [one.id for one in again.layers].count("badge") == 1
    assert badge.remove(again).layers == chateval.poster("panel").spec.layers


@pytest.mark.parametrize(("text", "said"), [("", "a few words"), ("Half price everything", "14")])
def test_a_badge_that_cannot_be_made_is_a_sentence(kit: BrandKit, text: str, said: str) -> None:
    with pytest.raises(badge.BadgeError, match=said):
        badge.place(chateval.poster("panel").spec, kit, text)


def test_auto_keeps_off_the_words_in_every_size(kit: BrandKit) -> None:
    spec = badge.place(chateval.poster("panel").spec, kit, "20% off")
    for aspect in SIZES:
        laid = retarget(spec, aspect)
        burst = laid.layer("badge").box
        for one in ("headline", "offer", "cta", "logo"):
            other = laid.layer(one).box
            apart = (
                burst.x + burst.w <= other.x
                or other.x + other.w <= burst.x
                or burst.y + burst.h <= other.y
                or other.y + other.h <= burst.y
            )
            assert apart, (aspect, one)


def test_a_corner_asked_for_is_kept_in_every_size(kit: BrandKit) -> None:
    spec = badge.place(chateval.poster("type").spec, kit, "New", corner="bottom_left")
    for aspect in SIZES:
        box = retarget(spec, aspect).layer("badge").box
        assert box.x < 0.2 and box.y + box.h > 0.75, aspect


# --- the walk and the short words ---------------------------------------------------------------


def test_the_walk_covers_every_op_the_panel_can_send() -> None:
    walked = set().union(*(step.op_names for step in walk.WALK))
    assert walked >= service.EDITOR_OPS, sorted(service.EDITOR_OPS - walked)
    assert any(step.change is not None for step in walk.WALK)  # the badge
    assert any(step.poster == "collage" for step in walk.WALK)


def test_the_walk_walks_clean() -> None:
    steps = tuple(one for one in walk.WALK if one.label in ("fade into the panel", "offer badge"))
    sheet, problems = walk.run(REPO, steps)
    assert problems == [] and sheet.height > walk.TILE_H


@pytest.mark.parametrize(
    ("poster", "layer", "message", "op"),
    [
        ("panel", "bg", "flip", "flip"),
        ("type", "headline", "centre this", "set_align"),
        ("type", "logo", "centre", "align"),
        ("panel", "offer", "caps", "set_case"),
        ("pair", "photo_a", "bring it to the front", "reorder"),
        ("overlay", "headline", "shadow", "text_effect"),
    ],
)
def test_one_word_on_a_pointed_part_needs_no_model(
    poster: str, layer: str, message: str, op: str
) -> None:
    ops = quick_ops(chateval.poster(poster).spec, layer, message)
    assert ops is not None and [one.op for one in ops] == [op]
    assert quick_ops(chateval.poster(poster).spec, layer, message, newer=False) is None


def test_a_pointed_logo_is_never_mirrored() -> None:
    assert quick_ops(chateval.poster("panel").spec, "logo", "flip") is None
