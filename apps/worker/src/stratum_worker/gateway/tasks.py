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
    POST_COPY = "post.copy"
    CAPTION_GENERATE = "caption.generate"
    EDIT_PARSE = "edit.parse"
    BACKGROUND_CREATE = "background.create"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- post.copy --------------------------------------------------------------
#
# Every word of a post in one call: what goes on the poster AND what goes in the
# box below it. One call means one voice across both, one round trip and one price -
# and the same words are reused for every aspect ratio the post ships in.
#
# `caption.generate` stays for the editor's "rewrite just the caption" path.


class PostCopyRequest(Frozen):
    brief: str = Field(min_length=1, max_length=2000)
    client_name: str = Field(min_length=1, max_length=120)
    trade: str = Field(default="", max_length=60)
    tone: str = Field(default="friendly, plain-spoken", max_length=200)
    language: str = Field(default="en-GB", max_length=12)
    platform: Literal["instagram", "facebook", "linkedin"] = "instagram"

    # Discoverability. Instagram search reads caption text directly and weights the
    # first line most, so the prompt asks for a service term and an area term there.
    # Asking is not enough - draft.preflight checks it afterwards.
    services: tuple[str, ...] = Field(default=(), max_length=40)
    areas: tuple[str, ...] = Field(default=(), max_length=40)
    allowed_hashtags: tuple[str, ...] = Field(default=(), max_length=60)
    max_hashtags: int = Field(default=5, ge=0, le=30)

    must_include: tuple[str, ...] = Field(default=(), max_length=20)
    banned_claims: tuple[str, ...] = Field(default=(), max_length=60)

    # What the photo actually shows, in the designer's words. Empty means no alt
    # text is written: inventing a description of a picture nobody looked at is
    # worse for a screen-reader user than having none, and it can simply be false.
    photo_description: str = Field(default="", max_length=300)

    # The template's real capacity, so the model aims inside the box instead of
    # relying on auto-fit to shrink its way out of trouble.
    headline_max_chars: int = Field(default=60, ge=10, le=120)
    offer_max_chars: int = Field(default=120, ge=10, le=200)
    cta_max_chars: int = Field(default=40, ge=6, le=80)


class PostCopyResult(Frozen):
    headline: str = Field(min_length=1, max_length=120)
    offer_detail: str = Field(min_length=1, max_length=200)
    cta: str = Field(min_length=1, max_length=80)
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: tuple[str, ...] = Field(default=(), max_length=30)
    alt_text: str = Field(default="", max_length=1000)


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
    aspect: Literal["1:1", "4:5", "9:16", "16:9"] = "4:5"
    reference_image: bytes | None = None
    # Enforced by the adapter in its own prompt; checked again by OCR downstream.
    no_text: Literal[True] = True


class BackgroundResult(Frozen):
    image_png: bytes
    width: int = Field(gt=0)
    height: int = Field(gt=0)


TaskRequest = Annotated[
    PostCopyRequest | CaptionRequest | EditParseRequest | BackgroundRequest,
    Field(union_mode="left_to_right"),
]
TaskResult = Annotated[
    PostCopyResult | CaptionResult | EditParseResult | BackgroundResult,
    Field(union_mode="left_to_right"),
]

RESULT_TYPES: dict[TaskName, type[BaseModel]] = {
    TaskName.POST_COPY: PostCopyResult,
    TaskName.CAPTION_GENERATE: CaptionResult,
    TaskName.EDIT_PARSE: EditParseResult,
    TaskName.BACKGROUND_CREATE: BackgroundResult,
}

# Latency budgets. edit.parse is on the interactive path (and later the voice path),
# so it gets the tightest timeout and is never routed through a slow model.
TIMEOUTS_S: dict[TaskName, float] = {
    TaskName.POST_COPY: 25.0,
    TaskName.CAPTION_GENERATE: 20.0,
    TaskName.EDIT_PARSE: 8.0,
    TaskName.BACKGROUND_CREATE: 60.0,
}
