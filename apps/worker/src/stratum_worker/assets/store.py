"""Local, content-addressed asset store.

Layout under `root`:
    <sha256>/full.png, web.png, thumb.png, meta.json   one normalised asset
    _sources/<sha256 of uploaded bytes>                 the asset id it became

The source index lets a re-sent photo skip decoding entirely. Cloud storage is a
later module; it only has to keep this layout.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from stratum_worker.assets.errors import AssetNotFound
from stratum_worker.assets.ingest import add_source, ingest, load_meta, read_source
from stratum_worker.assets.models import VARIANTS, AssetMeta, Source, Variant

SOURCES_DIR = "_sources"
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

    @staticmethod
    def _write_index(index: Path, sha: str) -> None:
        index.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(prefix=".index-", dir=index.parent)
        with os.fdopen(handle, "w", encoding="ascii") as out:
            out.write(sha + "\n")
        os.replace(tmp, index)
