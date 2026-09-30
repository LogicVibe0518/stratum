"""Before and after: the same designs with the old even cut and with framing.

Run it on any photo you are allowed to use (your own, or a public-domain one) to see
what framing does, size by size. Each row is one design; each pair is before, after.

    uv run python -m stratum_worker.photo.sheet photo.jpg --out frames.png
    make frame-sheet PHOTO=photo.jpg

No photo is stored anywhere; the sheet is written where --out says.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image
from stratum_spec import BrandKit
from stratum_spec.models import Aspect

from stratum_worker.design.preview import SAMPLE_COPY, bind, render
from stratum_worker.design.space import everything, lay_out
from stratum_worker.photo.frame import frame_photos
from stratum_worker.photo.read import read_photo

SIZES: tuple[Aspect, ...] = ("4:5", "9:16", "16:9")
GAP = 6


def row(tiles: list[Image.Image]) -> Image.Image:
    """Pairs side by side, a wider gap between pairs."""
    height = max(tile.height for tile in tiles)
    width = sum(tile.width for tile in tiles) + GAP * 4 * len(tiles)
    out = Image.new("RGB", (width, height), "white")
    x = 0
    for index, tile in enumerate(tiles):
        out.paste(tile, (x, 0))
        x += tile.width + (GAP * 4 if index % 2 else GAP)
    return out.crop((0, 0, x, height))


def stack(rows: list[Image.Image]) -> Image.Image:
    out = Image.new(
        "RGB",
        (max(one.width for one in rows), sum(one.height + GAP * 2 for one in rows)),
        "white",
    )
    y = 0
    for one in rows:
        out.paste(one, (0, y))
        y += one.height + GAP * 2
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("photo", type=Path)
    parser.add_argument("--client", default="kowalski-plumbing")
    parser.add_argument("--families", default="overlay,panel,pair")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("frames.png"))
    args = parser.parse_args(argv)
    root: Path = args.root
    folder = root / "clients" / args.client
    kit = BrandKit.model_validate(json.loads((folder / "brand.json").read_text()))
    read = read_photo(args.photo)
    stand_in = {"a" * 64: str(args.photo), "b" * 64: str(args.photo)}
    files = {**stand_in, "c" * 64: str(folder / "logo.png")}
    reads = dict.fromkeys(stand_in, read)
    rows = []
    for family in args.families.split(","):
        designs = [one.id for one in everything((family,))]
        for design in (designs[0], designs[len(designs) // 2]):
            laid = lay_out(design, kit, root, copy=SAMPLE_COPY, logo_aspect=2.0)
            if laid is None:
                continue
            spec = bind(laid, kit, SAMPLE_COPY)
            framed = frame_photos(spec, reads, kit, root, SIZES)
            tiles = [
                render(
                    poster, kit, root, files, aspect=aspect, width=300 if aspect == "16:9" else 216
                )
                for aspect in SIZES
                for poster in (spec, framed)
            ]
            rows.append(row(tiles))
    stack(rows).save(args.out)
    faces = len(read.faces)
    print(f"{len(rows)} designs, {faces} face{'s' * (faces != 1)}, -> {args.out}")


if __name__ == "__main__":
    main()
