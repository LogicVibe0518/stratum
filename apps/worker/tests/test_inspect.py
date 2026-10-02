"""What the control panel is told about a poster (M10d), worked out like the renderer.

Every number here must match what is drawn: the words' size and lines come from the
same fit the renderer uses, the contrast from the same rule the layout check uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import BrandKit, ImageLayer, PosterSpec, retarget
from stratum_spec.ops import SetColor, apply_ops
from stratum_worker.design.choose import Material, fill
from stratum_worker.draft.capacity import measure
from stratum_worker.edit.inspect import inspect, photo_axes, style_options
from stratum_worker.render.layout import place_text

REPO = Path(__file__).resolve().parents[3]
PANEL = "ds.panel.1.0010110"
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
PHOTO = "a" * 64
MATERIAL = Material(
    words={
        "headline": "A little peace for your mind",
        "offer_detail": "Women and under-18s get 20% off for 2 months.",
        "cta": "Take a moment for your mind",
    },
    kit=KIT,
    photos=(PHOTO,),
    background=None,
    logo="c" * 64,
    logo_aspect=1.0,
    ink=None,
    reads={},
)


def poster(aspect: str = "4:5") -> PosterSpec:
    spec = fill(PANEL, MATERIAL, ["4:5", "9:16"], REPO)
    assert spec is not None
    return retarget(spec, aspect, {})  # type: ignore[arg-type]


def panel(spec: PosterSpec) -> dict:  # type: ignore[type-arg]
    return inspect(
        spec,
        KIT,
        REPO,
        fits=measure(spec, KIT, root=REPO),
        photo_sizes={PHOTO: (1600, 1200)},
    )


def by_id(out: dict, layer_id: str) -> dict:  # type: ignore[type-arg]
    return next(one for one in out["layers"] if one["id"] == layer_id)


def test_every_layer_is_listed_in_order_with_a_name() -> None:
    spec = poster()
    out = panel(spec)
    assert [one["id"] for one in out["layers"]] == [layer.id for layer in spec.layers]
    assert by_id(out, "offer")["name"] == "offer line"
    assert by_id(out, "bg")["kind"] == "photo"
    assert out["canvas"] == {"width": 1080, "height": 1350}


def test_the_words_are_measured_exactly_as_drawn() -> None:
    spec = poster()
    headline = by_id(panel(spec), "headline")
    drawn = place_text(spec.layer("headline"), spec, KIT, REPO)  # type: ignore[arg-type]
    assert headline["lines"] == list(drawn.lines)
    assert headline["size_px"] == pytest.approx(drawn.size_px, abs=0.1)
    ink, box = headline["ink"], headline["px"]
    assert box["x"] - 0.1 <= ink["x"] and ink["x"] + ink["w"] <= box["x"] + box["w"] + 0.1
    assert headline["fits"] > len(headline["text"])


def test_text_says_its_colour_by_name_and_its_contrast() -> None:
    offer = by_id(panel(poster()), "offer")
    assert offer["colour"] == {"token": "palette.text", "hex": "#251e14", "name": "very dark brown"}
    assert offer["contrast"]["behind"]["name"] == "cream"
    assert offer["contrast"]["ok"] is True and offer["contrast"]["ratio"] > 10


def test_low_contrast_is_reported_as_not_ok() -> None:
    gold = apply_ops(poster(), [SetColor(layer="offer", color="palette.secondary")])
    contrast = by_id(panel(gold), "offer")["contrast"]
    assert contrast["ok"] is False and contrast["ratio"] < contrast["needed"]


def test_a_photo_says_its_crop_darkness_and_which_ways_it_moves() -> None:
    photo = by_id(panel(poster()), "bg")
    assert photo["crop"] == {"x": 0.5, "y": 0.5, "zoom": 1.0}
    assert photo["dim"] == 0.0
    assert photo["can_move"] == {"x": False, "y": True}  # a 4:3 photo in a wide band


def test_photo_axes_follow_the_shape_of_photo_and_frame() -> None:
    layer = poster().layer("bg")
    assert isinstance(layer, ImageLayer)
    assert photo_axes(layer, (1080, 1350), (4000, 1000)) == {"x": True, "y": False}
    assert photo_axes(layer, (1080, 1350), None) == {"x": False, "y": False}


def test_the_palette_names_every_colour_and_what_uses_it() -> None:
    palette = {one["token"]: one for one in panel(poster())["palette"]}
    assert palette["palette.surface"]["name"] == "cream"
    assert palette["palette.surface"]["used_by"] == ["colour panel"]
    assert set(palette["palette.text"]["used_by"]) == {"headline", "offer line", "call to action"}
    assert palette["palette.secondary"]["used_by"] == []  # the accent line is hidden


def test_measurements_margins_gaps_and_alignment() -> None:
    out = panel(poster())
    measured = out["measure"]
    headline = by_id(out, "headline")["ink"]
    assert measured["margins"]["headline"]["left"] == pytest.approx(headline["x"])
    first = measured["gaps"][0]
    assert (first["above"], first["below"]) == ("headline", "offer") and first["px"] > 0
    assert [one for one in measured["aligned_left"][0] if one != "logo"] == [
        "headline",
        "offer",
        "cta",
    ]
    assert measured["story_safe"] is None


def test_a_story_says_its_safe_band_and_what_is_outside_it() -> None:
    safe = panel(poster("9:16"))["measure"]["story_safe"]
    assert safe["top_px"] == pytest.approx(0.14 * 1920, abs=0.1)
    assert safe["outside"] == []


def test_style_options_are_dropdowns_with_brand_names() -> None:
    style = style_options(PANEL, KIT, photos=1)
    assert style is not None
    fill_setting = next(one for one in style["settings"] if one["name"] == "fill")
    assert fill_setting["label"] == "Background" and fill_setting["value"] == "surface"
    assert [one["label"] for one in fill_setting["options"]] == [
        "Brown",
        "Cream",
        "Very dark brown",
    ]
    pair = next(one for one in style["designs"] if one["family"] == "pair")
    assert pair["available"] is False
    assert pair["why"] == "Needs 2 of the client's photos; there is 1."
    assert next(one for one in style["designs"] if one["current"])["family"] == "panel"


def test_a_fixed_template_has_no_style_options() -> None:
    assert style_options("full_bleed", KIT, photos=3) is None
