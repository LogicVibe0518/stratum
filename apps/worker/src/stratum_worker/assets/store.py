"""Local, content-addressed asset store.

Layout under `root`:
    <sha256>/full.png, web.png, thumb.png, meta.json   one normalised asset
    _sources/<sha256 of uploaded bytes>                 the asset id it became
    _fetched/<sha256>.png                               full size, fetched from storage

The source index lets a re-sent photo skip decoding entirely. Object storage is the
record; this disk is a cache in front of it (see `service.local_path`).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from stratum_worker.assets.errors import AssetError, AssetNotFound, CorruptImage
from stratum_worker.assets.ingest import (
    add_source,
    address,
    decode,
    ingest,
    load_meta,
    read_source,
)
from stratum_worker.assets.models import VARIANTS, AssetMeta, Source, Variant

SOURCES_DIR = "_sources"
FETCHED_DIR = "_fetched"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _checked(sha: str) -> str:
    if not _SHA256.fullmatch(sha):
        raise ValueError(f"{sha!r} is not a sha256 asset id")
    return sha


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, path: Path, *, source: Source = "client") -> AssetMeta:
        upload_sha = hashlib.sha256(read_source(path)).hexdigest()
        index = self.root / SOURCES_DIR / upload_sha
        if index.is_file():
            folder = self.root / _checked(index.read_text(encoding="ascii").strip())
            if load_meta(folder) is not None:
                return add_source(folder, source)
        meta = ingest(path, self.root, source=source)
        self._write_index(index, meta.sha256)
        return meta

    def meta_for(self, sha: str) -> AssetMeta:
        meta = load_meta(self.root / _checked(sha))
        if meta is None:
            raise AssetNotFound(sha)
        return meta

    def path_for(self, sha: str, variant: Variant) -> Path:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
        self.meta_for(sha)
        return self.root / sha / f"{variant}.png"

    def fetched(self, sha: str) -> Path:
        """Where a full-size copy fetched from object storage is kept on this disk.

        A server that did not ingest an asset itself (the worker, for a photo uploaded
        through the API; any server after a redeploy wipes its disk) keeps the copy
        here, apart from ingested assets: a folder named by a sha is complete only
        with its meta.json, and a fetched file must never look like a half-ingested one.
        """
        return self.root / FETCHED_DIR / f"{_checked(sha)}.png"

    def keep_fetched(self, sha: str, data: bytes) -> Path:
        """Keep the bytes of a fetched asset, refusing any that are not the asset named.

        The id is the hash of the pixels, so the check is exact: a wrong or damaged
        object in storage is an error here, never a different photo on a poster.
        Written atomically, so jobs running side by side can fetch the same file.
        """
        try:
            image = decode(data, f"stored asset {sha[:12]}")
        except AssetError as exc:
            raise CorruptImage(f"The stored copy of asset {sha[:12]} is damaged.") from exc
        if address(image) != sha:
            raise CorruptImage(
                f"The stored copy of asset {sha[:12]} is a different picture, so it was not used."
            )
        target = self.fetched(sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(prefix=".fetch-", dir=target.parent)
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(tmp, target)
        return target

    @staticmethod
    def _write_index(index: Path, sha: str) -> None:
        index.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(prefix=".index-", dir=index.parent)
        with os.fdopen(handle, "w", encoding="ascii") as out:
            out.write(sha + "\n")
        os.replace(tmp, index)
