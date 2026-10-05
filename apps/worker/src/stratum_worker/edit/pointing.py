"""Point and say (M10h): the part of the poster the designer tapped before typing.

"Make this bigger" means nothing without "this". In live test 3 the vague tweaks
failed for exactly that reason: the model had to guess which part was meant. Now
the page can send where the person tapped, as fractions of the poster in the size
they were looking at, or the layer they picked in the control panel. The topmost
visible layer at that spot is what they mean. Text is hit by its letters, not its
box: a headline's box is wider than its words, and a tap beside a short headline on
a photo means the photo.

With a part pointed at, a short message is often a whole instruction - "bigger",
"hide", "a bit darker", "up". Those are carried out without a model (`quick_ops`):
no wait, no cost, and nothing to misread. Anything longer goes to the model, which
is told what was pointed at.

Pure: no database, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stratum_spec import (
    BrandKit,
    ImageLayer,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextLayer,
    retarget,
)
from stratum_spec.models import Aspect
from stratum_spec.ops import MAX_DIM, Dim, EditOp, Move, Scale, SetVisible, Zoom

from stratum_worker.photo.depth import letters
from stratum_worker.render.layout import box_px

# A fingertip is not a pixel: letters are hit within this share of the shorter side.
REACH = 0.02
STEP = 0.03  # one "up" moves a part this share of the poster
BIGGER, SMALLER = 1.15, 1 / 1.15
DARKER = 0.15  # one "darker" dims a photo by this much more

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Pointing:
    """Where the person pointed: a layer picked by name, or a tap at (x, y) as
    fractions of the poster in the size they were looking at."""

    layer: str | None = None
    x: float | None = None
    y: float | None = None
    aspect: Aspect | None = None

    def parts(self) -> dict[str, Any]:
        return {"layer": self.layer, "x": self.x, "y": self.y, "aspect": self.aspect}


def _contains(rect: Rect, x: float, y: float, slack: float = 0.0) -> bool:
    left, top, width, height = rect
    return left - slack <= x <= left + width + slack and top - slack <= y <= top + height + slack


def layer_at(
    spec: PosterSpec, x: float, y: float, kit: BrandKit, root: Path, aspect: Aspect | None = None
) -> str | None:
    """The id of the topmost visible layer at (x, y), fractions of the poster, in the
    size the person was looking at. None when the point is off the poster."""
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    shown = retarget(spec, aspect) if aspect and aspect != spec.canvas.aspect else spec
    width, height = shown.canvas.size
    px, py = x * width, y * height
    slack = REACH * min(width, height)
    for layer in reversed(shown.layers):
        if not layer.visible:
            continue
        if isinstance(layer, TextLayer):
            if any(_contains(line, px, py, slack) for line in letters(layer, shown, kit, root)):
                return layer.id
            continue
        if _contains(box_px(shown, layer), px, py):
            return layer.id
    return None


def pointed_layer(
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    *,
    layer: str | None = None,
    x: float | None = None,
    y: float | None = None,
    aspect: Aspect | None = None,
) -> str | None:
    """What the person pointed at: a layer they picked by name, or the layer at the
    point they tapped. None when neither names a part of this poster."""
    ids = {one.id for one in spec.layers}
    if layer is not None:
        return layer if layer in ids else None
    if x is None or y is None:
        return None
    return layer_at(spec, x, y, kit, root, aspect)


# --- short messages that need no model ---------------------------------------------------

# Words that add nothing once a part is pointed at.
_FILLER = {
    "a", "an", "the", "this", "that", "it", "these", "those", "here", "please", "pls",
    "make", "can", "you", "could", "just", "bit", "little", "tiny", "slightly", "lil",
    "move", "go", "put", "be", "is", "should", "more", "much", "somewhat", "abit",
    "thanks", "thank", "ty", "one", "part", "thing", "get", "of", "to", "too",
}  # fmt: skip
_SAY = {
    "bigger": "bigger", "larger": "bigger", "enlarge": "bigger", "grow": "bigger",
    "smaller": "smaller", "shrink": "smaller", "tinier": "smaller",
    "hide": "hide", "remove": "hide", "delete": "hide", "rid": "hide", "drop": "hide",
    "up": "up", "higher": "up", "down": "down", "lower": "down",
    "left": "left", "right": "right",
    "darker": "darker", "darken": "darker", "dimmer": "darker",
    "lighter": "lighter", "brighter": "lighter", "lighten": "lighter",
    "closer": "closer", "zoom": "closer", "in": "",
}  # fmt: skip


def said(message: str) -> str | None:
    """The one instruction a short message gives, or None when it says more than one
    thing (or anything this does not know): "make this a bit bigger" -> "bigger"."""
    words = re.findall(r"[a-z]+", message.lower())
    if not words or len(words) > 7:
        return None
    meant = {_SAY[word] for word in words if word in _SAY} - {""}
    rest = [word for word in words if word not in _SAY and word not in _FILLER]
    if rest or len(meant) != 1:
        return None
    return meant.pop()


def quick_ops(spec: PosterSpec, layer_id: str, message: str) -> list[EditOp] | None:
    """The change a short message asks of the part pointed at, made without a model.
    None when the message is not one of these, or does not suit that part."""
    instruction = said(message)
    if instruction is None:
        return None
    target = next((one for one in spec.layers if one.id == layer_id), None)
    if target is None or target.locked:
        return None
    photo = isinstance(target, ImageLayer)
    moves = {"up": (0.0, -STEP), "down": (0.0, STEP), "left": (-STEP, 0.0), "right": (STEP, 0.0)}
    if instruction == "bigger" and not photo:
        return [Scale(layer=layer_id, factor=BIGGER)]
    if instruction == "smaller" and not photo:
        return [Scale(layer=layer_id, factor=SMALLER)]
    if instruction == "hide" and target.visible:
        return [SetVisible(layer=layer_id, visible=False)]
    if instruction in moves and isinstance(target, TextLayer | LogoLayer | ShapeLayer):
        dx, dy = moves[instruction]
        return [Move(layer=layer_id, dx=dx, dy=dy)]
    if photo and isinstance(target, ImageLayer):
        if instruction == "darker" and target.dim < MAX_DIM:
            return [Dim(layer=layer_id, amount=round(min(MAX_DIM, target.dim + DARKER), 3))]
        if instruction == "lighter" and target.dim > 0:
            return [Dim(layer=layer_id, amount=round(max(0.0, target.dim - DARKER), 3))]
        if instruction in ("closer", "bigger") and target.fit == "cover":
            return [Zoom(layer=layer_id, factor=BIGGER)]
    return None


__all__ = ["Pointing", "layer_at", "pointed_layer", "quick_ops", "said"]
