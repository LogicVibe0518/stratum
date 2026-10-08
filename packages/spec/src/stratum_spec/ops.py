"""Edit operations: the ONLY way a spec ever changes.

A designer's click, a typed tweak and a voice command all end up here, so the
same validation protects every path. A model may propose ops; it can never
write a spec directly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from stratum_spec.models import (
    Adjust,
    AssetRef,
    Box,
    Canvas,
    ColorToken,
    Crop,
    CutoutLayer,
    Edge,
    Fade,
    FontToken,
    ImageLayer,
    LayerId,
    LayoutOverride,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextEffect,
    TextLayer,
)

MIN_SCALE = 0.2
MAX_SCALE = 5.0
MAX_ZOOM = 3.0  # the same limit as Crop.zoom
MAX_DIM = 0.8  # darker than this and the photo is no longer a photo
MIN_OPACITY = 0.1  # fainter than this is hidden: use set_visible
# Where "align left" puts a part: the margin every design keeps, as a share of the
# shorter side (design/frame.MARGIN), and in a story, inside the band Instagram
# leaves clear (draft/preflight.STORY_SAFE_*). A test keeps these equal to those.
ALIGN_MARGIN = 0.074
STORY_SAFE_TOP = 0.14
STORY_SAFE_BOTTOM = 0.80


class OpError(ValueError):
    """An op that cannot be applied: unknown layer, wrong layer type, locked."""


class BaseOp(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SetText(BaseOp):
    op: Literal["set_text"] = "set_text"
    layer: LayerId
    text: str = Field(min_length=1, max_length=500)


class SetColor(BaseOp):
    op: Literal["set_color"] = "set_color"
    layer: LayerId
    color: ColorToken


class Move(BaseOp):
    op: Literal["move"] = "move"
    layer: LayerId
    dx: float = Field(ge=-1.0, le=1.0)
    dy: float = Field(ge=-1.0, le=1.0)


class Scale(BaseOp):
    op: Literal["scale"] = "scale"
    layer: LayerId
    factor: float = Field(gt=0.0, le=MAX_SCALE)


class SwapAsset(BaseOp):
    op: Literal["swap_asset"] = "swap_asset"
    layer: LayerId
    asset: AssetRef


class SetVisible(BaseOp):
    op: Literal["set_visible"] = "set_visible"
    layer: LayerId
    visible: bool


class Pan(BaseOp):
    """Move a photo inside its frame, like dragging it in Lightroom's crop tool.

    dx, dy are fractions of the photo: positive dx moves the picture right (showing
    more of its left side), positive dy moves it down (showing more of its top).
    """

    op: Literal["pan"] = "pan"
    layer: LayerId
    dx: float = Field(ge=-1.0, le=1.0)
    dy: float = Field(ge=-1.0, le=1.0)


class Zoom(BaseOp):
    """Zoom a photo inside its frame: above 1 shows less of it, larger."""

    op: Literal["zoom"] = "zoom"
    layer: LayerId
    factor: float = Field(gt=0.0, le=MAX_ZOOM)


class Dim(BaseOp):
    """Darken a photo (0 = as taken), usually so the words on it read."""

    op: Literal["dim"] = "dim"
    layer: LayerId
    amount: float = Field(ge=0.0, le=MAX_DIM)


class SetFade(BaseOp):
    """Fade a photo, or a band of colour, into what is behind it at one edge; length 0
    takes the fade away."""

    op: Literal["fade"] = "fade"
    layer: LayerId
    edge: Edge
    length: float = Field(ge=0.0, le=1.0)


class Flip(BaseOp):
    """Mirror a photo left to right (again to undo): a person then faces the words."""

    op: Literal["flip"] = "flip"
    layer: LayerId


class AdjustPhoto(BaseOp):
    """Set a photo's light and colour; a field left out keeps its value. 0 is as taken."""

    op: Literal["adjust"] = "adjust"
    layer: LayerId
    brightness: float | None = Field(default=None, ge=-0.4, le=0.4)
    contrast: float | None = Field(default=None, ge=-0.4, le=0.4)
    saturation: float | None = Field(default=None, ge=-1.0, le=0.5)
    warmth: float | None = Field(default=None, ge=-0.4, le=0.4)


