"""Photo intake (M2): turn any client photo into a clean, content-addressed asset."""

from __future__ import annotations

from stratum_worker.assets.errors import (
    AssetError,
    AssetNotFound,
    CorruptImage,
    FileTooLarge,
    ImageTooLarge,
    UnsupportedFileType,
)
from stratum_worker.assets.ingest import (
    MAX_FILE_BYTES,
    MAX_PIXELS,
    MAX_SIDE_PX,
    NORMALISATION_VERSION,
    ingest,
)
from stratum_worker.assets.models import VARIANTS, AssetMeta, Flag, Source, Variant
from stratum_worker.assets.quality import MIN_BRIGHTNESS, MIN_DIMENSION_PX, MIN_SHARPNESS
from stratum_worker.assets.store import AssetStore

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_PIXELS",
    "MAX_SIDE_PX",
    "MIN_BRIGHTNESS",
    "MIN_DIMENSION_PX",
    "MIN_SHARPNESS",
    "NORMALISATION_VERSION",
    "VARIANTS",
    "AssetError",
    "AssetMeta",
    "AssetNotFound",
    "AssetStore",
    "CorruptImage",
    "FileTooLarge",
    "Flag",
    "ImageTooLarge",
    "Source",
    "UnsupportedFileType",
    "Variant",
    "ingest",
]
