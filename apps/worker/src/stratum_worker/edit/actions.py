"""Ops the service carries out itself, rather than stratum_spec.

Spec ops (set_text, move, pan, ...) change a poster in place and are pure. These
three need more than the poster:
- restyle: lays the post out again as a neighbouring design (the words, the photos,
  the logo, the brand kit), and changes the post's design;
- ask: changes nothing, and puts a question to the designer with choices to click;
- undo: makes the version before the last one the poster again.

They arrive from the model in the same "ops" list as the spec ops and are validated
here, just as strictly, before anything happens.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter
from stratum_spec import BrandKit, LogoLayer, PosterSpec
from stratum_spec.ops import EditOp, SetColor, SetFont


class _Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _shortened(limit: int) -> BeforeValidator:
    """A model's over-long sentence is shortened, not a reason to drop the answer."""
    return BeforeValidator(lambda value: value[:limit] if isinstance(value, str) else value)


# British spellings a model may use for the design space's own values.
_SPELLINGS = {"centre": "center", "centred": "center", "centered": "center"}


def _plain(value: object) -> object:
    # A setting like share is a number in the design space's own words ("0.5"); a
    # model may send it as the number 0.5. The chat eval (6 Oct) found "make it pop"
    # thrown away as invalid for exactly that, so a number is read as its text.
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = f"{value:g}"
    if not isinstance(value, str):
        return value
    word = value.strip().lower()
    return _SPELLINGS.get(word, word)


LOGO_NEVER_RECOLOURED = (
    "The logo is the client's own file, so it is never recoloured. You can move it, "
    "resize it, hide it, or put it on a panel."
)


def without_logo_recolours(spec: PosterSpec, ops: list[EditOp]) -> tuple[list[EditOp], str]:
    """The ops, minus any that recolour a logo, and the sentence saying why (or "").

    A logo is always the client's original file (AGENTS.md). A model asked "same for
    the logo" after a colour change will try anyway (chat eval, 6 Oct); the editor
    said "That change could not be made" when it should say why."""
    logos = {layer.id for layer in spec.layers if isinstance(layer, LogoLayer)}
    kept = [op for op in ops if not (isinstance(op, SetColor) and op.layer in logos)]
    return kept, LOGO_NEVER_RECOLOURED if len(kept) < len(ops) else ""


def without_unknown_fonts(kit: BrandKit, ops: list[EditOp]) -> tuple[list[EditOp], str]:
    """The ops, minus any font the brand does not have, and the sentence saying why.
    A font is part of the brand like a colour: new ones are added to the brand kit."""

    def unknown(op: EditOp) -> bool:
        return isinstance(op, SetFont) and op.font.removeprefix("font.") not in kit.fonts

    kept = [op for op in ops if not unknown(op)]
    if len(kept) == len(ops):
        return kept, ""
    names = ", ".join(sorted(kit.fonts))
    return kept, f"That font is not one of the brand's. Its fonts are: {names}."


Setting = Annotated[
    str, BeforeValidator(_plain), Field(min_length=1, max_length=24, pattern=r"^[a-z_]+$")
]
Value = Annotated[
    str, BeforeValidator(_plain), Field(min_length=1, max_length=24, pattern=r"^[a-z0-9_.]+$")
]


class Restyle(_Action):
    op: Literal["restyle"] = "restyle"
    changes: dict[Setting, Value] = Field(min_length=1, max_length=8)


class Choice(_Action):
    """One answer to a question: a change to send as the next tweak, or a brand edit."""

    label: Annotated[str, _shortened(120), Field(min_length=1, max_length=120)]
    kind: Literal["tweak", "brand"] = "tweak"


class Ask(_Action):
    op: Literal["ask"] = "ask"
    question: Annotated[str, _shortened(300), Field(min_length=1, max_length=300)]
    choices: Annotated[
        list[Choice],
        BeforeValidator(lambda value: value[:4] if isinstance(value, list) else value),
        Field(default_factory=list, max_length=4),
    ]


class Undo(_Action):
    op: Literal["undo"] = "undo"
    all: bool = False  # back to the first draft, not one step


class Badge(_Action):
    """An offer badge in a corner (edit/badge.py), or `remove` it. Its words come from
    the designer or the poster's own offer, never invented."""

    op: Literal["badge"] = "badge"
    text: Annotated[str, _shortened(40), Field(default="", max_length=40)]
    corner: Literal["auto", "top_left", "top_right", "bottom_left", "bottom_right"] = "auto"
    remove: bool = False


Action = Annotated[Restyle | Ask | Undo | Badge, Field(discriminator="op")]
ACTIONS: TypeAdapter[list[Action]] = TypeAdapter(list[Action])
NAMES = frozenset({"restyle", "ask", "undo", "badge"})


def split(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Action]]:
    """(spec ops, still raw; service actions, validated). Raises ValidationError."""
    spec_ops = [one for one in raw if one.get("op") not in NAMES]
    actions = ACTIONS.validate_python([one for one in raw if one.get("op") in NAMES])
    return spec_ops, actions


# Said to undo without asking a model: instant and free.
UNDO_WORDS = frozenset(
    {"undo", "undo that", "undo it", "go back", "revert", "revert that", "undo the last change"}
)
UNDO_ALL_WORDS = frozenset(
    {
        "undo all",
        "undo all changes",
        "undo everything",
        "undo all the changes",
        "revert all",
        "revert everything",
        "start over",
        "start again",
        "reset",
        "reset it",
        "back to the original",
        "go back to the original",
        "back to the first draft",
        "go back to the first draft",
    }
)


def undo_asked(message: str) -> Undo | None:
    """An undo typed in so many words, needing no model: one step, or all of them."""
    said = " ".join(message.strip().lower().rstrip(".!").split())
    if said in UNDO_ALL_WORDS:
        return Undo(all=True)
    if said in UNDO_WORDS:
        return Undo()
    return None


def is_undo(message: str) -> bool:
    return undo_asked(message) is not None


__all__ = [
    "ACTIONS",
    "LOGO_NEVER_RECOLOURED",
    "NAMES",
    "Action",
    "Ask",
    "Badge",
    "Choice",
    "Restyle",
    "Undo",
    "is_undo",
    "split",
    "undo_asked",
    "without_logo_recolours",
    "without_unknown_fonts",
]
