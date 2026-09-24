"""Stratum compositor: spec -> SVG -> PNG."""

from __future__ import annotations

from pathlib import Path

from stratum_spec import BrandKit, PosterSpec

from stratum_worker.render.raster import rasterize
from stratum_worker.render.svg import build_svg

DEFAULT_FONT_DIR = Path("assets/fonts")


def render_poster(
    spec: PosterSpec,
    kit: BrandKit,
    *,
    assets: dict[str, str] | None = None,
    root: Path | None = None,
) -> bytes:
    """One poster, one PNG. Same inputs always produce the same bytes."""
    root = root or Path.cwd()
    svg = build_svg(spec, kit, assets=assets, root=root)
    return rasterize(svg, font_dir=root / DEFAULT_FONT_DIR, resources_dir=root)


__all__ = ["DEFAULT_FONT_DIR", "build_svg", "rasterize", "render_poster"]