class SetAlign(BaseOp):
    """Line the words up left, centred or right, inside their own box."""

    op: Literal["set_align"] = "set_align"
    layer: LayerId
    align: Literal["left", "center", "right"]


class SetFont(BaseOp):
    """Another of the brand's own fonts (the service checks the brand has it)."""

    op: Literal["set_font"] = "set_font"
    layer: LayerId
    font: FontToken


class SetCase(BaseOp):
    op: Literal["set_case"] = "set_case"
    layer: LayerId
    upper: bool


class SetEffect(BaseOp):
    """A shadow or an outline behind the words, or none."""

    op: Literal["text_effect"] = "text_effect"
    layer: LayerId
    kind: Literal["shadow", "outline", "none"]
    color: ColorToken | None = None
    strength: float = Field(default=0.5, gt=0.0, le=1.0)


class Reorder(BaseOp):
    """In front of or behind the other parts."""

    op: Literal["reorder"] = "reorder"
    layer: LayerId
    to: Literal["front", "back", "forward", "backward"]


class SetOpacity(BaseOp):
    op: Literal["set_opacity"] = "set_opacity"
    layer: LayerId
    opacity: float = Field(ge=MIN_OPACITY, le=1.0)


class Align(BaseOp):
    """Put a part against the margin (left, right, top, bottom) or in the middle, in
    every size: each size works out its own position."""

    op: Literal["align"] = "align"
    layer: LayerId
    x: Literal["left", "center", "right"] | None = None
    y: Literal["top", "middle", "bottom"] | None = None


class NeedsHuman(BaseOp):
    """The request was understood but is not mechanical ("make it pop")."""

    op: Literal["needs_human"] = "needs_human"
    reason: str = Field(min_length=1, max_length=200)


EditOp = Annotated[
    SetText
    | SetColor
    | Move
    | Scale
    | SwapAsset
    | SetVisible
    | Pan
    | Zoom
    | Dim
    | SetFade
    | Flip
    | AdjustPhoto
    | SetAlign
    | SetFont
    | SetCase
    | SetEffect
    | Reorder
    | SetOpacity
    | Align
    | NeedsHuman,
    Field(discriminator="op"),
]

_ASSET_LAYERS = (ImageLayer, CutoutLayer, LogoLayer)
_COLOR_LAYERS = (ShapeLayer, TextLayer)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _in_front_of(spec: PosterSpec, photo_id: str) -> PosterSpec:
    """Every text that went behind this photo's subject, back in front."""
    layers = [
        layer.model_copy(update={"behind": None})
        if isinstance(layer, TextLayer) and layer.behind == photo_id
        else layer
        for layer in spec.layers
    ]
    return spec.model_copy(update={"layers": layers})


def _replace(
    spec: PosterSpec,
    layer_id: str,
    *,
    sized: Callable[[LayoutOverride], LayoutOverride] | None = None,
    **changes: object,
) -> PosterSpec:
    """Change one layer; `sized` makes the same change in every other size's layout.

    Without it, "make the logo bigger" changed the base size only: every size that
    lays the logo out differently kept the old logo.
    """
    target = spec.layer(layer_id)  # raises KeyError if missing
    if target.locked:
        raise OpError(f"layer {layer_id!r} is locked")
    updated = target.model_copy(update=changes)
    layers = [updated if candidate.id == layer_id else candidate for candidate in spec.layers]
    variants = spec.variants
    if sized is not None:
        variants = {
            aspect: {
                one: sized(override) if one == layer_id else override
                for one, override in overrides.items()
            }
            for aspect, overrides in spec.variants.items()
        }
    return spec.model_copy(update={"layers": layers, "variants": variants})


def _moved(box: Box, dx: float, dy: float) -> Box:
    return box.model_copy(
        update={"x": _clamp(box.x + dx, -1.0, 2.0), "y": _clamp(box.y + dy, -1.0, 2.0)}
    )


