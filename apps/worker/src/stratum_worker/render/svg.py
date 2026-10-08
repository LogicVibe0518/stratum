"""Spec -> SVG.

SVG is the canonical render target: deterministic, fast to rasterise, and it doubles
as the editable export a designer can open in Figma or Photopea. No model ever writes
this markup; every value comes from the spec or the brand kit.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from stratum_spec import (
    BrandKit,
    CutoutLayer,
    ImageLayer,
    Layer,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextLayer,
)
from stratum_spec.colour import contrast_ratio
from stratum_spec.models import Adjust, Fade

from stratum_worker.render.layout import box_px, crop_window, place_text, plated

PLACEHOLDER_FILL = "#d8d8d8"
PLACEHOLDER_STROKE = "#b4b4b4"
DIM_FILL = "#000000"
# blur=1.0 means a standard deviation of 4% of the canvas's shorter side.
BLUR_SCALE = 0.04
# A fade eases in and out (smoothstep) rather than ramping straight: a straight ramp
# shows a visible band where it starts.
FADE_STOPS = 9
# Warmth moves red up and blue down by this much at warmth 1.
WARM_SHIFT = 0.15
# A burst (offer badge) has this many points, the inner ones this far in.
BURST_POINTS = 16
BURST_INNER = 0.84


def _open_group(layer: Layer, x: float, y: float, w: float, h: float) -> str:
    attrs = [f'id="{escape(layer.id)}"']
    if layer.opacity < 1.0:
        attrs.append(f'opacity="{layer.opacity:.4f}"')
    if layer.box.rotation:
        attrs.append(
            f'transform="rotate({layer.box.rotation:.3f} {x + w / 2:.2f} {y + h / 2:.2f})"'
        )
    return f"<g {' '.join(attrs)}>"


def _placeholder(x: float, y: float, w: float, h: float, label: str) -> str:
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="{PLACEHOLDER_FILL}" stroke="{PLACEHOLDER_STROKE}" stroke-width="2" '
        f'stroke-dasharray="12 8"/>'
        f'<text x="{x + w / 2:.2f}" y="{y + h / 2:.2f}" font-family="Barlow" '
        f'font-size="{max(12.0, min(w, h) * 0.12):.2f}" fill="#7a7a7a" '
        f'text-anchor="middle" dominant-baseline="middle">{escape(label)}</text>'
    )


def _blurred(layer: ImageLayer, spec: PosterSpec, geom: tuple[float, ...], image: str) -> str:
    """Blur the image, clipped to its own box so it never bleeds over neighbours."""
    x, y, w, h = geom
    std_dev = layer.blur * BLUR_SCALE * min(spec.canvas.size)
    # Layer ids cannot contain "-", so these ids never collide with a layer's.
    clip_id, filter_id = f"clip-{layer.id}", f"blur-{layer.id}"
    return (
        f'<defs><clipPath id="{clip_id}"><rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" '
        f'height="{h:.2f}"/></clipPath>'
        f'<filter id="{filter_id}"><feGaussianBlur stdDeviation="{std_dev:.3f}"/></filter></defs>'
        f'<g clip-path="url(#{clip_id})"><g filter="url(#{filter_id})">{image}</g></g>'
    )


def _href(path: str) -> str:
    return escape(path, {chr(34): "&quot;"})


def _framing(
    layer: ImageLayer, geom: tuple[float, ...], size: tuple[int, int]
) -> tuple[float, float, float, float]:
    """Where the whole photo is drawn so that its crop window fills the box exactly."""
    x, y, w, h = geom
    win_x, win_y, win_w, _ = crop_window(size, (w, h), layer.crop)
    scale = w / win_w
    return x - win_x * scale, y - win_y * scale, size[0] * scale, size[1] * scale


def _framed_image(layer: ImageLayer, geom: tuple[float, ...], href: str) -> str:
    """The photo scaled and shifted so its crop window fills the box exactly.

    Drawn whole and clipped by the caller, so the part outside the window is simply
    not visible - the same picture "xMidYMid slice" draws when the crop is the centre.
    """
    with Image.open(href) as image:
        size = image.size
    ix, iy, iw, ih = _framing(layer, geom, size)
    return (
        f'<image x="{ix:.2f}" y="{iy:.2f}" width="{iw:.2f}" height="{ih:.2f}" '
        f'href="{_href(href)}" preserveAspectRatio="none"/>'
    )


def _in_front(text: TextLayer, spec: PosterSpec, assets: Mapping[str, str], kit: BrandKit) -> str:
    """Depth (schema 6): the photo's subject drawn again over these words, so they sit
    behind it, as on a magazine cover. Empty when the words are simply in front.

    The photo is drawn a second time exactly as it was (same box, crop, blur, dimming,
    opacity, rotation), through its subject matte framed the same way. Outside the
    subject the matte is black, so nothing changes there; on the subject, the subject
    covers the words and whatever panel was drawn between them, so the person stands
    in front of the band as well as the letters.
    """
    if text.behind is None:
        return ""
    photo = next((one for one in spec.layers if one.id == text.behind), None)
    if not isinstance(photo, ImageLayer) or not photo.visible or photo.fit != "cover":
        return ""
    picture = assets.get(photo.asset) if photo.asset else None
    matte = assets.get(photo.subject) if photo.subject else None
    if picture is None or matte is None:
        return ""
    with Image.open(picture) as image:
        size = image.size
    geom = box_px(spec, photo)
    x, y, w, h = geom
    ix, iy, iw, ih = _framing(photo, geom, size)
    # A hyphen never appears in a layer id, so these ids never meet the photo's own.
    again = photo.model_copy(update={"id": f"{photo.id}-{text.id}"})
    mask_id = f"subject-{photo.id}-{text.id}"
    attrs = [f'mask="url(#{mask_id})"']
    if photo.opacity < 1.0:
        attrs.append(f'opacity="{photo.opacity:.4f}"')
    if photo.box.rotation:
        attrs.append(
            f'transform="rotate({photo.box.rotation:.3f} {x + w / 2:.2f} {y + h / 2:.2f})"'
        )
    matte_image = (
        f'<image x="{ix:.2f}" y="{iy:.2f}" width="{iw:.2f}" height="{ih:.2f}" '
        f'href="{_href(matte)}" preserveAspectRatio="none"/>'
    )
    if photo.flip:
        matte_image = _mirrored(geom, matte_image)
    return (
        f'<defs><mask id="{mask_id}" maskUnits="userSpaceOnUse" x="{x:.2f}" y="{y:.2f}" '
        f'width="{w:.2f}" height="{h:.2f}">{matte_image}</mask></defs>'
        f"<g {' '.join(attrs)}>{_image_svg(again, spec, geom, picture, kit)}</g>"
    )


def _faded(layer: ImageLayer | ShapeLayer, geom: tuple[float, ...], body: str) -> str:
    """The photo going to nothing at each faded edge: one luminance mask per edge,
    a gradient from white (shown) to black (gone), eased."""
    x, y, w, h = geom
    for fade in layer.fades:
        body = _fade_mask(f"fade-{layer.id}-{fade.edge}", fade, x, y, w, h, body)
    return body


def _fade_mask(mask_id: str, fade: Fade, x: float, y: float, w: float, h: float, body: str) -> str:
    span_w, span_h = fade.length * w, fade.length * h
    # From where the fade starts (fully shown) to the edge (gone).
    x1, y1, x2, y2 = {
        "bottom": (x, y + h - span_h, x, y + h),
        "top": (x, y + span_h, x, y),
        "left": (x + span_w, y, x, y),
        "right": (x + w - span_w, y, x + w, y),
    }[fade.edge]
    stops = []
    for i in range(FADE_STOPS):
        t = i / (FADE_STOPS - 1)
        shown = 1.0 - t * t * (3.0 - 2.0 * t)  # smoothstep from 1 to 0
        stops.append(f'<stop offset="{t:.3f}" stop-color="#ffffff" stop-opacity="{shown:.4f}"/>')
    grad = f"{mask_id}-g"
    return (
        f'<defs><linearGradient id="{grad}" gradientUnits="userSpaceOnUse" x1="{x1:.2f}" '
        f'y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}">{"".join(stops)}</linearGradient>'
        f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="{x:.2f}" y="{y:.2f}" '
        f'width="{w:.2f}" height="{h:.2f}"><rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" '
        f'height="{h:.2f}" fill="url(#{grad})"/></mask></defs>'
        f'<g mask="url(#{mask_id})">{body}</g>'
    )


def _mirrored(geom: tuple[float, ...], body: str) -> str:
    """Mirrored left to right about the middle of its box."""
    x, _, w, _ = geom
    return f'<g transform="matrix(-1 0 0 1 {2 * x + w:.2f} 0)">{body}</g>'


def _adjusted(layer: ImageLayer, adjust: Adjust, geom: tuple[float, ...], body: str) -> str:
    """Light and colour as an SVG filter: the photo file itself never changes."""
    x, y, w, h = geom
    slope = 1.0 + adjust.contrast
    intercept = 0.5 * (1.0 - slope) + adjust.brightness
    warm = WARM_SHIFT * adjust.warmth
    filter_id = f"adjust-{layer.id}"
    channel = '<feFunc{c} type="linear" slope="{s:.4f}" intercept="{i:.4f}"/>'
    funcs = "".join(
        channel.format(c=c, s=slope * (1.0 + shift), i=intercept)
        for c, shift in (("R", warm), ("G", 0.0), ("B", -warm))
    )
    return (
        f'<defs><filter id="{filter_id}" filterUnits="userSpaceOnUse" x="{x:.2f}" '
        f'y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" color-interpolation-filters="sRGB">'
        f'<feColorMatrix type="saturate" values="{1.0 + adjust.saturation:.4f}"/>'
        f"<feComponentTransfer>{funcs}</feComponentTransfer></filter></defs>"
        f'<g filter="url(#{filter_id})">{body}</g>'
    )


def _image_svg(
    layer: ImageLayer | CutoutLayer | LogoLayer,
    spec: PosterSpec,
    geom: tuple[float, ...],
    href: str | None,
    kit: BrandKit,
) -> str:
    x, y, w, h = geom
    plate = ""
    if href is not None and isinstance(layer, LogoLayer) and layer.plate is not None:
        # The panel first, then the logo drawn exactly inside it.
        with Image.open(href) as image:
            size = image.size
        (x, y, w, h), (px, py, pw, ph), radius = plated(geom, size, layer.plate)  # type: ignore[arg-type]
        plate = (
            f'<rect x="{px:.2f}" y="{py:.2f}" width="{pw:.2f}" height="{ph:.2f}" '
            f'rx="{radius:.2f}" fill="{kit.color(layer.plate.fill)}" '
            f'fill-opacity="{layer.plate.opacity:.4f}"/>'
        )
    if href is None:
        body = _placeholder(x, y, w, h, layer.type)
    else:
        # A photo may be cropped to fill its box. A logo or a cutout may NOT: it is
        # the client's own artwork, and a logo with its first letter sliced off is
        # the most visible way there is to look amateur. `getattr(layer, "fit")`
        # used to default logos to "cover" - it shipped a cropped logo on the very
        # first real poster, which is why this is now spelled out by type.
        preserve = (
            "xMidYMid slice"
            if isinstance(layer, ImageLayer) and layer.fit == "cover"
            else "xMidYMid meet"
        )
        framed = (
            layer
            if isinstance(layer, ImageLayer) and layer.fit == "cover" and layer.crop is not None
            else None
        )
        if framed is not None:
            body = _framed_image(framed, geom, href)
        else:
            body = (
                f'<image x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                f'href="{_href(href)}" preserveAspectRatio="{preserve}"/>'
            )
        if isinstance(layer, ImageLayer) and layer.flip:
            body = _mirrored(geom, body)
        if isinstance(layer, ImageLayer) and layer.adjust is not None:
            body = _adjusted(layer, layer.adjust, geom, body)
        if isinstance(layer, ImageLayer) and layer.blur > 0:
            body = _blurred(layer, spec, geom, body)
        elif framed is not None:
            clip_id = f"crop-{layer.id}"  # layer ids cannot contain "-": never collides
            body = (
                f'<defs><clipPath id="{clip_id}"><rect x="{x:.2f}" y="{y:.2f}" '
                f'width="{w:.2f}" height="{h:.2f}"/></clipPath></defs>'
                f'<g clip-path="url(#{clip_id})">{body}</g>'
            )
        body = plate + body
    if isinstance(layer, ImageLayer) and layer.dim > 0:
        body += (
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{DIM_FILL}" opacity="{layer.dim:.4f}"/>'
        )
    if isinstance(layer, ImageLayer) and layer.fades:
        # The darkening fades with the photo, or a grey edge would show where it went.
        body = _faded(layer, geom, body)
    return body


def _burst(x: float, y: float, w: float, h: float) -> str:
    """A starburst inside the box: points alternate between the edge and BURST_INNER."""
    cx, cy, rx, ry = x + w / 2, y + h / 2, w / 2, h / 2
    points = []
    for i in range(2 * BURST_POINTS):
        reach = 1.0 if i % 2 == 0 else BURST_INNER
        angle = math.pi * i / BURST_POINTS - math.pi / 2
        px, py = cx + rx * reach * math.cos(angle), cy + ry * reach * math.sin(angle)
        points.append(f"{px:.2f},{py:.2f}")
    return " ".join(points)


def _shape_svg(layer: ShapeLayer, kit: BrandKit, geom: tuple[float, ...]) -> str:
    x, y, w, h = geom
    fill = kit.color(layer.fill)
    if layer.shape == "burst":
        return f'<polygon points="{_burst(x, y, w, h)}" fill="{fill}"/>'
    if layer.shape == "pill":
        return (
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{min(w, h) / 2:.2f}" fill="{fill}"/>'
        )
    if layer.shape == "ellipse":
        return (
            f'<ellipse cx="{x + w / 2:.2f}" cy="{y + h / 2:.2f}" rx="{w / 2:.2f}" '
            f'ry="{h / 2:.2f}" fill="{fill}"/>'
        )
    radius = layer.radius * min(w, h) if layer.shape == "rounded" else 0.0
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'rx="{radius:.2f}" fill="{fill}"/>'
    )


def effect_colour(layer: TextLayer, kit: BrandKit) -> str:
    """The shadow or outline colour: the one asked for, else black or white, whichever
    stands out more from the words themselves."""
    effect = layer.effect
    if effect is not None and effect.color is not None:
        return kit.color(effect.color)
    ink = kit.color(layer.color)
    darker = contrast_ratio(ink, "#000000") >= contrast_ratio(ink, "#ffffff")
    return "#000000" if darker else "#ffffff"


def _effect(layer: TextLayer, kit: BrandKit, size_px: float) -> tuple[str, str]:
    """(defs, attributes) for the text's shadow or outline; empty for none."""
    effect = layer.effect
    if effect is None:
        return "", ""
    colour = effect_colour(layer, kit)
    if effect.kind == "outline":
        width = size_px * 0.1 * effect.strength
        return "", (
            f' stroke="{colour}" stroke-width="{width:.2f}" stroke-linejoin="round" '
            f'paint-order="stroke"'
        )
    filter_id = f"shadow-{layer.id}"
    offset, spread = size_px * 0.04, size_px * 0.08 * effect.strength
    opacity = 0.35 + 0.5 * effect.strength
    return (
        f'<defs><filter id="{filter_id}" x="-20%" y="-20%" width="140%" height="160%">'
        f'<feDropShadow dx="0" dy="{offset:.2f}" stdDeviation="{spread:.2f}" '
        f'flood-color="{colour}" flood-opacity="{opacity:.3f}"/></filter></defs>',
        f' filter="url(#{filter_id})"',
    )


