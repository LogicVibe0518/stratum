"""What a control panel shows about a poster (M10d): every layer, exactly.

The chat reads a poster in words; a designer working by hand needs numbers. This is
everything the page needs to draw a Lightroom-style panel and a Measure overlay, for
one size of one version, worked out the same way the renderer and the layout check
work it out, so what the panel says is what the poster is:

- each layer: what it is, where (fractions and pixels), whether it is shown;
  text: the words, the size they render at, the lines, the block of ink, how many
  characters fit, its colour by name, and its contrast against what is behind it;
  shapes: colour and opacity; photos: the crop, how dark, which ways it can move;
- the brand's colours by name, and what uses each one;
- measurements: each text block's and the logo's distance to every edge, the gaps
  between text blocks, which left edges line up, and a story's safe band;
- the design's style settings as dropdowns, and the other designs.

Pure: no database, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from stratum_spec import BrandKit, PosterSpec
from stratum_spec.colour import blend, contrast_ratio, min_contrast_for
from stratum_spec.models import ImageLayer, Layer, LogoLayer, ShapeLayer, TextLayer

from stratum_worker.brand.names import colour_name
from stratum_worker.design import FAMILIES, decode, is_design
from stratum_worker.draft.backdrop import ASSUMED_BACKDROP
from stratum_worker.draft.bindings import binding_for
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP
from stratum_worker.edit.context import friendly, where
from stratum_worker.edit.restyle import behind, option_label, setting_label
from stratum_worker.render.layout import box_px, place_text
from stratum_worker.render.text import load_metrics

# Two left edges this close (in pixels) line up, to the eye.
ALIGNED_PX = 2.0
KIND = {"text": "text", "shape": "shape", "image": "photo", "logo": "logo", "cutout": "cutout"}


def _r(value: float, places: int = 1) -> float:
    return round(float(value), places)


def colour(kit: BrandKit, token: str) -> dict[str, str]:
    value = kit.palette.get(token.removeprefix("palette."), "")
    return {"token": token, "hex": value, "name": colour_name(value) if value else token}


def photo_axes(
    layer: ImageLayer, canvas: tuple[int, int], size: tuple[int, int] | None
) -> dict[str, bool]:
    """Which ways a photo can be moved inside its frame right now."""
    if layer.fit != "cover" or size is None:
        return {"x": False, "y": False}
    if layer.crop is not None and layer.crop.zoom > 1.01:
        return {"x": True, "y": True}
    box_ratio = (layer.box.w * canvas[0]) / max(layer.box.h * canvas[1], 1e-6)
    photo_ratio = size[0] / max(size[1], 1)
    return {"x": photo_ratio > box_ratio * 1.02, "y": photo_ratio < box_ratio / 1.02}


def _ink(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> dict[str, Any]:
    """The words as drawn: size, lines, and the rectangle the ink occupies."""
    placed = place_text(layer, spec, kit, root)
    metrics = load_metrics(str(root / kit.font(layer.font).file))
    width = max((metrics.width(line, placed.size_px) for line in placed.lines), default=0.0)
    x, _, w, _ = placed.box
    left = {"left": x, "center": x + (w - width) / 2, "right": x + w - width}[placed.align]
    return {
        "size_px": _r(placed.size_px),
        "lines": list(placed.lines),
        "ink": {
            "x": _r(left),
            "y": _r(placed.top_px),
            "w": _r(width),
            "h": _r(placed.height_px),
        },
    }


def _contrast(spec: PosterSpec, layer: TextLayer, kit: BrandKit, size_px: float) -> dict[str, Any]:
    _, height = spec.canvas.size
    needed = min_contrast_for(size_px / height)
    backing = behind(spec, layer.id)
    if backing is None:
        return {"ratio": None, "needed": needed, "ok": None, "behind": None}
    shown = blend(kit.color(backing.fill), ASSUMED_BACKDROP, backing.opacity)
    ratio = contrast_ratio(kit.color(layer.color), shown)
    return {
        "ratio": _r(ratio, 2),
        "needed": needed,
        "ok": ratio >= needed,
        "behind": colour(kit, backing.fill),
    }


def _layer(
    layer: Layer,
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    fits: Mapping[str, int],
    photo_sizes: Mapping[str, tuple[int, int]],
) -> dict[str, Any]:
    x, y, w, h = box_px(spec, layer)
    out: dict[str, Any] = {
        "id": layer.id,
        "name": friendly(layer.id),
        "kind": KIND.get(layer.type, layer.type),
        "visible": layer.visible,
        "locked": layer.locked,
        "where": where(layer.box),
        "box": {key: _r(value, 4) for key, value in layer.box.model_dump().items()},
        "px": {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h)},
    }
    if isinstance(layer, TextLayer):
        drawn = _ink(layer, spec, kit, root)
        out |= {
            "text": layer.content,
            "fits": fits.get(layer.id),
            "colour": colour(kit, layer.color),
            "align": layer.align,
            **drawn,
            "contrast": _contrast(spec, layer, kit, drawn["size_px"]),
        }
    elif isinstance(layer, ShapeLayer):
        out |= {"colour": colour(kit, layer.fill), "opacity": layer.opacity}
    elif isinstance(layer, ImageLayer):
        crop = layer.crop
        size = photo_sizes.get(layer.asset) if layer.asset else None
        out |= {
            "asset": layer.asset,
            "fit": layer.fit,
            "crop": {
                "x": crop.x if crop else 0.5,
                "y": crop.y if crop else 0.5,
                "zoom": crop.zoom if crop else 1.0,
            },
            "dim": layer.dim,
            "can_move": photo_axes(layer, spec.canvas.size, size),
        }
    elif isinstance(layer, LogoLayer):
        out |= {"asset": layer.asset}
    return out


def _edges(rect: Mapping[str, float], canvas: tuple[int, int]) -> dict[str, float]:
    width, height = canvas
    return {
        "left": _r(rect["x"]),
        "top": _r(rect["y"]),
        "right": _r(width - rect["x"] - rect["w"]),
        "bottom": _r(height - rect["y"] - rect["h"]),
    }


def measure(layers: list[dict[str, Any]], spec: PosterSpec) -> dict[str, Any]:
    """Distances a designer checks by eye: margins, gaps, alignment, the safe band."""
    canvas = spec.canvas.size
    shown = [one for one in layers if one["visible"]]
    rects = {
        one["id"]: one["ink"] if one["kind"] == "text" else one["px"]
        for one in shown
        if one["kind"] in ("text", "logo")
    }
    margins = {layer_id: _edges(rect, canvas) for layer_id, rect in rects.items()}
    texts = sorted((one for one in shown if one["kind"] == "text"), key=lambda one: one["ink"]["y"])
    gaps = [
        {
            "above": upper["id"],
            "below": lower["id"],
            "px": _r(lower["ink"]["y"] - upper["ink"]["y"] - upper["ink"]["h"]),
        }
        for upper, lower in pairwise(texts)
    ]
    groups: list[list[str]] = []
    for layer_id, rect in sorted(rects.items(), key=lambda item: item[1]["x"]):
        if groups and abs(rects[groups[-1][0]]["x"] - rect["x"]) <= ALIGNED_PX:
            groups[-1].append(layer_id)
        else:
            groups.append([layer_id])
    width, height = canvas
    safe = None
    if spec.canvas.aspect == "9:16":
        top, bottom = STORY_SAFE_TOP * height, STORY_SAFE_BOTTOM * height
        safe = {
            "top_px": _r(top),
            "bottom_px": _r(bottom),
            "outside": [
                layer_id
                for layer_id, rect in rects.items()
                if rect["y"] < top or rect["y"] + rect["h"] > bottom
            ],
        }
    return {
        "short_side_px": min(width, height),
        "margins": margins,
        "gaps": gaps,
        "aligned_left": [group for group in groups if len(group) > 1],
        "story_safe": safe,
    }


def palette(spec: PosterSpec, kit: BrandKit) -> list[dict[str, Any]]:
    """The brand's colours by name, with what on this poster uses each one."""
    users: dict[str, list[str]] = {}
    for layer in spec.layers:
        token = (
            layer.color
            if isinstance(layer, TextLayer)
            else layer.fill
            if isinstance(layer, ShapeLayer)
            else None
        )
        if token and layer.visible:
            users.setdefault(token, []).append(friendly(layer.id))
    return [
        {**colour(kit, f"palette.{name}"), "used_by": users.get(f"palette.{name}", [])}
        for name in kit.palette
    ]


