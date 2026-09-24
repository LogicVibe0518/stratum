"""SVG -> PNG with resvg.

Deterministic on purpose: only the fonts bundled in assets/fonts are loaded, never
the system's, so a render is identical on a Mac, in CI and on a server.
"""

from __future__ import annotations

from pathlib import Path

from resvg import render, usvg

# Row-major affine: (sx, kx, tx, ky, sy, ty). Writing this as (1,0,0,1,0,0) sets
# sy = 0, which silently renders an empty image - it cost an hour once.
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def rasterize(svg: str, *, font_dir: Path, resources_dir: Path | None = None) -> bytes:
    options = usvg.Options.default()
    options.load_fonts_dir(str(font_dir))
    if resources_dir is not None:
        options.resources_dir = str(resources_dir)
    tree = usvg.Tree.from_str(svg, options)
    return bytes(render(tree, IDENTITY))
