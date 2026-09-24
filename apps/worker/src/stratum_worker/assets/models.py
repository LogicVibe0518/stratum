from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from stratum_spec.models import AssetRef

Source = Literal["client", "stock", "generated"]
Flag = Literal["too_blurry", "too_small", "too_dark", "colour_profile_ignored"]
Variant = Literal["full", "web", "thumb"]

VARIANTS: tuple[Variant, ...] = ("full", "web", "thumb")


class AssetMeta(BaseModel):
    """What the store knows about one normalised photo.

    Pixels and quality never change once written. `sources` only grows: the same
    pixels arriving again as stock, say, are recorded rather than discarded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: AssetRef
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    has_alpha: bool
    sources: frozenset[Source] = Field(min_length=1)
    quality: dict[str, float]
    flags: list[Flag]

    @field_serializer("sources")
    def _sorted_sources(self, sources: frozenset[Source]) -> list[Source]:
        # Set order varies between processes; meta.json must not.
        return sorted(sources)
