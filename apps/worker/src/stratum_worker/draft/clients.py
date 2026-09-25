"""One folder per client, which is also how the agency already thinks.

    clients/kowalski-plumbing/
        client.json     name, trade, and the rules copy must obey
        brand.json      colours and fonts
        logo.png        their file, never regenerated
        photos/         what they sent us
        out/<slug>/     what we made

Nothing in a client folder is shared with another client, which is the cheap version
of tenant isolation until the database arrives: a bug cannot leak across a boundary
that is a directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from stratum_spec.brand import BrandKit
from stratum_spec.client import ClientRules

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")
LOGO_NAMES = ("logo.png", "logo.webp", "logo.jpg", "logo.jpeg")


class ClientFileError(ValueError):
    """A client folder is missing something or holds something malformed."""


class _ClientFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    trade: str = Field(default="", max_length=60)
    rules: ClientRules


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    trade: str
    folder: Path
    kit: BrandKit
    rules: ClientRules

    @property
    def photos(self) -> tuple[Path, ...]:
        folder = self.folder / "photos"
        if not folder.is_dir():
            return ()
        return tuple(sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES))

    @property
    def logo(self) -> Path | None:
        for name in LOGO_NAMES:
            candidate = self.folder / name
            if candidate.is_file():
                return candidate
        return None

    def out_dir(self, slug: str) -> Path:
        return self.folder / "out" / slug


def _read(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ClientFileError(f"{path} is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ClientFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClientFileError(f"{path} should hold an object")
    return data


def load_client(root: Path, client_id: str) -> Client:
    """Read one client folder. Raises ClientFileError with a path you can go and fix."""
    folder = root / client_id
    if not folder.is_dir():
        known = ", ".join(sorted(p.name for p in root.iterdir() if p.is_dir())) or "none"
        raise ClientFileError(f"no client folder {folder}; found: {known}")
    try:
        meta = _ClientFile.model_validate(_read(folder / "client.json"))
        kit = BrandKit.model_validate(_read(folder / "brand.json"))
    except ValidationError as exc:
        raise ClientFileError(f"{folder}: {exc.error_count()} problems: {exc}") from exc
    if kit.client_id != client_id or meta.id != client_id:
        raise ClientFileError(
            f"{folder}: client_id disagrees with the folder name - "
            f"client.json says {meta.id!r}, brand.json says {kit.client_id!r}"
        )
    return Client(
        id=client_id,
        name=meta.name,
        trade=meta.trade,
        folder=folder,
        kit=kit,
        rules=meta.rules,
    )


def list_clients(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(p.name for p in root.iterdir() if (p / "client.json").is_file()))


__all__ = ["Client", "ClientFileError", "list_clients", "load_client"]
