"""Edit operations: the ONLY way a spec ever changes.

A designer's click, a typed tweak and a voice command all end up here, so the
same validation protects every path. A model may propose ops; it can never
write a spec directly.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from stratum_spec.models import (
    AssetRef,
    ColorToken,
    CutoutLayer,
    ImageLayer,
    LayerId,
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


def _replace(spec: PosterSpec, layer_id: str, **changes: object) -> PosterSpec:
    target = spec.layer(layer_id)  # raises KeyError if missing
    if target.locked:
        raise OpError(f"layer {layer_id!r} is locked")
    updated = target.model_copy(update=changes)
    layers = [updated if candidate.id == layer_id else candidate for candidate in spec.layers]
    return spec.model_copy(update={"layers": layers})


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
        field = "color" if isinstance(target, TextLayer) else "fill"
        return _replace(spec, op.layer, **{field: op.color})

    if isinstance(op, Move):
        box = target.box.model_copy(
            update={
                "x": _clamp(target.box.x + op.dx, -1.0, 2.0),
                "y": _clamp(target.box.y + op.dy, -1.0, 2.0),
            }
        )
        return _replace(spec, op.layer, box=box)

    if isinstance(op, Scale):
        factor = _clamp(op.factor, MIN_SCALE, MAX_SCALE)
        # Scale about the box centre so "bigger" does not drift to the corner.
        new_w = _clamp(target.box.w * factor, 0.01, 3.0)
        new_h = _clamp(target.box.h * factor, 0.01, 3.0)
        box = target.box.model_copy(
            update={
                "w": new_w,
                "h": new_h,
                "x": _clamp(target.box.x - (new_w - target.box.w) / 2, -1.0, 2.0),
                "y": _clamp(target.box.y - (new_h - target.box.h) / 2, -1.0, 2.0),
            }
        )
        return _replace(spec, op.layer, box=box)

    if isinstance(op, SwapAsset):
        if not isinstance(target, _ASSET_LAYERS):
            raise OpError(f"layer {op.layer!r} holds no asset")
        return _replace(spec, op.layer, asset=op.asset)

    return _replace(spec, op.layer, visible=op.visible)


def apply_ops(spec: PosterSpec, ops: list[EditOp]) -> PosterSpec:
    """Apply ops in order, all or nothing: one bad op leaves the spec untouched."""
    result = spec
    for op in ops:
        result = apply_op(result, op)
    return result
