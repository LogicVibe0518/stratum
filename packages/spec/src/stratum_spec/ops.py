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
    AssetRef,
    Box,
    ColorToken,
    CutoutLayer,
    ImageLayer,
    LayerId,
    LayoutOverride,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextLayer,
)

MIN_SCALE = 0.2
MAX_SCALE = 5.0


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


class NeedsHuman(BaseOp):
    """The request was understood but is not mechanical ("make it pop")."""

    op: Literal["needs_human"] = "needs_human"
    reason: str = Field(min_length=1, max_length=200)


EditOp = Annotated[
    SetText | SetColor | Move | Scale | SwapAsset | SetVisible | NeedsHuman,
    Field(discriminator="op"),
]

_ASSET_LAYERS = (ImageLayer, CutoutLayer, LogoLayer)
_COLOR_LAYERS = (ShapeLayer, TextLayer)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def apply_op(spec: PosterSpec, op: EditOp) -> PosterSpec:
    """Apply one op, returning a new spec. Never mutates the input."""
    if isinstance(op, NeedsHuman):
        return spec
    try:
        target = spec.layer(op.layer)
    except KeyError as exc:
        raise OpError(f"unknown layer {op.layer!r}") from exc

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
            # to the centre until the photo is framed again.
            return _replace(spec, op.layer, sized=_uncropped, asset=op.asset, crop=None)
        return _replace(spec, op.layer, asset=op.asset)

    visible = op.visible

    def shown(override: LayoutOverride) -> LayoutOverride:
        # A size that hid this layer on purpose follows the person's explicit ask.
        if override.visible is None:
            return override
        return override.model_copy(update={"visible": visible})

    return _replace(spec, op.layer, sized=shown, visible=visible)


def apply_ops(spec: PosterSpec, ops: list[EditOp]) -> PosterSpec:
    """Apply ops in order, all or nothing: one bad op leaves the spec untouched."""
    result = spec
    for op in ops:
        result = apply_op(result, op)
    return result
