"""Spec schema migrations.

Posters saved in October must still open in March. Every schema bump ships an
upgrader here plus a test with a real stored spec from the old version.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stratum_spec.models import SCHEMA_VERSION, PosterSpec

Upgrader = Callable[[dict[str, Any]], dict[str, Any]]


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 2 added LogoLayer.plate. A version-1 poster has no plate: unchanged."""
    return {**data, "schema_version": 2}


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 3 added PosterSpec.variants. A version-2 poster carries none, and is
    laid out in other sizes from its template file, exactly as before."""
    return {**data, "schema_version": 3, "variants": data.get("variants", {})}


def _v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 4 added ImageLayer.crop. A version-3 poster has none, so every photo
    still shows its centre, exactly as before."""
    return {**data, "schema_version": 4}


def _v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 5 added TextLayer.wrap, "balanced" for new text. A version-4 poster was
    approved with greedy wrapping, so every text layer keeps it: same lines, same
    picture, whatever the default is now."""
    layers = [
        {**layer, "wrap": layer.get("wrap", "greedy")} if layer.get("type") == "text" else layer
        for layer in data.get("layers", [])
    ]
    return {**data, "schema_version": 5, "layers": layers}


# version N -> function producing version N+1.
UPGRADERS: dict[int, Upgrader] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5}


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