def _text_svg(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> str:
    placed = place_text(layer, spec, kit, root)
    anchor = {"left": "start", "center": "middle", "right": "end"}[placed.align]
    defs, extra = _effect(layer, kit, placed.size_px)
    parts = [
        defs,
        f'<text font-family="{escape(placed.family)}" font-size="{placed.size_px:.2f}" '
        f'font-weight="{placed.weight}" fill="{placed.color}" text-anchor="{anchor}"{extra}>',
    ]
    for index, line in enumerate(placed.lines):
        x, y = placed.anchor_x, placed.baseline(index)
        parts.append(f'<tspan x="{x:.2f}" y="{y:.2f}">{escape(line)}</tspan>')
    parts.append("</text>")
    return "".join(parts)


def build_svg(
    spec: PosterSpec,
    kit: BrandKit,
    *,
    assets: Mapping[str, str] | None = None,
    root: Path | None = None,
    width: int | None = None,
) -> str:
    """Render one spec to an SVG document. `assets` maps a SHA-256 to a file path.

    `width` draws the whole poster smaller (the height follows): the layout is the
    poster's own, only the picture is scaled. For quick looks, never for output.
    """
    assets = assets or {}
    root = root or Path.cwd()
    canvas_w, canvas_h = spec.canvas.size
    out_w = width or canvas_w
    out_h = max(1, round(out_w * canvas_h / canvas_w))
    body: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{out_w}" height="{out_h}" viewBox="0 0 {canvas_w} {canvas_h}">'
    ]
    for layer in spec.layers:
        if not layer.visible:
            continue
        geom = box_px(spec, layer)
        body.append(_open_group(layer, *geom))
        if isinstance(layer, TextLayer):
            body.append(_text_svg(layer, spec, kit, root))
        elif isinstance(layer, ShapeLayer):
            shape = _shape_svg(layer, kit, geom)
            body.append(_faded(layer, geom, shape) if layer.fades else shape)
        else:
            href = assets.get(layer.asset) if layer.asset else None
            body.append(_image_svg(layer, spec, geom, href, kit))
        body.append("</g>")
        if isinstance(layer, TextLayer):
            body.append(_in_front(layer, spec, assets, kit))
    body.append("</svg>")
    return "".join(body)
