"""Typed AI tasks.

The code never says "call OpenRouter" or "call GPT". It says `caption.generate`.
A task declares what goes in, what must come out, and how long it may take. Providers
register which tasks they can serve. Swapping models is then a config change, and a
new provider is one adapter class - which is the whole point of this module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskName(StrEnum):
    CAPTION_GENERATE = "caption.generate"
    EDIT_PARSE = "edit.parse"
    BACKGROUND_CREATE = "background.create"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- caption.generate -------------------------------------------------------


class CaptionRequest(Frozen):
    brief: str = Field(min_length=1, max_length=2000)
    client_name: str = Field(min_length=1, max_length=120)
    tone: str = Field(default="friendly", max_length=200)
    language: str = Field(default="en-GB", max_length=12)
    platform: Literal["instagram", "facebook", "linkedin"] = "instagram"
    past_posts: list[str] = Field(default_factory=list, max_length=20)
    banned_words: list[str] = Field(default_factory=list, max_length=50)


class CaptionResult(Frozen):
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=15)


# --- edit.parse -------------------------------------------------------------


class EditParseRequest(Frozen):
    """A tweak in plain language, plus what the poster currently contains."""

    message: str = Field(min_length=1, max_length=1000)
    layers: dict[str, str] = Field(max_length=64)  # layer id -> short description
    palette: list[str] = Field(default_factory=list, max_length=32)


class EditParseResult(Frozen):
    """Ops as raw dicts here; stratum_spec validates them into real EditOps.

    Keeping the gateway ignorant of op semantics means a hostile or confused model
    cannot reach the spec - the validator downstream is the only way in.
    """

    ops: list[dict[str, object]] = Field(default_factory=list, max_length=12)
    needs_human: str | None = None


# --- background.create ------------------------------------------------------


class BackgroundRequest(Frozen):
    prompt: str = Field(min_length=1, max_length=1000)
    aspect: Literal["1:1", "4:5", "9:16"] = "4:5"
    reference_image: bytes | None = None
    # Enforced by the adapter in its own prompt; checked again by OCR downstream.
    no_text: Literal[True] = True


class BackgroundResult(Frozen):
    image_png: bytes
    width: int = Field(gt=0)
    height: int = Field(gt=0)


TaskRequest = Annotated[
    CaptionRequest | EditParseRequest | BackgroundRequest, Field(union_mode="left_to_right")
]
TaskResult = Annotated[
    CaptionResult | EditParseResult | BackgroundResult, Field(union_mode="left_to_right")
]

RESULT_TYPES: dict[TaskName, type[BaseModel]] = {
    TaskName.CAPTION_GENERATE: CaptionResult,
    TaskName.EDIT_PARSE: EditParseResult,
    TaskName.BACKGROUND_CREATE: BackgroundResult,
}

# Latency budgets. edit.parse is on the interactive path (and later the voice path),
# so it gets the tightest timeout and is never routed through a slow model.
TIMEOUTS_S: dict[TaskName, float] = {
    TaskName.CAPTION_GENERATE: 20.0,
    TaskName.EDIT_PARSE: 8.0,
    TaskName.BACKGROUND_CREATE: 60.0,
}