def _scaled(box: Box, factor: float) -> Box:
    """Scale about the box centre, so "bigger" does not drift to the corner."""
    new_w = _clamp(box.w * factor, 0.01, 3.0)
    new_h = _clamp(box.h * factor, 0.01, 3.0)
    return box.model_copy(
        update={
            "w": new_w,
            "h": new_h,
            "x": _clamp(box.x - (new_w - box.w) / 2, -1.0, 2.0),
            "y": _clamp(box.y - (new_h - box.h) / 2, -1.0, 2.0),
        }
    )


def _override_box(change: Callable[[Box], Box]) -> Callable[[LayoutOverride], LayoutOverride]:
    def apply(override: LayoutOverride) -> LayoutOverride:
        if override.box is None:
            return override  # this size uses the base box, which already changed
        return override.model_copy(update={"box": change(override.box)})

    return apply


def _uncropped(override: LayoutOverride) -> LayoutOverride:
    return override if override.crop is None else override.model_copy(update={"crop": None})


def _override_crop(change: Callable[[Crop], Crop]) -> Callable[[LayoutOverride], LayoutOverride]:
    """The same change to every size that frames the photo its own way."""

    def apply(override: LayoutOverride) -> LayoutOverride:
        if override.crop is None:
            return override  # this size uses the base crop, which already changed
        return override.model_copy(update={"crop": change(override.crop)})

    return apply


def _panned(crop: Crop, dx: float, dy: float) -> Crop:
    # The picture moves one way, so the window onto it moves the other.
    return crop.model_copy(
        update={"x": _clamp(crop.x - dx, 0.0, 1.0), "y": _clamp(crop.y - dy, 0.0, 1.0)}
    )


def _zoomed(crop: Crop, factor: float) -> Crop:
    return crop.model_copy(update={"zoom": _clamp(crop.zoom * factor, 1.0, MAX_ZOOM)})


def _photo(target: object, layer_id: str) -> ImageLayer:
    if not isinstance(target, ImageLayer):
        raise OpError(f"layer {layer_id!r} is not a photo")
    return target


def _text(target: object, layer_id: str) -> TextLayer:
    if not isinstance(target, TextLayer):
        raise OpError(f"layer {layer_id!r} is not a text layer")
    return target


def _faded(fades: list[Fade], edge: str, length: float) -> list[Fade]:
    kept = [one for one in fades if one.edge != edge]
    return [*kept, Fade(edge=edge, length=length)] if length > 0 else kept  # type: ignore[arg-type]


def _following(field: str, value: object) -> Callable[[LayoutOverride], LayoutOverride]:
    """A size that set this field its own way follows the person's explicit ask."""

    def apply(override: LayoutOverride) -> LayoutOverride:
        if getattr(override, field) is None:
            return override
        return override.model_copy(update={field: value})

    return apply


