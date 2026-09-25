"""Loading template files.

A template is just a PosterSpec with placeholder text and no assets bound yet.
Keeping templates as data (not code) means new layouts ship without a deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

from stratum_spec.migrate import load
from stratum_spec.models import PosterSpec


def load_template(path: Path) -> PosterSpec:
    """Read one template file, upgrading it if it was written by an older schema."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a template object")
    # "aspects" belongs to the template file, not to a poster: one spec is one
    # poster, and the variants are read separately by load_variants().
    return load({key: value for key, value in data.items() if key != "aspects"})


def load_templates(directory: Path) -> dict[str, PosterSpec]:
    """Load every template in a directory, keyed by template_id."""
    templates: dict[str, PosterSpec] = {}
    for path in sorted(directory.glob("*.json")):
        template = load_template(path)
        if template.template_id in templates:
            raise ValueError(f"duplicate template_id {template.template_id!r} in {path}")
        templates[template.template_id] = template
    return templates


# --- per-aspect layout ------------------------------------------------------------
#
# A story is not a cropped feed post. Boxes are fractions, so re-rendering 4:5 at 1:1
# is a free nudge - but at 9:16 the text block has to move, and at 16:9 the whole
# composition turns sideways. A template may therefore carry an "aspects" block of
# per-aspect overrides: one design, deliberate variants, one source of truth.
#
# Overrides may change LAYOUT only. Content and brand are not in the allow-list, so
# no variant can quietly say something different or use a different colour from the
# one the client approved.

LAYOUT_FIELDS = frozenset(
    {
        "box",
        "align",
        "max_lines",
        "min_size",
        "max_size",
        "visible",
        "opacity",
        "fit",
        "dim",
        "blur",
        "radius",
        "safe_area",
        "uppercase",
    }
)

Variants = dict[str, dict[str, dict[str, object]]]


class NotALayoutField(ValueError):
    """An aspect override tried to change something that is not layout."""


def load_variants(path: Path) -> Variants:
    """Read the optional per-aspect overrides from a template file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("aspects") if isinstance(data, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 'aspects' should be an object")
    variants: Variants = {}
    for aspect, layers in raw.items():
        if not isinstance(layers, dict):
            raise ValueError(f"{path}: aspects.{aspect} should be an object")
        clean: dict[str, dict[str, object]] = {}
        for layer_id, fields in layers.items():
            if not isinstance(fields, dict):
                raise ValueError(f"{path}: aspects.{aspect}.{layer_id} should be an object")
            extra = set(fields) - LAYOUT_FIELDS
            if extra:
                raise NotALayoutField(
                    f"{path}: aspects.{aspect}.{layer_id} may not set "
                    f"{', '.join(sorted(extra))} - overrides change layout, not content"
                )
            clean[layer_id] = dict(fields)
        variants[aspect] = clean
    return variants


def retarget(spec: PosterSpec, aspect: str, variants: Variants) -> PosterSpec:
    """Return the same poster laid out for another aspect.

    No model runs, no asset is re-read and not a word changes - which is why shipping
    four formats costs what shipping one costs.
    """
    overrides = variants.get(aspect, {})
    known = {layer.id for layer in spec.layers}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"aspect {aspect} overrides unknown layers: {', '.join(sorted(unknown))}")
    layers = [
        layer.model_validate({**layer.model_dump(), **overrides[layer.id]})
        if layer.id in overrides
        else layer
        for layer in spec.layers
    ]
    return PosterSpec.model_validate(
        {
            **spec.model_dump(),
            "canvas": {
                **spec.canvas.model_dump(),
                "aspect": aspect,
                "base_width": _base_width(spec.canvas.base_width, aspect),
            },
            "layers": [layer.model_dump() for layer in layers],
        }
    )


def _base_width(base_width: int, aspect: str) -> int:
    """Keep the SHORTER side at the template's base width.

    base_width is a width, so a 16:9 poster from a 1080-wide template would come out
    1080x608 - too small to post as a landscape banner. Holding the shorter side
    fixed instead gives 1920x1080, and leaves 4:5, 1:1 and 9:16 exactly as they were.
    """
    w_ratio, h_ratio = (int(part) for part in aspect.split(":"))
    if w_ratio <= h_ratio:
        return base_width
    return round(base_width * w_ratio / h_ratio)
