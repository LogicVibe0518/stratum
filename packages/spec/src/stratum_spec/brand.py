"""Brand kit: the only place raw colours and font files are allowed.

A spec references tokens ("palette.primary", "font.display"); the brand kit turns
them into a hex colour or a font file at render time. A poster pins the brand-kit
version it was built with, so re-rendering an approved post never shifts its colours.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Hex = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
TokenName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class UnknownToken(KeyError):
    """A spec referenced a token this brand kit does not define."""


class FontFace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    family: str = Field(min_length=1, max_length=64)
    file: str = Field(min_length=1, max_length=256)
    weight: int = Field(default=400, ge=100, le=900)
    line_height: float = Field(default=1.15, ge=0.8, le=2.5)
    letter_spacing: float = Field(default=0.0, ge=-0.1, le=0.5)


class BrandKit(BaseModel):
    """One client's colours, fonts and rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    palette: dict[TokenName, Hex]
    fonts: dict[TokenName, FontFace]

    def color(self, token: str) -> str:
        """Resolve "palette.primary" -> "#e07a1f"."""
        name = token.removeprefix("palette.")
        try:
            return self.palette[name]
        except KeyError as exc:
            raise UnknownToken(f"{token} is not in brand kit {self.client_id}") from exc

    def font(self, token: str) -> FontFace:
        """Resolve "font.display" -> a FontFace."""
        name = token.removeprefix("font.")
        try:
            return self.fonts[name]
        except KeyError as exc:
            raise UnknownToken(f"{token} is not in brand kit {self.client_id}") from exc
