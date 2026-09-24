"""Spec -> SVG.

SVG is the canonical render target: deterministic, fast to rasterise, and it doubles
as the editable export a designer can open in Figma or Photopea. No model ever writes
this markup; every value comes from the spec or the brand kit.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from xml.sax.saxutils import escape

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

from stratum_worker.render.text import fit, load_metrics

PLACEHOLDER_FILL = "#d8d8d8"
PLACEHOLDER_STROKE = "#b4b4b4"
DIM_FILL = "#000000"
# blur=1.0 means a standard deviation of 4% of the canvas's shorter side.
BLUR_SCALE = 0.04


def _px(spec: PosterSpec, layer: Layer) -> tuple[float, float, float, float]:
    width, height = spec.canvas.size
    box = layer.box
    return box.x * width, box.y * height, box.w * width, box.h * height


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
) -> str:
    x, y, w, h = geom
    if href is None:
        body = _placeholder(x, y, w, h, layer.type)
    else:
        preserve = (
            "xMidYMid slice" if getattr(layer, "fit", "cover") == "cover" else "xMidYMid meet"
        )
        body = (
            f'<image x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'href="{escape(href, {chr(34): "&quot;"})}" preserveAspectRatio="{preserve}"/>'
        )
        if isinstance(layer, ImageLayer) and layer.blur > 0:
            body = _blurred(layer, spec, geom, body)
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


def _text_svg(
    layer: TextLayer, spec: PosterSpec, kit: BrandKit, geom: tuple[float, ...], root: Path
) -> str:
    x, y, w, h = geom
    _, canvas_h = spec.canvas.size
    face = kit.font(layer.font)
    metrics = load_metrics(str(root / face.file))
    content = layer.content.upper() if layer.uppercase else layer.content
    fitted = fit(
        content,
        metrics,
        box_w_px=w,
        box_h_px=h,
        min_px=layer.min_size * canvas_h,
        max_px=layer.max_size * canvas_h,
        max_lines=layer.max_lines,
        line_height=face.line_height,
    )
    anchor = {"left": "start", "center": "middle", "right": "end"}[layer.align]
    anchor_x = {"left": x, "center": x + w / 2, "right": x + w}[layer.align]
    # Vertically centre the block inside its box, then place each baseline.
    top = y + (h - fitted.height_px) / 2
    parts = [
        f'<text font-family="{escape(face.family)}" font-size="{fitted.size_px:.2f}" '
        f'font-weight="{face.weight}" fill="{kit.color(layer.color)}" text-anchor="{anchor}">'
    ]
    for index, line in enumerate(fitted.lines):
        baseline = top + fitted.line_height_px * (index + 0.78)
        parts.append(f'<tspan x="{anchor_x:.2f}" y="{baseline:.2f}">{escape(line)}</tspan>')
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
        geom = _px(spec, layer)
        body.append(_open_group(layer, *geom))
        if isinstance(layer, TextLayer):
            body.append(_text_svg(layer, spec, kit, geom, root))
        elif isinstance(layer, ShapeLayer):
            body.append(_shape_svg(layer, kit, geom))
        else:
            href = assets.get(layer.asset) if layer.asset else None
            body.append(_image_svg(layer, spec, geom, href))
        body.append("</g>")
    body.append("</svg>")
    return "".join(body)
