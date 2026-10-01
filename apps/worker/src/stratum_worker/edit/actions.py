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


class _Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _shortened(limit: int) -> BeforeValidator:
    """A model's over-long sentence is shortened, not a reason to drop the answer."""
    return BeforeValidator(lambda value: value[:limit] if isinstance(value, str) else value)


# British spellings a model may use for the design space's own values.
_SPELLINGS = {"centre": "center", "centred": "center", "centered": "center"}


def _plain(value: object) -> object:
    if not isinstance(value, str):
        return value
    word = value.strip().lower()
    return _SPELLINGS.get(word, word)


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


Action = Annotated[Restyle | Ask | Undo, Field(discriminator="op")]
ACTIONS: TypeAdapter[list[Action]] = TypeAdapter(list[Action])
NAMES = frozenset({"restyle", "ask", "undo"})


def split(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Action]]:
    """(spec ops, still raw; service actions, validated). Raises ValidationError."""
    spec_ops = [one for one in raw if one.get("op") not in NAMES]
    actions = ACTIONS.validate_python([one for one in raw if one.get("op") in NAMES])
    return spec_ops, actions


# Said to undo without asking a model: instant and free.
UNDO_WORDS = frozenset(
    {"undo", "undo that", "undo it", "go back", "revert", "revert that", "undo the last change"}
)


def is_undo(message: str) -> bool:
    return message.strip().lower().rstrip(".!") in UNDO_WORDS


__all__ = ["ACTIONS", "NAMES", "Action", "Ask", "Choice", "Restyle", "Undo", "is_undo", "split"]
