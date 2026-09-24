"""Photo intake: any phone photo in, one clean content-addressed asset out.

Pipeline: size check -> magic-byte sniff -> header dimension check -> decode ->
EXIF orientation -> sRGB -> alpha kept or dropped -> rebuilt without metadata ->
sha256 -> full/web/thumb PNGs + meta.json.

The caller's file is read once into memory and never opened for writing.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from stratum_worker.assets.errors import (
    AssetNotFound,
    CorruptImage,
    FileTooLarge,
    ImageTooLarge,
    UnsupportedFileType,
)
from stratum_worker.assets.models import AssetMeta, Source, Variant
from stratum_worker.assets.quality import flags_for, measure

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_SIDE_PX = 8000
MAX_PIXELS = MAX_SIDE_PX * MAX_SIDE_PX
VARIANT_MAX_SIDE: dict[Variant, int | None] = {"full": None, "web": 1080, "thumb": 512}
META_FILE = "meta.json"

# Pillow's own bomb guard, set to our limit rather than its default. It warns above
# this and raises DecompressionBombError above twice this, both from the header alone.
# Process-wide on purpose: no code path in the worker should decode more than this.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

Format = Literal["JPEG", "PNG", "WEBP"]
_HIGH_BIT_DEPTH_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N", "F"})
# ISO-BMFF brands used by HEIC/HEIF stills and sequences (bytes 8-12 of the file).
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


def read_source(path: Path) -> bytes:
    """Read the whole upload, refusing anything over the byte limit before reading it."""
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileTooLarge(f"{path.name} is {size / 1e6:.1f} MB; the limit is 25 MB")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:  # the file grew between stat and read
        raise FileTooLarge(f"{path.name} is over the 25 MB limit")
    return data


def sniff(data: bytes) -> Format | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def is_heif(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


def decode(data: bytes, name: str) -> Image.Image:
    """Open with only the decoder the magic bytes named, check the header, then decode."""
    if is_heif(data):
        raise UnsupportedFileType("HEIC is not supported yet - export as JPEG")
    fmt = sniff(data)
    if fmt is None:
        raise UnsupportedFileType(f"{name} is not a JPEG, PNG or WebP image")
    try:
        image = Image.open(io.BytesIO(data), formats=[fmt])
    except Image.DecompressionBombError as exc:
        raise ImageTooLarge(f"{name} has far too many pixels") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptImage(f"{name} could not be decoded as {fmt}") from exc
    width, height = image.size
    if width > MAX_SIDE_PX or height > MAX_SIDE_PX:
        raise ImageTooLarge(f"{name} is {width}x{height}; the limit is 8000x8000")
    try:
        image.load()
        return ImageOps.exif_transpose(image)
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptImage(f"{name} could not be decoded as {fmt}") from exc


def _to_8bit(image: Image.Image) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64) / 257.0
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


def _split_alpha(image: Image.Image) -> tuple[Image.Image, Image.Image | None]:
    """Reduce every mode Pillow can hand us to (L | RGB | CMYK colour, optional alpha)."""
    if image.mode in _HIGH_BIT_DEPTH_MODES:
        image = _to_8bit(image)
    elif image.mode in {"P", "PA", "La", "RGBa"} or "transparency" in image.info:
        image = image.convert("RGBA")
    elif image.mode == "1":
        image = image.convert("L")
    if "A" not in image.getbands():
        return (image if image.mode in {"L", "RGB", "CMYK"} else image.convert("RGB")), None
    alpha = image.getchannel("A")
    return image.convert("L" if image.mode == "LA" else "RGB"), alpha


def _to_srgb(colour: Image.Image, icc: bytes | None) -> tuple[Image.Image, bool]:
    """Convert to sRGB. Returns (image, profile_ignored).

    A broken or mismatched profile is common on photos passed through messaging apps.
    Rejecting them would bounce real client photos, so fall back to a naive
    conversion and flag it for a human instead.
    """
    if not icc:
        return colour.convert("RGB"), False
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(colour, profile, _SRGB, outputMode="RGB")
    except (OSError, ImageCms.PyCMSError):
        return colour.convert("RGB"), True
    assert converted is not None  # only None when inPlace=True
    return converted, False


def normalise(image: Image.Image, *, icc: bytes | None) -> tuple[Image.Image, bool]:
    """Upright, sRGB, 8-bit RGB or RGBA, and rebuilt from raw pixels so no EXIF,
    ICC, XMP or text chunk can survive. Returns (image, profile_ignored).

    Alpha is kept only if some pixel is actually transparent. A fully opaque alpha
    channel is dropped, which is the same as flattening on white.
    """
    colour, alpha = _split_alpha(image)
    rgb, profile_ignored = _to_srgb(colour, icc)
    if alpha is not None and int(np.asarray(alpha).min()) < 255:
        rgb.putalpha(alpha)
    return Image.fromarray(np.asarray(rgb)), profile_ignored


# Normalisation version, hashed into every asset id. Bump it deliberately, never by
# accident, whenever a change would alter normalised pixels for the same input
# (colour conversion, orientation, alpha rules). The old ids stay valid for old files;
# new ingests get new ids instead of silently colliding with different pixels.
NORMALISATION_VERSION = b"n1"


def address(image: Image.Image) -> str:
    """sha256 of the normalised pixels, not of any encoded file.

    The same photo in a PNG and in a lossless WebP is one asset, and a Pillow or
    zlib upgrade that changes PNG bytes does not change any asset id.
    """
    width, height = image.size
    header = b"%s:%s:%dx%d:" % (NORMALISATION_VERSION, image.mode.encode(), width, height)
    return hashlib.sha256(header + image.tobytes()).hexdigest()


def _resized(image: Image.Image, max_side: int | None) -> Image.Image:
    width, height = image.size
    if max_side is None or max(width, height) <= max_side:
        return image
    scale = max_side / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _write_meta(folder: Path, meta: AssetMeta) -> None:
    handle, tmp = tempfile.mkstemp(prefix=".meta-", dir=folder)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(meta.model_dump_json(indent=2) + "\n")
    os.replace(tmp, folder / META_FILE)


def _write_asset(
    folder: Path, image: Image.Image, source: Source, sha: str, *, profile_ignored: bool
) -> AssetMeta:
    variants = {name: _resized(image, side) for name, side in VARIANT_MAX_SIDE.items()}
    for name, variant in variants.items():
        variant.save(folder / f"{name}.png", "PNG")
    quality = measure(variants["web"], image.size)
    flags = flags_for(quality)
    if profile_ignored:
        flags.append("colour_profile_ignored")
    meta = AssetMeta(
        sha256=sha,
        width=image.size[0],
        height=image.size[1],
        has_alpha=image.mode == "RGBA",
        sources=frozenset({source}),
        quality=quality,
        flags=flags,
    )
    # Written last: a folder with meta.json is a complete asset.
    _write_meta(folder, meta)
    return meta


def load_meta(folder: Path) -> AssetMeta | None:
    meta_path = folder / META_FILE
    if not meta_path.is_file():
        return None
    return AssetMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))


def add_source(folder: Path, source: Source) -> AssetMeta:
    """Record another source for an existing asset. Touches nothing if already known."""
    known = load_meta(folder)
    if known is None:
        raise AssetNotFound(folder.name)
    if source in known.sources:
        return known
    # Lock the asset folder so two workers adding different sources cannot lose one.
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = load_meta(folder)
        assert current is not None  # asset folders are never deleted
        if source in current.sources:
            return current
        updated = current.model_copy(update={"sources": current.sources | {source}})
        _write_meta(folder, updated)
        return updated
    finally:
        os.close(fd)  # releases the lock


def ingest(path: Path, store_dir: Path, *, source: Source = "client") -> AssetMeta:
    """Normalise one photo into `store_dir/<sha256>/`. Safe to call twice or concurrently."""
    data = read_source(path)
    raw = decode(data, path.name)
    image, profile_ignored = normalise(raw, icc=raw.info.get("icc_profile"))
    sha = address(image)
    final = store_dir / sha
    if load_meta(final) is not None:
        return add_source(final, source)

    store_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ingest-", dir=store_dir))
    try:
        meta = _write_asset(staging, image, source, sha, profile_ignored=profile_ignored)
        try:
            staging.rename(final)
        except OSError:
            # Another worker finished the same photo first; its pixels are identical.
            if load_meta(final) is None:
                raise
            return add_source(final, source)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return meta
