"""Spec -> SVG.

SVG is the canonical render target: deterministic, fast to rasterise, and it doubles
as the editable export a designer can open in Figma or Photopea. No model ever writes
this markup; every value comes from the spec or the brand kit.
"""

from __future__ import annotations

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

from stratum_worker.render.layout import box_px, place_text, plated

PLACEHOLDER_FILL = "#d8d8d8"
PLACEHOLDER_STROKE = "#b4b4b4"
DIM_FILL = "#000000"
# blur=1.0 means a standard deviation of 4% of the canvas's shorter side.
BLUR_SCALE = 0.04


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
        body = (
            f'<image x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'href="{escape(href, {chr(34): "&quot;"})}" preserveAspectRatio="{preserve}"/>'
        )
        if isinstance(layer, ImageLayer) and layer.blur > 0:
            body = _blurred(layer, spec, geom, body)
        body = plate + body
    if isinstance(layer, ImageLayer) and layer.dim > 0:
        body += (
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{DIM_FILL}" opacity="{layer.dim:.4f}"/>'
        )
    return body


def _shape_svg(layer: ShapeLayer, kit: BrandKit, geom: tuple[float, ...]) -> str:
    x, y, w, h = geom
    fill = kit.color(layer.fill)
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


def _text_svg(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> str:
    placed = place_text(layer, spec, kit, root)
    anchor = {"left": "start", "center": "middle", "right": "end"}[placed.align]
    parts = [
        f'<text font-family="{escape(placed.family)}" font-size="{placed.size_px:.2f}" '
        f'font-weight="{placed.weight}" fill="{placed.color}" text-anchor="{anchor}">'
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
) -> str:
    """Render one spec to an SVG document. `assets` maps a SHA-256 to a file path."""
    assets = assets or {}
    root = root or Path.cwd()
    width, height = spec.canvas.size
    body: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    ]
    for layer in spec.layers:
        if not layer.visible:
            continue
        geom = box_px(spec, layer)
        body.append(_open_group(layer, *geom))
        if isinstance(layer, TextLayer):
            body.append(_text_svg(layer, spec, kit, root))
        elif isinstance(layer, ShapeLayer):
            body.append(_shape_svg(layer, kit, geom))
        else:
            href = assets.get(layer.asset) if layer.asset else None
            body.append(_image_svg(layer, spec, geom, href, kit))
        body.append("</g>")
    body.append("</svg>")
    return "".join(body)
