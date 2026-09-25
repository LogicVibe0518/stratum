"""What `build_spec` must do. Written before the function, on purpose.

Every test here runs with no network, no API key and no mock: build_spec is pure.
If these pass, the heart of M7 is correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from stratum_spec.brand import BrandKit, UnknownToken
from stratum_spec.models import ImageLayer, LogoLayer, PosterSpec, TextLayer
from stratum_spec.templates import load_template
from stratum_worker.draft import (
    MissingAsset,
    MissingCopy,
    UnexpectedLayer,
    build_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PHOTO = "a" * 64
LOGO = "b" * 64

TEXTS = {
    "headline": "20% off boiler servicing",
    "offer": "Book before 31 October. Gas Safe 123456.",
    "cta": "Call 0161 496 0000",
}
ASSETS = {"bg": PHOTO, "logo": LOGO}


@pytest.fixture
def template() -> PosterSpec:
    return load_template(REPO_ROOT / "templates" / "offer_4x5.json")


@pytest.fixture
def kit() -> BrandKit:
    data = json.loads((REPO_ROOT / "brandkits" / "kowalski_plumbing.json").read_text())
    return BrandKit.model_validate(data)


# --- the happy path ---------------------------------------------------------------


def test_text_lands_in_the_right_layer(template: PosterSpec, kit: BrandKit) -> None:
    spec = build_spec(template, kit, TEXTS, ASSETS)
    for layer_id, expected in TEXTS.items():
        found = spec.layer(layer_id)
        assert isinstance(found, TextLayer)
        assert found.content == expected


def test_assets_bind_to_the_right_layers(template: PosterSpec, kit: BrandKit) -> None:
    spec = build_spec(template, kit, TEXTS, ASSETS)
    bg = spec.layer("bg")
    logo = spec.layer("logo")
    assert isinstance(bg, ImageLayer) and bg.asset == PHOTO
    assert isinstance(logo, LogoLayer) and logo.asset == LOGO


def test_no_placeholder_text_survives(template: PosterSpec, kit: BrandKit) -> None:
    """The failure this catches is a poster shipped to a client saying HEADLINE."""
    placeholders = {layer.content for layer in template.layers if isinstance(layer, TextLayer)}
    spec = build_spec(template, kit, TEXTS, ASSETS)
    for layer in spec.layers:
        if isinstance(layer, TextLayer):
            assert layer.content not in placeholders


def test_layer_order_is_preserved(template: PosterSpec, kit: BrandKit) -> None:
    """Order is z-order. Reordering would put the background over the text."""
    spec = build_spec(template, kit, TEXTS, ASSETS)
    assert [layer.id for layer in spec.layers] == [layer.id for layer in template.layers]


def test_brand_kit_version_is_stamped(template: PosterSpec, kit: BrandKit) -> None:
    spec = build_spec(template, kit, TEXTS, ASSETS)
    assert spec.brand_kit_version == kit.version
    assert spec.template_id == template.template_id
    assert spec.template_version == template.template_version


def test_the_template_is_not_modified(template: PosterSpec, kit: BrandKit) -> None:
    before = template.model_dump_json()
    build_spec(template, kit, TEXTS, ASSETS)
    assert template.model_dump_json() == before


def test_colours_stay_tokens(template: PosterSpec, kit: BrandKit) -> None:
    """Resolving a token proves it exists; writing the hex in would destroy the one
    guarantee that no tweak can introduce an off-brand colour."""
    dumped = build_spec(template, kit, TEXTS, ASSETS).model_dump_json()
    for hex_value in kit.palette.values():
        assert hex_value not in dumped


def test_text_passes_through_verbatim(template: PosterSpec, kit: BrandKit) -> None:
    """No casing, no trimming, no truncation here - the renderer owns all three."""
    texts = dict(TEXTS, headline="  lower case with spaces  ")
    spec = build_spec(template, kit, texts, ASSETS)
    found = spec.layer("headline")
    assert isinstance(found, TextLayer)
    assert found.content == "  lower case with spaces  "


# --- aspect ----------------------------------------------------------------------


def test_aspect_override_changes_only_the_canvas(template: PosterSpec, kit: BrandKit) -> None:
    """1:1 is a free re-render of 4:5 because every box is a fraction."""
    spec = build_spec(template, kit, TEXTS, ASSETS, aspect="1:1")
    assert spec.canvas.aspect == "1:1"
    assert spec.canvas.size == (1080, 1080)
    assert [layer.box for layer in spec.layers] == [layer.box for layer in template.layers]


# --- the failures that matter ----------------------------------------------------


def test_missing_text_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    with pytest.raises(MissingCopy, match="cta"):
        build_spec(template, kit, {k: v for k, v in TEXTS.items() if k != "cta"}, ASSETS)


def test_unknown_text_layer_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    """Silently dropping an unknown key means shipping a placeholder."""
    with pytest.raises(UnexpectedLayer, match="subhead"):
        build_spec(template, kit, dict(TEXTS, subhead="extra"), ASSETS)


def test_text_aimed_at_a_non_text_layer_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    with pytest.raises(UnexpectedLayer, match="band"):
        build_spec(template, kit, dict(TEXTS, band="not a text layer"), ASSETS)


def test_unknown_asset_layer_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    with pytest.raises(UnexpectedLayer, match="hero"):
        build_spec(template, kit, TEXTS, dict(ASSETS, hero=PHOTO))


def test_missing_asset_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    with pytest.raises(MissingAsset, match="logo"):
        build_spec(template, kit, TEXTS, {"bg": PHOTO})


def test_unknown_colour_token_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    """A brand kit missing a token the template needs must fail, not render a hole."""
    thin = kit.model_copy(
        update={"palette": {k: v for k, v in kit.palette.items() if k != "text_on_primary"}}
    )
    with pytest.raises(UnknownToken, match="text_on_primary"):
        build_spec(template, thin, TEXTS, ASSETS)


def test_unknown_font_token_is_an_error(template: PosterSpec, kit: BrandKit) -> None:
    thin = kit.model_copy(update={"fonts": {k: v for k, v in kit.fonts.items() if k != "display"}})
    with pytest.raises(UnknownToken, match="display"):
        build_spec(template, thin, TEXTS, ASSETS)


# --- the invariant ---------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    headline=st.text(min_size=1, max_size=120),
    offer=st.text(min_size=1, max_size=200),
    cta=st.text(min_size=1, max_size=80),
)
def test_any_copy_a_model_could_return_still_builds_a_valid_spec(
    headline: str, offer: str, cta: str
) -> None:
    """The one that matters. A model can return anything inside the schema - emoji,
    RTL, control characters, 120 identical spaces. None of it may produce a spec the
    renderer cannot draw. If this passes, no caption can break a poster."""
    spec = build_spec(
        load_template(REPO_ROOT / "templates" / "offer_4x5.json"),
        BrandKit.model_validate(
            json.loads((REPO_ROOT / "brandkits" / "kowalski_plumbing.json").read_text())
        ),
        {"headline": headline, "offer": offer, "cta": cta},
        ASSETS,
    )
    PosterSpec.model_validate(spec.model_dump())