def _band(aspect: str, base_width: int) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) a part may be aligned to, as fractions of a size."""
    from stratum_spec.templates import base_width_for  # no cycle: templates does not import ops

    width, height = Canvas(aspect=aspect, base_width=base_width_for(base_width, aspect)).size  # type: ignore[arg-type]
    short = min(width, height)
    mx, my = ALIGN_MARGIN * short / width, ALIGN_MARGIN * short / height
    top, bottom = my, 1.0 - my
    if aspect == "9:16":
        top, bottom = max(top, STORY_SAFE_TOP), min(bottom, STORY_SAFE_BOTTOM)
    return mx, top, 1.0 - mx, bottom


def _aligned(box: Box, band: tuple[float, float, float, float], op: Align) -> Box:
    left, top, right, bottom = band
    x, y = box.x, box.y
    if op.x == "left":
        x = left
    elif op.x == "right":
        x = right - box.w
    elif op.x == "center":
        x = 0.5 - box.w / 2
    if op.y == "top":
        y = top
    elif op.y == "bottom":
        y = bottom - box.h
    elif op.y == "middle":
        y = (top + bottom) / 2 - box.h / 2
    return box.model_copy(update={"x": _clamp(x, -1.0, 2.0), "y": _clamp(y, -1.0, 2.0)})


def _reordered(spec: PosterSpec, layer_id: str, to: str) -> PosterSpec:
    layers = list(spec.layers)
    index = next(i for i, one in enumerate(layers) if one.id == layer_id)
    moving = layers.pop(index)
    # Nothing goes behind a backdrop that covers the whole poster: it would vanish.
    first = layers[0] if layers else None
    floor = int(
        first is not None
        and first.box.x <= 0.0
        and first.box.y <= 0.0
        and first.box.x + first.box.w >= 1.0
        and first.box.y + first.box.h >= 1.0
    )
    where = {
        "front": len(layers),
        "back": floor,
        "forward": min(index + 1, len(layers)),
        "backward": max(index - 1, floor),
    }[to]
    layers.insert(where, moving)
    try:
        return PosterSpec.model_validate(
            {**spec.model_dump(), "layers": [one.model_dump() for one in layers]}
        )
    except ValueError as exc:
        raise OpError(
            f"layer {layer_id!r} cannot go there: words that sit behind a photo's "
            f"subject must stay above that photo"
        ) from exc


def apply_op(spec: PosterSpec, op: EditOp) -> PosterSpec:
    """Apply one op, returning a new spec. Never mutates the input."""
    if isinstance(op, NeedsHuman):
        return spec
    try:
        target = spec.layer(op.layer)
    except KeyError as exc:
        raise OpError(f"unknown layer {op.layer!r}") from exc
    if isinstance(op, SetFade | Flip | AdjustPhoto | SetAlign | SetFont | SetCase | SetEffect):
        return _apply_new(spec, op, target)
    if isinstance(op, Reorder | SetOpacity | Align):
        return _apply_place(spec, op, target)

    if isinstance(op, SetText):
        if not isinstance(target, TextLayer):
            raise OpError(f"layer {op.layer!r} is not a text layer")
        return _replace(spec, op.layer, content=op.text)

    if isinstance(op, SetColor):
        if not isinstance(target, _COLOR_LAYERS):
            raise OpError(f"layer {op.layer!r} has no colour")
        if isinstance(target, TextLayer):
            return _replace(spec, op.layer, color=op.color)
        return _replace(spec, op.layer, fill=op.color)

    if isinstance(op, Move):
        dx, dy = op.dx, op.dy
        return _replace(
            spec,
            op.layer,
            sized=_override_box(lambda box: _moved(box, dx, dy)),
            box=_moved(target.box, dx, dy),
        )

    if isinstance(op, Scale):
        factor = _clamp(op.factor, MIN_SCALE, MAX_SCALE)
        return _replace(
            spec,
            op.layer,
            sized=_override_box(lambda box: _scaled(box, factor)),
            box=_scaled(target.box, factor),
        )

    if isinstance(op, SwapAsset):
        if not isinstance(target, _ASSET_LAYERS):
            raise OpError(f"layer {op.layer!r} holds no asset")
        if isinstance(target, ImageLayer):
            # A crop was chosen for the old photo's subject. Kept, it would frame
            # the new photo by where someone else stood, so every size goes back
            # to the centre until the photo is framed again. The subject mask is the
            # old photo's too: it goes, and so do the words behind it (schema 6).
            swapped = _replace(
                spec, op.layer, sized=_uncropped, asset=op.asset, crop=None, subject=None
            )
            return _in_front_of(swapped, op.layer)
        return _replace(spec, op.layer, asset=op.asset)

    if isinstance(op, Pan | Zoom):
        photo = _photo(target, op.layer)
        if photo.fit != "cover":
            raise OpError(f"layer {op.layer!r} shows the whole photo, so it cannot be moved in it")
        if isinstance(op, Pan):
            dx, dy = op.dx, op.dy

            def change(crop: Crop) -> Crop:
                return _panned(crop, dx, dy)
        else:
            factor = op.factor

            def change(crop: Crop) -> Crop:
                return _zoomed(crop, factor)

        return _replace(
            spec, op.layer, sized=_override_crop(change), crop=change(photo.crop or Crop())
        )

    if isinstance(op, Dim):
        _photo(target, op.layer)
        amount = op.amount

        def dimmed(override: LayoutOverride) -> LayoutOverride:
            # A size dimmed differently on purpose follows the explicit ask, like visible.
            if override.dim is None:
                return override
            return override.model_copy(update={"dim": amount})

        return _replace(spec, op.layer, sized=dimmed, dim=amount)

    visible = op.visible

    def shown(override: LayoutOverride) -> LayoutOverride:
        # A size that hid this layer on purpose follows the person's explicit ask.
        if override.visible is None:
            return override
        return override.model_copy(update={"visible": visible})

    return _replace(spec, op.layer, sized=shown, visible=visible)


def _apply_new(
    spec: PosterSpec,
    op: SetFade | Flip | AdjustPhoto | SetAlign | SetFont | SetCase | SetEffect,
    target: object,
) -> PosterSpec:
    """The photo and text ops of the first editing set (M10i, schema 7)."""
    if isinstance(op, SetFade):
        if not isinstance(target, ImageLayer | ShapeLayer):
            raise OpError(f"layer {op.layer!r} is not a photo or a shape, so it cannot fade")
        edge, length = op.edge, op.length

        def sized(override: LayoutOverride) -> LayoutOverride:
            if override.fades is None:
                return override
            return override.model_copy(update={"fades": _faded(override.fades, edge, length)})

        return _replace(spec, op.layer, sized=sized, fades=_faded(target.fades, edge, length))
    if isinstance(op, Flip):
        photo = _photo(target, op.layer)
        return _replace(spec, op.layer, flip=not photo.flip)
    if isinstance(op, AdjustPhoto):
        photo = _photo(target, op.layer)
        given = {
            name: value
            for name in ("brightness", "contrast", "saturation", "warmth")
            if (value := getattr(op, name)) is not None
        }
        if not given:
            raise OpError("adjust names no setting to change")
        adjusted = (photo.adjust or Adjust()).model_copy(update=given)
        return _replace(spec, op.layer, adjust=None if adjusted.neutral else adjusted)
    text = _text(target, op.layer)
    if isinstance(op, SetAlign):
        return _replace(spec, op.layer, sized=_following("align", op.align), align=op.align)
    if isinstance(op, SetFont):
        return _replace(spec, op.layer, font=op.font)
    if isinstance(op, SetCase):
        upper = op.upper
        return _replace(spec, op.layer, sized=_following("uppercase", upper), uppercase=upper)
    if op.kind == "none":
        return _replace(spec, op.layer, effect=None) if text.effect is not None else spec
    effect = TextEffect(kind=op.kind, color=op.color, strength=op.strength)
    return _replace(spec, op.layer, effect=effect)


def _apply_place(spec: PosterSpec, op: Reorder | SetOpacity | Align, target: object) -> PosterSpec:
    """Order, see-through and alignment (M10i)."""
    if getattr(target, "locked", False):
        raise OpError(f"layer {op.layer!r} is locked")
    if isinstance(op, Reorder):
        return _reordered(spec, op.layer, op.to)
    if isinstance(op, SetOpacity):
        opacity = round(op.opacity, 4)
        return _replace(spec, op.layer, sized=_following("opacity", opacity), opacity=opacity)
    if op.x is None and op.y is None:
        raise OpError("align names no direction")
    canvas = spec.canvas
    layers = [
        one.model_copy(
            update={"box": _aligned(one.box, _band(canvas.aspect, canvas.base_width), op)}
        )
        if one.id == op.layer
        else one
        for one in spec.layers
    ]
    variants = {
        aspect: {
            one: (
                override.model_copy(
                    update={"box": _aligned(override.box, _band(aspect, canvas.base_width), op)}
                )
                if one == op.layer and override.box is not None
                else override
            )
            for one, override in overrides.items()
        }
        for aspect, overrides in spec.variants.items()
    }
    return spec.model_copy(update={"layers": layers, "variants": variants})


def apply_ops(spec: PosterSpec, ops: list[EditOp]) -> PosterSpec:
    """Apply ops in order, all or nothing: one bad op leaves the spec untouched."""
    result = spec
    for op in ops:
        result = apply_op(result, op)
    return result
