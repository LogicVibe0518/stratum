"""Keeping the logo visible without touching the logo.

The promises:
- a logo that already stands out is left exactly as the template designed it;
- one that would vanish gets a light or dark panel, whichever shows more of it, and
  the template's see-through chip goes;
- the decision holds for EVERY size of the post, not just the one first drawn;
- a logo file with its own solid background is left alone;
- when no panel can help, preflight says so as a blocker instead of shipping it;
- across the whole template library, four kinds of logo, every size: the logo shows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import (
    BrandKit,
    LogoLayer,
    LogoPlate,
    PosterSpec,
    load_template,
    load_variants,
    retarget,
)
from stratum_spec.models import Box, Canvas, ImageLayer, ShapeLayer
from stratum_worker.draft.logo_fit import (
    ENOUGH,
    LogoInk,
    check_logos,
    fit_logo,
    logo_backdrop,
    visible_share,
)
from stratum_worker.draft.preflight import check_layout

REPO = Path(__file__).resolve().parents[3]
KOWALSKI = BrandKit.model_validate(
    json.loads((REPO / "clients" / "kowalski-plumbing" / "brand.json").read_text())
)
SHA = "a" * 64
NAVY_ORANGE = LogoInk((("#12264a", 0.88), ("#ff6a00", 0.12)))
BLACK = LogoInk((("#1a1a1a", 1.0),))
CREAM = LogoInk((("#f3ebdd", 1.0),))
WHITE = LogoInk((("#ffffff", 1.0),))
HALF_AND_HALF = LogoInk((("#000000", 0.5), ("#ffffff", 0.5)))


def poster(*, chip: bool = False, band: str = "palette.primary") -> PosterSpec:
    """A photo, a band across the top in `band`, and the logo on it."""
    layers: list[object] = [
        ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1)),
        ShapeLayer(id="band", box=Box(x=0, y=0, w=1, h=0.3), fill=band),
    ]
    if chip:
        layers.append(
            ShapeLayer(
                id="logo_chip",
                box=Box(x=0.6, y=0.05, w=0.35, h=0.15),
                fill="palette.text",
                opacity=0.42,
            )
        )
    layers.append(LogoLayer(id="logo", box=Box(x=0.62, y=0.07, w=0.3, h=0.1), asset=SHA))
    return PosterSpec(template_id="test", canvas=Canvas(aspect="4:5"), layers=layers)  # type: ignore[arg-type]


def logo_of(spec: PosterSpec) -> LogoLayer:
    layer = spec.layer("logo")
    assert isinstance(layer, LogoLayer)
    return layer


def test_how_much_of_a_logo_stands_out_is_measured_by_area() -> None:
    assert visible_share(NAVY_ORANGE, "#ffffff") == pytest.approx(0.88)  # orange is 2.9:1
    assert visible_share(NAVY_ORANGE, "#12264a") == pytest.approx(0.12)
    assert visible_share(WHITE, "#12264a") == 1.0


def test_a_logo_that_already_shows_is_left_as_designed() -> None:
    spec = poster()
    assert fit_logo(spec, KOWALSKI, WHITE, {}, ["4:5"]) == spec


def test_a_navy_logo_on_a_navy_band_gets_a_light_panel() -> None:
    fitted = fit_logo(poster(), KOWALSKI, NAVY_ORANGE, {}, ["4:5"])
    assert logo_of(fitted).plate == LogoPlate(fill="palette.surface", opacity=0.96)
    index = [layer.id for layer in fitted.layers].index("logo")
    assert visible_share(NAVY_ORANGE, logo_backdrop(fitted, index, KOWALSKI)) >= ENOUGH


def test_the_panel_replaces_the_templates_see_through_chip() -> None:
    fitted = fit_logo(poster(chip=True), KOWALSKI, NAVY_ORANGE, {}, ["4:5"])
    assert logo_of(fitted).plate is not None
    assert fitted.layer("logo_chip").visible is False


def test_a_light_logo_on_a_light_page_gets_a_dark_panel() -> None:
    fitted = fit_logo(poster(band="palette.surface"), KOWALSKI, WHITE, {}, ["4:5"])
    assert logo_of(fitted).plate == LogoPlate(fill="palette.text", opacity=0.92)


def test_a_logo_with_its_own_background_is_left_alone() -> None:
    boxed = LogoInk(NAVY_ORANGE.colours, self_backed=True)
    spec = poster()
    assert fit_logo(spec, KOWALSKI, boxed, {}, ["4:5"]) == spec
    assert check_logos(spec, KOWALSKI, {SHA: boxed}) == []


def test_every_size_of_the_post_is_checked_not_just_the_first() -> None:
    # In 4:5 the logo sits on the photo, where navy passes against mid-grey. In the
    # story the template moves it onto the navy band, where it would vanish.
    spec = poster().model_copy(
        update={
            "layers": [
                layer.model_copy(update={"box": Box(x=0.62, y=0.5, w=0.3, h=0.1)})
                if layer.id == "logo"
                else layer
                for layer in poster().layers
            ]
        }
    )
    story = {"9:16": {"logo": {"box": {"x": 0.62, "y": 0.1, "w": 0.3, "h": 0.1}}}}
    black = LogoInk((("#12264a", 1.0),))
    assert fit_logo(spec, KOWALSKI, black, story, ["4:5"]) == spec
    assert logo_of(fit_logo(spec, KOWALSKI, black, story, ["4:5", "9:16"])).plate is not None


def test_when_no_panel_helps_preflight_blocks_it(tmp_path: Path) -> None:
    fitted = fit_logo(poster(), KOWALSKI, HALF_AND_HALF, {}, ["4:5"])
    [(layer_id, share)] = check_logos(fitted, KOWALSKI, {SHA: HALF_AND_HALF})
    assert layer_id == "logo" and share < ENOUGH
    flags = check_layout(fitted, KOWALSKI, root=REPO, logos={SHA: HALF_AND_HALF})
    [flag] = [f for f in flags if f.code == "logo_hard_to_see"]
    assert flag.severity == "blocker"
    assert "hard to see" in flag.message


def test_without_logo_colours_the_check_is_skipped() -> None:
    codes = [f.code for f in check_layout(poster(), KOWALSKI, root=REPO)]
    assert "logo_hard_to_see" not in codes


KITS = {
    path.parent.name: BrandKit.model_validate(json.loads(path.read_text()))
    for path in sorted((REPO / "clients").glob("*/brand.json"))
}
TEMPLATE_FILES = sorted((REPO / "templates").glob("*.json"))


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: p.stem)
@pytest.mark.parametrize(
    "ink", [NAVY_ORANGE, BLACK, CREAM, WHITE], ids=["navy", "black", "cream", "white"]
)
@pytest.mark.parametrize("client", sorted(KITS))
def test_across_the_library_the_logo_always_shows(path: Path, ink: LogoInk, client: str) -> None:
    kit = KITS[client]
    template = load_template(path)
    variants = load_variants(path)
    layers = [
        layer.model_copy(update={"asset": SHA}) if isinstance(layer, LogoLayer) else layer
        for layer in template.layers
    ]
    spec = template.model_copy(update={"layers": layers})
    sizes = ["4:5", "1:1", "9:16", "16:9"]
    fitted = fit_logo(spec, kit, ink, variants, sizes)
    for size in sizes:
        assert check_logos(retarget(fitted, size, variants), kit, {SHA: ink}) == [], size
