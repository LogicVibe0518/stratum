"""A poster as a PowerPoint file whose layers stay editable, for finishing in Canva.

The agency's team already works in Canva. Handing them a flat PNG would throw away
the one thing Stratum has that an image model does not: every word, shape and logo
is a separate object. Canva imports .pptx with each object intact, so this writes
one slide per poster where each layer of the spec becomes one PowerPoint object,
in the same stacking order and named after the layer:

    image    -> a picture, cropped exactly as the poster crops it ("cover"), or
                fitted inside its box ("contain"); a blur is baked into the pixels,
                a dim is a separate black rectangle so it can be deleted
    cutout   -> a picture, never cropped, transparency kept
    logo     -> the client's original file, never cropped, never recoloured
    shape    -> a native rectangle, rounded rectangle or oval in the brand colour
    text     -> a text box at the size and line breaks the renderer chose, so the
                file opens looking like the approved poster and stays editable

Geometry and text fitting come from render/layout.py, the code the renderer uses.
Pixels map to EMU at 96 dpi and to points at 0.75 pt per pixel, which is how Canva
turns a PowerPoint page back into pixels.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Pt
from stratum_spec import (
    BrandKit,
    CutoutLayer,
    ImageLayer,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextLayer,
)

from stratum_worker.render.layout import box_px, contained, crop_window, place_text, plated
from stratum_worker.render.svg import BLUR_SCALE, DIM_FILL, PLACEHOLDER_FILL

EMU_PER_PX = 9525  # 914400 EMU per inch / 96 px per inch
PT_PER_PX = 0.75  # 72 pt per inch / 96 px per inch
# Photos are kept at up to twice the size they are shown: sharp on a retina screen,
# without a 40 MB file for a 1080 px post.
MAX_SCALE = 2.0
JPEG_QUALITY = 90
ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
SHAPES = {
    "rect": MSO_SHAPE.RECTANGLE,
    "rounded": MSO_SHAPE.ROUNDED_RECTANGLE,
    "ellipse": MSO_SHAPE.OVAL,
}
AnyImage = ImageLayer | CutoutLayer | LogoLayer
Geom = tuple[float, float, float, float]


class ExportError(ValueError):
    """The spec cannot be written as a PowerPoint file; the message says why."""


def _emu(px: float) -> Emu:
    return Emu(round(px * EMU_PER_PX))


def _rgb(hex_colour: str) -> RGBColor:
    digits = hex_colour.lstrip("#")
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    # python-pptx 1.0 ships annotations, but not on RGBColor's constructor.
    return RGBColor(red, green, blue)  # type: ignore[no-untyped-call]


def _child(parent: Any, tag: str, **attrs: str) -> None:
    element = OxmlElement(tag)
    for name, value in attrs.items():
        element.set(name, value)
    parent.append(element)


def _alpha(element: Any, opacity: float) -> None:
    """Make the first solid colour under `element` translucent. 100000 is opaque."""
    if opacity >= 1.0:
        return
    colour = element.find(f".//{qn('a:srgbClr')}")
    if colour is not None:
        _child(colour, "a:alpha", val=str(round(opacity * 100000)))


def _picture_alpha(picture: Any, opacity: float) -> None:
    if opacity >= 1.0:
        return
    blip = picture._element.find(f".//{qn('a:blip')}")
    if blip is not None:
        _child(blip, "a:alphaModFix", amt=str(round(opacity * 100000)))


def _plain(shape: Any) -> None:
    """No outline, no shadow, no theme styling: the shape is exactly its fill.

    A new PowerPoint shape carries a <p:style> pointing at the theme's line, fill and
    shadow. Importers apply it differently (LibreOffice drew a drop shadow under the
    brand rule), so it is removed and every property the poster has is set directly.
    """
    shape.line.fill.background()
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def _encode(image: Image.Image) -> bytes:
    """PNG when there is transparency to keep, JPEG otherwise (about a tenth the size)."""
    buffer = io.BytesIO()
    transparent = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if transparent:
        image.convert("RGBA").save(buffer, "PNG", optimize=True)
    else:
        image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _limit(image: Image.Image, shown_w: float, shown_h: float) -> Image.Image:
    """Shrink an image that is far larger than it will ever be shown."""
    ratio = min(1.0, shown_w * MAX_SCALE / image.width, shown_h * MAX_SCALE / image.height)
    if ratio >= 1.0:
        return image
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _cover(slide: Any, layer: ImageLayer, spec: PosterSpec, image: Image.Image, geom: Geom) -> Any:
    """Fill the box, trimming the photo as the poster does: its crop, or evenly."""
    x, y, w, h = geom
    window = crop_window(image.size, (w, h), layer.crop) if layer.crop is not None else None
    scale = max(w / image.width, h / image.height) if window is None else w / window[2]
    if layer.blur > 0:
        # The renderer blurs at canvas scale; the same blur measured in source pixels.
        sigma = layer.blur * BLUR_SCALE * min(spec.canvas.size) / scale
        image = image.convert("RGB").filter(ImageFilter.GaussianBlur(sigma))
    source_size = image.size
    image = _limit(image, image.width * scale, image.height * scale)
    picture = slide.shapes.add_picture(
        io.BytesIO(_encode(image)), _emu(x), _emu(y), _emu(w), _emu(h)
    )
    if window is not None:
        # The crop window, as the share of the photo trimmed from each side. Shares,
        # not pixels, so the shrink `_limit` may have applied does not matter.
        (win_x, win_y, win_w, win_h), (full_w, full_h) = window, source_size
        picture.crop_left = win_x / full_w
        picture.crop_right = 1 - (win_x + win_w) / full_w
        picture.crop_top = win_y / full_h
        picture.crop_bottom = 1 - (win_y + win_h) / full_h
        return picture
    image_ratio, box_ratio = image.width / image.height, w / h
    if image_ratio > box_ratio:  # wider than the box: trim the sides
        side = (1 - box_ratio / image_ratio) / 2
        picture.crop_left = picture.crop_right = side
    elif image_ratio < box_ratio:  # taller than the box: trim top and bottom
        side = (1 - image_ratio / box_ratio) / 2
        picture.crop_top = picture.crop_bottom = side
    return picture


def _contain(slide: Any, image: Image.Image, geom: Geom) -> Any:
    """The whole image, as large as fits, centred in the box. Logos and cutouts always."""
    x, y, shown_w, shown_h = contained(geom, image.size)
    image = _limit(image, shown_w, shown_h)
    return slide.shapes.add_picture(
        io.BytesIO(_encode(image)), _emu(x), _emu(y), _emu(shown_w), _emu(shown_h)
    )


def _rectangle(slide: Any, geom: Geom, colour: str, opacity: float = 1.0) -> Any:
    x, y, w, h = geom
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _emu(x), _emu(y), _emu(w), _emu(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(colour)
    _alpha(shape._element.spPr, opacity)
    _plain(shape)
    return shape


def _plate(slide: Any, layer: LogoLayer, kit: BrandKit, panel: Geom, radius: float) -> Any:
    """The panel behind a logo, as its own shape so a designer can restyle or delete it."""
    assert layer.plate is not None
    x, y, w, h = panel
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, _emu(x), _emu(y), _emu(w), _emu(h))
    shape.adjustments[0] = min(0.5, radius / min(w, h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(kit.color(layer.plate.fill))
    _alpha(shape._element.spPr, layer.plate.opacity * layer.opacity)
    _plain(shape)
    shape.name = f"{layer.id} plate"
    return shape


def _image_layer(
    slide: Any,
    layer: AnyImage,
    spec: PosterSpec,
    kit: BrandKit,
    assets: Mapping[str, str],
    geom: Geom,
) -> list[Any]:
    path = assets.get(layer.asset) if layer.asset else None
    if path is None:
        # An empty photo slot stays visible as a grey box, as on the poster.
        placeholder = _rectangle(slide, geom, PLACEHOLDER_FILL)
        placeholder.name = f"{layer.id} (empty {layer.type})"
        return [placeholder]
    with Image.open(path) as source:
        source.load()
        image = source.copy()
    made: list[Any] = []
    if isinstance(layer, LogoLayer) and layer.plate is not None:
        geom, panel, radius = plated(geom, image.size, layer.plate)
        made.append(_plate(slide, layer, kit, panel, radius))
    if isinstance(layer, ImageLayer) and layer.fit == "cover":
        picture = _cover(slide, layer, spec, image, geom)
    else:
        picture = _contain(slide, image, geom)
    picture.name = layer.id
    _picture_alpha(picture, layer.opacity)
    made.append(picture)
    if isinstance(layer, ImageLayer) and layer.dim > 0:
        dim = _rectangle(slide, geom, DIM_FILL, layer.dim * layer.opacity)
        dim.name = f"{layer.id} dim"
        made.append(dim)
    return made


def _shape_layer(slide: Any, layer: ShapeLayer, kit: BrandKit, geom: Geom) -> Any:
    x, y, w, h = geom
    shape = slide.shapes.add_shape(SHAPES[layer.shape], _emu(x), _emu(y), _emu(w), _emu(h))
    if layer.shape == "rounded":
        # Both formats measure the corner as a fraction of the shorter side.
        shape.adjustments[0] = min(0.5, layer.radius)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(kit.color(layer.fill))
    _alpha(shape._element.spPr, layer.opacity)
    _plain(shape)
    shape.name = layer.id
    return shape


def _text_layer(slide: Any, layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> Any:
    placed = place_text(layer, spec, kit, root)
    x, y, w, h = placed.box
    box = slide.shapes.add_textbox(_emu(x), _emu(y), _emu(w), _emu(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = Emu(0)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = ALIGN[placed.align]
    paragraph.line_spacing = Pt(round(placed.line_height_px * PT_PER_PX, 2))
    # One paragraph, with the renderer's own line breaks: it opens exactly as the
    # poster was drawn, and a designer editing it gets normal reflow from there.
    for index, line in enumerate(placed.lines):
        if index:
            paragraph.add_line_break()
        run = paragraph.add_run()
        run.text = line
        font = run.font
        font.name = placed.family
        font.size = Pt(round(placed.size_px * PT_PER_PX, 2))  # PowerPoint keeps 1/100 pt
        font.bold = placed.weight >= 600
        font.color.rgb = _rgb(placed.color)
        _alpha(run._r, layer.opacity)
    box.name = layer.id
    return box


def export_pptx(
    spec: PosterSpec,
    kit: BrandKit,
    *,
    assets: Mapping[str, str] | None = None,
    root: Path | None = None,
    title: str = "",
    notes: str = "",
) -> bytes:
    """One poster as a one-slide .pptx, a PowerPoint object per visible layer.

    `assets` maps a SHA-256 to a local file, as for the renderer. `notes` becomes
    the slide's speaker notes, where Canva shows it beside the design.
    """
    assets = assets or {}
    root = root or Path.cwd()
    width, height = spec.canvas.size
    deck = Presentation()
    deck.slide_width, deck.slide_height = _emu(width), _emu(height)
    deck.core_properties.title = title[:200]
    deck.core_properties.author = "Stratum"
    slide = deck.slides.add_slide(deck.slide_layouts[6])  # the blank layout
    for layer in spec.layers:
        if not layer.visible:
            continue
        geom = box_px(spec, layer)
        made: list[Any]
        if isinstance(layer, TextLayer):
            made = [_text_layer(slide, layer, spec, kit, root)]
        elif isinstance(layer, ShapeLayer):
            made = [_shape_layer(slide, layer, kit, geom)]
        elif isinstance(layer, ImageLayer | CutoutLayer | LogoLayer):
            made = _image_layer(slide, layer, spec, kit, assets, geom)
        else:  # pragma: no cover - the spec's union is closed
            raise ExportError(f"no PowerPoint mapping for layer type {layer.type!r}")
        for shape in made:
            shape.rotation = layer.box.rotation
    if notes:
        slide.notes_slide.notes_text_frame.text = notes
    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()
