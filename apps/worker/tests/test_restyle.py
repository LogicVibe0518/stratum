"""Restyle (M10c): "words on the left", "cream panel", "make it words only".

Which neighbouring design a style change means. Every asked-for setting is kept
exactly; every other setting stays as it was where the new design has it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import BrandKit, ImageLayer, PosterSpec, TextLayer
from stratum_spec.ops import Dim, Scale, SetColor, SetVisible, apply_ops
from stratum_worker.design import decode
from stratum_worker.design.choose import Material, fill
from stratum_worker.edit.restyle import RestyleError, carry_over, described, said, targets

PANEL = "ds.panel.1.0010110"  # side end, share 0.42, surface, left, none, panel, bold


def test_one_setting_changes_and_the_rest_stay() -> None:
    first = decode(targets(PANEL, {"fill": "dark"})[0])
    before = decode(PANEL)
    assert first.family.name == "panel"
    assert first.choice == {**before.choice, "fill": "dark"}


def test_every_candidate_keeps_what_was_asked() -> None:
    for one in targets(PANEL, {"side": "start", "scale": "calm"}):
        choice = decode(one).choice
        assert choice["side"] == "start" and choice["scale"] == "calm"


def test_the_candidates_keep_as_much_as_they_can_first() -> None:
    before = decode(PANEL).choice
    kept = [
        sum(decode(one).choice[name] == value for name, value in before.items() if name != "fill")
        for one in targets(PANEL, {"fill": "primary"})
    ]
    assert kept == sorted(kept, reverse=True)


def test_switching_family_carries_the_settings_both_have() -> None:
    first = decode(targets(PANEL, {"family": "type"})[0])
    assert first.family.name == "type"
    assert first.choice["fill"] == "surface"
    assert first.choice["align"] == "left"
    assert first.choice["scale"] == "bold"


def test_switching_family_with_a_setting_of_that_family() -> None:
    first = decode(targets(PANEL, {"family": "overlay", "treatment": "card"})[0])
    assert first.family.name == "overlay" and first.choice["treatment"] == "card"


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"colour": "gold"}, "has no colour setting"),
        ({"fill": "gold"}, "fill can be primary, surface, dark; not gold"),
        ({"family": "mosaic"}, "There is no mosaic design"),
        ({"fill": "surface"}, "already has that style"),
        ({}, "Say which style setting"),
    ],
)
def test_impossible_restyles_are_a_sentence(changes: dict[str, str], said: str) -> None:
    with pytest.raises(RestyleError, match=said):
        targets(PANEL, changes)


def test_a_fixed_template_has_no_styles_to_switch() -> None:
    with pytest.raises(RestyleError, match="fixed template"):
        targets("full_bleed", {"fill": "surface"})


def test_a_preset_name_restyles_like_its_design() -> None:
    assert decode(targets("two_photos", {"fill": "dark"})[0]).family.name == "pair"


def test_a_design_is_described_for_its_version_note() -> None:
    assert described(PANEL).startswith("Photo and panel (side end, share 0.42, fill surface")


# --- what changed, in words (live, 1 Oct: the note listed raw settings) ------------------

REPO = Path(__file__).resolve().parents[3]
WARM = {
    "text": "#251e14",
    "muted": "#7b7061",
    "primary": "#6b5941",
    "surface": "#fcf6ed",
    "secondary": "#ca9b5a",
    "text_on_dark": "#ffffff",
    "text_on_primary": "#ffffff",
}
KIT = BrandKit.model_validate(
    {**json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text()), "palette": WARM}
)
CALM = "ds.panel.1.0010111"  # PANEL with a calm headline


@pytest.mark.parametrize(
    ("changes", "note"),
    [
        ({"scale": "calm"}, "Changed the style: a smaller, calmer headline."),
        ({"fill": "dark"}, "Changed the style: a very dark brown background behind the words."),
        ({"share": "0.58"}, "Changed the style: a bigger panel."),
        ({"side": "start", "align": "center"}, None),
        ({"family": "overlay", "treatment": "card"}, None),
    ],
)
def test_a_restyle_is_said_in_words(changes: dict[str, str], note: str | None) -> None:
    after = targets(PANEL, changes)[0]
    text = said(PANEL, after, changes, KIT)
    if note is not None:
        assert text == note
    assert "share 0." not in text and "fill surface" not in text
    assert text.endswith(".")


def test_a_new_design_is_named_with_what_was_asked() -> None:
    after = targets(PANEL, {"family": "overlay", "treatment": "card"})[0]
    assert said(PANEL, after, {"family": "overlay", "treatment": "card"}, KIT) == (
        "Switched to the Photo with words over it design, with the words on a cream card."
    )


# --- the person's changes survive a restyle ------------------------------------------------

PHOTO, LOGO = "a" * 64, "c" * 64
MATERIAL = Material(
    words={"headline": "A little peace", "offer_detail": "20% off in November", "cta": "Book"},
    kit=KIT,
    photos=(PHOTO,),
    background=None,
    logo=LOGO,
    logo_aspect=2.0,
    ink=None,
    reads={},
)


def laid(design: str) -> PosterSpec:
    spec = fill(design, MATERIAL, ["4:5"], REPO)
    assert spec is not None
    return spec


def test_colours_darkness_hidden_parts_and_logo_size_are_carried() -> None:
    pristine = laid(PANEL)
    changed = apply_ops(
        pristine,
        [
            SetColor(layer="headline", color="palette.muted"),
            Dim(layer="bg", amount=0.3),
            SetVisible(layer="cta", visible=False),
            Scale(layer="logo", factor=1.2),
        ],
    )
    kept = carry_over(changed, pristine, laid(CALM), KIT, {"scale": "calm"})
    headline, photo = kept.spec.layer("headline"), kept.spec.layer("bg")
    assert isinstance(headline, TextLayer) and headline.color == "palette.muted"
    assert isinstance(photo, ImageLayer) and photo.dim == 0.3
    assert not kept.spec.layer("cta").visible
    assert kept.spec.layer("logo").box.w == pytest.approx(laid(CALM).layer("logo").box.w * 1.2)
    assert kept.kept == [
        "the darker photo",
        "the warm grey headline",
        "the hidden call to action",
        "the bigger logo",
    ]
    assert kept.dropped == []


def test_nothing_changed_means_nothing_carried() -> None:
    kept = carry_over(laid(PANEL), laid(PANEL), laid(CALM), KIT, {"scale": "calm"})
    assert kept.spec == laid(CALM) and kept.kept == [] and kept.dropped == []


def test_a_text_colour_that_would_not_read_is_dropped_and_said() -> None:
    pristine = laid(PANEL)  # cream panel, very dark brown words
    changed = apply_ops(pristine, [SetColor(layer="offer", color="palette.text")])
    changed = apply_ops(changed, [SetColor(layer="headline", color="palette.primary")])
    dark = targets(PANEL, {"fill": "dark"})[0]
    kept = carry_over(changed, pristine, laid(dark), KIT, {"fill": "dark"})
    assert kept.dropped == [
        "Your brown headline would not be readable on the new background, so it has the "
        "design's colour."
    ]
    headline = kept.spec.layer("headline")
    assert isinstance(headline, TextLayer) and headline.color != "palette.primary"


def test_a_shape_colour_is_not_carried_when_the_restyle_changes_the_background() -> None:
    pristine = laid(PANEL)
    gold = apply_ops(pristine, [SetColor(layer="panel", color="palette.secondary")])
    dark = targets(PANEL, {"fill": "dark"})[0]
    kept = carry_over(gold, pristine, laid(dark), KIT, {"fill": "dark"})
    assert kept.spec.layer("panel").fill == laid(dark).layer("panel").fill  # type: ignore[union-attr]
    calm = carry_over(gold, pristine, laid(CALM), KIT, {"scale": "calm"})
    assert calm.spec.layer("panel").fill == "palette.secondary"  # type: ignore[union-attr]


def test_without_a_fresh_layout_nothing_is_guessed() -> None:
    assert carry_over(laid(PANEL), None, laid(CALM), KIT, {"scale": "calm"}).spec == laid(CALM)
