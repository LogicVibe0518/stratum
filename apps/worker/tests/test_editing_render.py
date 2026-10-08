"""M10i: the first editing set, drawn - fades, mirroring, light and colour, word effects
and badge shapes - in the poster and in the PowerPoint/Canva export.

What a reviewer should be able to repeat back:
- a fade really goes from the photo to what is behind it, eased, at the edge asked;
- a mirrored photo is mirrored, and its subject matte with it (depth stays right);
- black and white is grey, brighter is brighter: the photo file is never changed;
- an outline or shadow is the renderer's own, black or white by contrast when no
  colour is asked;
- a burst and a pill are drawn, and every one of these survives the export, baked
  where PowerPoint has no equivalent;
- the margins align uses are the designs' own margins and the story's safe band.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pptx import Presentation
from stratum_spec import (
    Adjust,
    Box,
    BrandKit,
    Canvas,
    Fade,
    ImageLayer,
    PosterSpec,
    ShapeLayer,
    TextEffect,
    TextLayer,
)
from stratum_spec import ops as spec_ops
from stratum_worker.design import frame
from stratum_worker.draft import preflight
from stratum_worker.edit import chateval
from stratum_worker.export.pptx import export_pptx
from stratum_worker.render import build_svg, render_poster
from stratum_worker.render.svg import effect_colour

REPO = Path(__file__).resolve().parents[3]
RED, WHITE = (220, 30, 30), (255, 255, 255)
PHOTO = "a" * 64


@pytest.fixture
def kit() -> BrandKit:
    return chateval.poster("panel").kit


@pytest.fixture
def photo(tmp_path: Path) -> dict[str, str]:
    """A photo red on the left half, blue on the right."""
    image = Image.new("RGB", (400, 400), RED)
    image.paste((30, 30, 220), (200, 0, 400, 400))
    path = tmp_path / "photo.png"
    image.save(path)
    return {PHOTO: str(path)}


def poster(*layers: object) -> PosterSpec:
    base = ShapeLayer(id="base", box=Box(x=0, y=0, w=1, h=1), fill="palette.surface")
    return PosterSpec(
        template_id="test", canvas=Canvas(aspect="1:1", base_width=256), layers=[base, *layers]
    )


def picture(spec: PosterSpec, kit: BrandKit, assets: dict[str, str]) -> np.ndarray:
    png = render_poster(spec, kit, assets=assets, root=REPO)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float64)


def surface(kit: BrandKit) -> np.ndarray:
    value = kit.palette["surface"].lstrip("#")
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def test_the_margins_are_the_designs_own() -> None:
    assert spec_ops.ALIGN_MARGIN == frame.MARGIN
    assert spec_ops.STORY_SAFE_TOP == preflight.STORY_SAFE_TOP
    assert spec_ops.STORY_SAFE_BOTTOM == preflight.STORY_SAFE_BOTTOM


def test_a_fade_goes_from_the_photo_to_whats_behind_it(
    kit: BrandKit, photo: dict[str, str]
) -> None:
    faded = ImageLayer(
        id="bg", box=Box(x=0, y=0, w=1, h=1), asset=PHOTO, fades=(Fade(edge="bottom", length=0.5),)
    )
    image = picture(poster(faded), kit, photo)
    top, middle, bottom = image[25, 50], image[192, 50], image[254, 50]
    assert np.allclose(top, RED, atol=6)  # the top half is the photo itself
    assert np.allclose(bottom, surface(kit), atol=12)  # the edge is what is behind
    assert middle[1] > top[1] + 20  # in between, blending toward the light surface


def test_a_band_of_colour_fades_too(kit: BrandKit) -> None:
    band = ShapeLayer(
        id="band",
        box=Box(x=0, y=0, w=1, h=1),
        fill="palette.text",
        fades=(Fade(edge="top", length=1.0),),
    )
    image = picture(poster(band), kit, {})
    assert image[2, 128].mean() > image[254, 128].mean() + 100  # light at the top, dark below


def test_a_mirrored_photo_and_its_matte_are_mirrored(kit: BrandKit, photo: dict[str, str]) -> None:
    flipped = ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1), asset=PHOTO, flip=True)
    image = picture(poster(flipped), kit, photo)
    assert image[128, 25][2] > image[128, 25][0]  # blue on the left now
    assert image[128, 230][0] > image[128, 230][2]
    matte = "b" * 64
    with_subject = flipped.model_copy(update={"subject": matte})
    words = TextLayer(id="headline", box=Box(x=0.1, y=0.4, w=0.8, h=0.2), content="Hi", behind="bg")
    svg = build_svg(
        poster(with_subject, words), kit, assets={**photo, matte: photo[PHOTO]}, root=REPO
    )
    assert svg.count("matrix(-1 0 0 1") >= 3  # the photo, its redraw, and the matte


def test_black_and_white_is_grey_and_brighter_is_brighter(
    kit: BrandKit, photo: dict[str, str]
) -> None:
    plain = ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1), asset=PHOTO)
    grey = plain.model_copy(update={"adjust": Adjust(saturation=-1.0)})
    pixel = picture(poster(grey), kit, photo)[128, 50]
    assert np.ptp(pixel) < 4  # r = g = b
    before = picture(poster(plain), kit, photo).mean()
    after = picture(poster(plain.model_copy(update={"adjust": Adjust(brightness=0.3)})), kit, photo)
    assert after.mean() > before + 20
    assert Path(photo[PHOTO]).read_bytes() == Path(photo[PHOTO]).read_bytes()  # untouched


def test_word_effects_are_drawn_and_pick_a_colour_that_stands_out(kit: BrandKit) -> None:
    words = TextLayer(
        id="headline",
        box=Box(x=0.1, y=0.4, w=0.8, h=0.2),
        content="Hello",
        color="palette.surface",
        effect=TextEffect(kind="outline"),
    )
    svg = build_svg(poster(words), kit, root=REPO)
    assert 'paint-order="stroke"' in svg and 'stroke="#000000"' in svg  # dark on light words
    shadow = words.model_copy(
        update={"effect": TextEffect(kind="shadow", color="palette.secondary")}
    )
    svg = build_svg(poster(shadow), kit, root=REPO)
    assert "feDropShadow" in svg and kit.palette["secondary"] in svg
    dark = words.model_copy(update={"color": "palette.text"})
    assert effect_colour(dark, kit) == "#ffffff"


def test_a_burst_and_a_pill_are_drawn(kit: BrandKit) -> None:
    burst = ShapeLayer(
        id="badge", box=Box(x=0.6, y=0.1, w=0.3, h=0.3), shape="burst", fill="palette.secondary"
    )
    pill = ShapeLayer(
        id="pill", box=Box(x=0.1, y=0.7, w=0.6, h=0.1), shape="pill", fill="palette.primary"
    )
    svg = build_svg(poster(burst, pill), kit, root=REPO)
    points = svg.split('<polygon points="')[1].split('"')[0].split()
    assert len(points) == 32  # sixteen points, sixteen notches
    assert 'rx="12.80"' in svg  # a pill's ends are half its height: 0.1 x 256 / 2


def test_the_export_keeps_every_new_look(kit: BrandKit, photo: dict[str, str]) -> None:
    faded = ImageLayer(
        id="bg",
        box=Box(x=0, y=0, w=1, h=0.6),
        asset=PHOTO,
        flip=True,
        adjust=Adjust(saturation=-1.0),
        fades=(Fade(edge="bottom", length=0.4),),
    )
    band = ShapeLayer(
        id="melt",
        box=Box(x=0, y=0.4, w=1, h=0.2),
        fill="palette.surface",
        fades=(Fade(edge="top", length=1.0),),
    )
    burst = ShapeLayer(
        id="badge", box=Box(x=0.6, y=0.1, w=0.3, h=0.3), shape="burst", fill="palette.secondary"
    )
    words = TextLayer(
        id="headline",
        box=Box(x=0.1, y=0.7, w=0.8, h=0.2),
        content="Hello",
        effect=TextEffect(kind="outline"),
    )
    data = export_pptx(poster(faded, band, burst, words), kit, assets=photo, root=REPO)
    slide = Presentation(io.BytesIO(data)).slides[0]
    shapes = {shape.name: shape for shape in slide.shapes}
    baked = Image.open(io.BytesIO(shapes["bg"].image.blob))
    assert baked.mode == "RGBA"  # the fade is in the pixels
    rgb = np.asarray(baked.convert("RGB"), dtype=np.float64)
    assert np.ptp(rgb[10, 10]) < 4  # black and white
    alpha = np.asarray(baked.getchannel("A"))
    assert alpha[2, 10] == 255 and alpha[-1, 10] < 30  # shown at the top, gone at the edge
    assert Image.open(io.BytesIO(shapes["melt"].image.blob)).mode == "RGBA"
    assert shapes["badge"].auto_shape_type is not None
    assert b"<a:ln" in shapes["headline"]._element.xml.encode()
