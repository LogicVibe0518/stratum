"""Typed intake failures. Each message is safe to show to the agency user."""

from __future__ import annotations


class AssetError(ValueError):
    """A file that cannot become an asset."""


class UnsupportedFileType(AssetError):
    """The bytes are not a format we accept, whatever the extension says."""


class CorruptImage(AssetError):
    """The header looked right but the image (or its colour profile) will not decode."""


class FileTooLarge(AssetError):
    """Over the byte limit; rejected before decoding."""


class ImageTooLarge(AssetError):
    """Over the pixel limit; rejected from the header, before decoding pixels."""


class AssetNotFound(LookupError):
    """No asset with that sha256 is in the store."""
