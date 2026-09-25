"""Domain types for a drafted post.

`PostCopy` is deliberately NOT the gateway's task result type. The gateway returns
its own model; a thin mapping turns it into this. That keeps `build_spec` - the heart
of the module - free of any dependency on the AI layer, which is why it can be tested
with no network, no keys and no mocks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from stratum_spec.models import Aspect

Severity = Literal["blocker", "warning"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PostCopy(Frozen):
    """Every word a model is allowed to contribute to a post.

    Note what is absent: no colour, no size, no position, no layer. A model returns
    words; the code decides where words go. That boundary is the product.
    """

    headline: str = Field(min_length=1, max_length=120)
    offer_detail: str = Field(min_length=1, max_length=200)
    cta: str = Field(min_length=1, max_length=80)
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: tuple[str, ...] = Field(default=(), max_length=30)
    # Accessibility, and a clean text signal for search. Describes the IMAGE, not
    # the offer. Empty when nobody told us what the photo shows.
    alt_text: str = Field(default="", max_length=1000)

    @property
    def printed(self) -> str:
        """Everything that ends up on the poster itself."""
        return f"{self.headline}\n{self.offer_detail}\n{self.cta}"

    @property
    def everything(self) -> str:
        return f"{self.printed}\n{self.caption}\n{' '.join(self.hashtags)}\n{self.alt_text}"


class PostBrief(Frozen):
    """What a designer hands in. Everything else is derived."""

    client_id: str = Field(min_length=1, max_length=64)
    brief: str = Field(min_length=1, max_length=2000)
    template_id: str = "offer_4x5"
    slug: str = Field(min_length=1, max_length=80)
    aspects: tuple[Aspect, ...] = ("4:5", "1:1")
    platform: Literal["instagram", "facebook", "linkedin"] = "instagram"
    # What the chosen photo shows, in the designer's words. Five seconds of typing
    # buys honest alt text; leaving it blank means no alt text rather than a guess.
    photo_description: str = ""
    # A named occasion (Diwali, Christmas, Eid). Changes the copy, not the layout.
    occasion: str = ""


class Flag(Frozen):
    """Something a human should look at before this post goes out.

    A blocker means do not publish. A warning means glance at it. Nothing here ever
    stops the draft being produced - a person decides, and they need to see the
    poster to decide.
    """

    code: str
    message: str
    severity: Severity = "warning"


class StepRecord(Frozen):
    """What one step of the pipeline did, and what it cost."""

    step: str
    cached: bool = False
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    run_id: str = ""


class RenderedPoster(Frozen):
    aspect: Aspect
    png_path: Path
    spec_path: Path
    width: int
    height: int


class PostDraft(Frozen):
    """The finished article: words, pictures, files, flags and receipts.

    Storing the spec and the asset ids - not just the PNGs - is what makes a tweak
    free: changing the headline or resizing the logo needs no model at all, only a
    re-render from what is already here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str
    slug: str
    template_id: str
    # Named `words`, not `copy`: a field called copy would shadow BaseModel.copy.
    words: PostCopy
    assets: dict[str, str]
    posters: tuple[RenderedPoster, ...]
    flags: tuple[Flag, ...]
    steps: tuple[StepRecord, ...]

    @property
    def cost_usd(self) -> float:
        return round(sum(step.cost_usd for step in self.steps), 6)

    @property
    def blockers(self) -> tuple[Flag, ...]:
        return tuple(flag for flag in self.flags if flag.severity == "blocker")


__all__ = [
    "Flag",
    "PostBrief",
    "PostCopy",
    "PostDraft",
    "RenderedPoster",
    "StepRecord",
]