def style_options(template_id: str, kit: BrandKit, photos: int) -> dict[str, Any] | None:
    """The design's settings as dropdowns, and the other designs; None for a fixed
    template. Each option's value is what a restyle sends; its label is for people."""
    if not is_design(template_id):
        return None
    design = decode(template_id)
    family = design.family.name
    settings = [
        {
            "name": name,
            "label": setting_label(family, name),
            "value": design.choice[name],
            "options": [
                {"value": value, "label": option_label(family, name, value, kit)}
                for value in options
            ],
        }
        for name, options in design.family.params
    ]
    designs = []
    for one in FAMILIES.values():
        default = f"ds.{one.name}.1.{'0' * len(one.params)}"
        wanted = list(binding_for(default).photos.values()).count("photo")
        verb = "is" if photos == 1 else "are"
        designs.append(
            {
                "family": one.name,
                "title": one.title,
                "current": one.name == family,
                "available": wanted <= photos,
                "why": None
                if wanted <= photos
                else f"Needs {wanted} of the client's photos; there {verb} {photos}.",
            }
        )
    return {"family": family, "title": design.title, "settings": settings, "designs": designs}


def inspect(
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    *,
    fits: Mapping[str, int],
    photo_sizes: Mapping[str, tuple[int, int]],
) -> dict[str, Any]:
    """One size of one version, everything a control panel draws. `spec` is already
    laid out for that size (retargeted)."""
    width, height = spec.canvas.size
    layers = [_layer(layer, spec, kit, root, fits, photo_sizes) for layer in spec.layers]
    return {
        "aspect": spec.canvas.aspect,
        "canvas": {"width": width, "height": height},
        "layers": layers,
        "palette": palette(spec, kit),
        "measure": measure(layers, spec),
    }


__all__ = ["colour", "inspect", "measure", "palette", "photo_axes", "style_options"]
