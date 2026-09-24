"""`stratum-render`: turn a template plus a brand kit into a PNG.

The smallest end-to-end path through the system, useful for demos and for the
benchmark harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stratum_spec import (
    BrandKit,
    PosterSpec,
    SetText,
    SwapAsset,
    apply_op,
    apply_ops,
    load_template,
)

from stratum_worker.assets import AssetStore
from stratum_worker.render import render_poster

DEFAULT_STORE = Path(".stratum/assets")


def _overrides(pairs: list[str]) -> list[SetText]:
    ops: list[SetText] = []
    for pair in pairs:
        layer, _, text = pair.partition("=")
        if not layer or not text:
            raise SystemExit(f"--text expects layer=value, got {pair!r}")
        ops.append(SetText(layer=layer, text=text))
    return ops


def _bind_assets(
    spec: PosterSpec, pairs: list[str], store: AssetStore
) -> tuple[PosterSpec, dict[str, str]]:
    """Ingest each photo and point its layer at the resulting asset id."""
    assets: dict[str, str] = {}
    for pair in pairs:
        layer, _, raw_path = pair.partition("=")
        if not layer or not raw_path:
            raise SystemExit(f"--asset expects layer=path, got {pair!r}")
        try:
            meta = store.put(Path(raw_path))
            spec = apply_op(spec, SwapAsset(layer=layer, asset=meta.sha256))
        except ValueError as exc:  # AssetError, OpError and ValidationError
            raise SystemExit(f"--asset {layer}: {exc}") from exc
        for flag in meta.flags:
            print(f"warning: photo for {layer!r} is {flag.replace('_', ' ')}", file=sys.stderr)
        assets[meta.sha256] = str(store.path_for(meta.sha256, "full").resolve())
    return spec, assets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratum-render", description=__doc__)
    parser.add_argument("template", type=Path, help="path to a template JSON file")
    parser.add_argument("--brand", type=Path, required=True, help="path to a brand kit JSON file")
    parser.add_argument("--out", type=Path, default=Path("poster.png"))
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        metavar="LAYER=VALUE",
        help="replace a text layer, e.g. --text headline='20% off this week'",
    )
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        metavar="LAYER=PATH",
        help="bind a photo to an image, cutout or logo layer, e.g. --asset bg=van.jpg",
    )
    parser.add_argument(
        "--store", type=Path, default=DEFAULT_STORE, help=f"asset store (default {DEFAULT_STORE})"
    )
    parser.add_argument("--svg", type=Path, help="also write the SVG here")
    args = parser.parse_args(argv)

    spec: PosterSpec = load_template(args.template)
    spec = apply_ops(spec, list(_overrides(args.text)))
    spec, assets = _bind_assets(spec, args.asset, AssetStore(args.store))
    kit = BrandKit.model_validate(json.loads(args.brand.read_text(encoding="utf-8")))

    png = render_poster(spec, kit, assets=assets, root=Path.cwd())
    args.out.write_bytes(png)
    if args.svg:
        from stratum_worker.render import build_svg

        args.svg.write_text(build_svg(spec, kit, assets=assets, root=Path.cwd()), encoding="utf-8")
    print(f"wrote {args.out} ({len(png)} bytes, {spec.canvas.size[0]}x{spec.canvas.size[1]})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
