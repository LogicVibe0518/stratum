"""Per-client marketing rules: what copy must contain and must never say.

Deliberately NOT part of BrandKit. A brand kit is visual (colours, fonts) and a
poster pins the brand-kit version it was rendered with, so adding a keyword would
bump that version and change the provenance of every past render for no visual
reason. These rules govern words, are checked after a model answers, and version
independently.

The point of this file: a prompt REQUESTS compliance, a validator GUARANTEES it.
Everything here is machine-checkable, which is also what contains a hostile brief -
a brief that says "ignore your instructions" can at worst produce bad copy, never
copy that breaks a constraint.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClientRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)

    # Discoverability. Instagram search reads caption text as a direct signal and
    # weights the first line most, so preflight requires one service term and one
    # area term in the caption's FIRST sentence - not merely somewhere in it.
    services: tuple[str, ...] = Field(default=(), max_length=40)
    areas: tuple[str, ...] = Field(default=(), max_length=40)

    # Hashtags classify a post; they are not a reach lever any more. A short
    # allow-list beats thirty scraped tags, and makes the check exact.
    allowed_hashtags: tuple[str, ...] = Field(default=(), max_length=60)
    max_hashtags: int = Field(default=5, ge=0, le=30)

    # Substrings that must appear verbatim somewhere in the copy (a Gas Safe
    # registration number, a licence number, a legally required disclaimer).
    must_include: tuple[str, ...] = Field(default=(), max_length=20)

    # Substrings that must never appear, case-insensitive: "cheapest in Manchester",
    # "we waive your deductible" (illegal in Texas), "guaranteed".
    banned_claims: tuple[str, ...] = Field(default=(), max_length=60)

    alt_text_max_chars: int = Field(default=125, ge=40, le=1000)


__all__ = ["ClientRules"]
