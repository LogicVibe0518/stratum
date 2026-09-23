"""Spec schema migrations.

Posters saved in October must still open in March. Every schema bump ships an
upgrader here plus a test with a real stored spec from the old version.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stratum_spec.models import SCHEMA_VERSION, PosterSpec

Upgrader = Callable[[dict[str, Any]], dict[str, Any]]

# version N -> function producing version N+1. Empty until the first bump.
UPGRADERS: dict[int, Upgrader] = {}


class UnknownSchemaVersion(ValueError):
    """The spec was written by a newer Stratum than this one."""


def upgrade(data: dict[str, Any]) -> dict[str, Any]:
    version = int(data.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise UnknownSchemaVersion(f"spec schema {version} is newer than {SCHEMA_VERSION}")
    while version < SCHEMA_VERSION:
        upgrader = UPGRADERS.get(version)
        if upgrader is None:
            raise UnknownSchemaVersion(f"no upgrader from schema {version}")
        data = upgrader(data)
        version = int(data["schema_version"])
    return data


def load(data: dict[str, Any]) -> PosterSpec:
    """Upgrade then validate: the only supported way to read a stored spec."""
    return PosterSpec.model_validate(upgrade(data))
