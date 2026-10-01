"""Render design-space candidates without a database: for contact sheets, the README
and the critic's training data.

Photos here are procedural scenes (soft shapes over gradients), made from a seed.
They carry no client and no licence, which is the point: the repository never holds
a real business's photo, and every image this makes can be made again exactly.

    uv run python -m stratum_worker.design.preview --client kowalski-plumbing \\
        --size 4:5 --count 24 --out sheet.png
"""

from __future__ import annotations

import argparse
import io
import json
import random
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from stratum_spec import BrandKit, retarget
from stratum_spec.models import Aspect, PosterSpec

from stratum_worker.brand import LogoError, read_logo
from stratum_worker.design.space import everything, lay_out
from stratum_worker.design.typeset import Copy
from stratum_worker.draft.compose import build_spec
from stratum_worker.draft.logo_fit import LogoInk, fit_logo
from stratum_worker.render import render_poster

FAKE_SHA = {"photo_a": "a" * 64, "photo_b": "b" * 64, "bg": "a" * 64, "logo": "c" * 64}
# Words that match the test client (a fictional plumber), so a contact sheet reads as
# one believable post rather than another client's words in this one's colours.
SAMPLE_COPY = Copy(
    headline="Boiler service, sorted before winter",
    offer="20% off every boiler service booked before 31 October.",
    cta="Call 0161 496 0000",
)


def scene(seed: int, width: int = 1600, height: int = 1200) -> Image.Image:
    """A photo-like picture from a seed: a gradient, soft discs, a little blur."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width] / max(width, height)
    base = np.stack(
        [
            0.55 + 0.35 * np.sin(3 * x + seed),
            0.5 + 0.3 * np.cos(2 * y + seed * 0.7),
            0.45 + 0.3 * np.sin(4 * x * y + seed),
        ],
        -1,
    )
    image = Image.fromarray((np.clip(base, 0, 1) * 255).astype("uint8"))
    draw = ImageDraw.Draw(image)
    for _ in range(int(rng.integers(10, 60))):
        cx, cy = int(rng.integers(0, width)), int(rng.integers(0, height))
        r = int(rng.integers(20, 180))
        colour = tuple(int(v) for v in rng.integers(20, 235, 3))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    return image.filter(ImageFilter.GaussianBlur(float(rng.uniform(2, 10))))


def bind(
    template: PosterSpec, kit: BrandKit, copy: Copy, *, logo: LogoInk | None = None
) -> PosterSpec:
    """Put words and stand-in asset ids into a laid-out design, and give the logo a
    panel wherever the draft would (M9b), so a preview is what a client would see."""
    texts = {"headline": copy.headline, "offer": copy.offer, "cta": copy.cta}
    ids = {layer.id for layer in template.layers}
    assets = {layer_id: sha for layer_id, sha in FAKE_SHA.items() if layer_id in ids}
    spec = build_spec(template, kit, texts, assets)
    if logo is None:
        return spec
    return fit_logo(spec, kit, logo, {}, ("4:5", "1:1", "9:16", "16:9"))


def ink(logo: Path) -> LogoInk | None:
    """The logo's colours, as the draft reads them."""
    try:
        return LogoInk.from_reading(read_logo(logo.read_bytes()))
    except LogoError:
        return None


def render(
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    files: dict[str, str],
    *,
    aspect: Aspect = "4:5",
    width: int = 270,
) -> Image.Image:
    """One size of a bound spec, as a small picture. `files` maps stand-in ids to paths."""
    one = retarget(spec, aspect)
    one = one.model_copy(update={"canvas": one.canvas.model_copy(update={"base_width": width})})
    png = render_poster(one, kit, assets=files, root=root)
    return Image.open(io.BytesIO(png)).convert("RGB")


def stand_ins(folder: Path, logo: Path, seed: int = 1) -> dict[str, str]:
    """Write two scenes and point the stand-in asset ids at them and the logo."""
    paths = {}
    for index, sha in enumerate(("a" * 64, "b" * 64)):
        path = folder / f"scene-{seed}-{index}.png"
        scene(seed * 10 + index).save(path)
        paths[sha] = str(path)
    paths["c" * 64] = str(logo)
    return paths


def sheet(tiles: list[Image.Image], columns: int = 6) -> Image.Image:
    width, height = max(t.width for t in tiles), max(t.height for t in tiles)
    rows = (len(tiles) + columns - 1) // columns
    out = Image.new("RGB", (columns * (width + 8) + 8, rows * (height + 8) + 8), (40, 40, 40))
    for index, tile in enumerate(tiles):
        out.paste(
            tile, (8 + (index % columns) * (width + 8), 8 + (index // columns) * (height + 8))
        )
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", default="kowalski-plumbing")
    parser.add_argument("--size", default="4:5", choices=["4:5", "1:1", "9:16", "16:9"])
    parser.add_argument("--family", default=None)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("design-sheet.png"))
    args = parser.parse_args(argv)
    root: Path = args.root
    folder = root / "clients" / args.client
    kit = BrandKit.model_validate(json.loads((folder / "brand.json").read_text()))
    with Image.open(folder / "logo.png") as logo:
        logo_aspect = logo.width / logo.height
    designs = [d.id for d in everything((args.family,) if args.family else None)]
    random.Random(args.seed).shuffle(designs)  # noqa: S311 - a reproducible sample, not a secret
    tiles = []
    logo_ink = ink(folder / "logo.png")
    with tempfile.TemporaryDirectory() as tmp:
        files = stand_ins(Path(tmp), folder / "logo.png")
        for design_id in designs:
            laid = lay_out(design_id, kit, root, copy=SAMPLE_COPY, logo_aspect=logo_aspect)
            if laid is None:
                continue
            spec = bind(laid, kit, SAMPLE_COPY, logo=logo_ink)
            tiles.append(render(spec, kit, root, files, aspect=args.size))
            if len(tiles) == args.count:
                break
    sheet(tiles).save(args.out)
    print(f"{len(tiles)} designs -> {args.out}")


if __name__ == "__main__":
    main()
