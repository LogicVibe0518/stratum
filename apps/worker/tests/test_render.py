from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat
from stratum_spec import BrandKit, PosterSpec, SetText, UnknownToken, apply_ops
from stratum_worker.render import build_svg, render_poster

GOLDEN = Path(__file__).parent / "golden" / "offer_4x5.png"
MAX_MEAN_DIFF = 2.0  # tolerance for font rasterisation differences across platforms


def _image(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_render_matches_canvas_size(offer: PosterSpec, kit: BrandKit, repo_root: Path) -> None:
    png = render_poster(offer, kit, root=repo_root)
    assert _image(png).size == offer.canvas.size


def test_render_is_deterministic(offer: PosterSpec, kit: BrandKit, repo_root: Path) -> None:
    assert render_poster(offer, kit, root=repo_root) == render_poster(offer, kit, root=repo_root)


def test_render_is_not_blank(offer: PosterSpec, kit: BrandKit, repo_root: Path) -> None:
    """A render that is one flat colour means the pipeline silently did nothing."""
    image = _image(render_poster(offer, kit, root=repo_root))
    colours = image.getcolors(maxcolors=1 << 20)
    assert colours is not None
    assert len(colours) > 50, "render looks blank"
    flat = max(count for count, _ in colours) / (image.size[0] * image.size[1])
    assert flat < 0.9, "one colour covers almost the whole canvas"


def test_brand_primary_is_actually_painted(
    offer: PosterSpec, kit: BrandKit, repo_root: Path
) -> None:
    """Sample a point inside the brand band: the colour must be the brand's own.

    The band is drawn at 0.92 opacity, so compare with a tolerance rather than
    demanding an exact match.
    """
    primary = kit.color("palette.primary").lstrip("#")
    expected = [int(primary[i : i + 2], 16) for i in (0, 2, 4)]
    image = _image(render_poster(offer, kit, root=repo_root))
    sampled = image.getpixel((1040, 800))
    assert isinstance(sampled, tuple)
    for channel, (got, want) in enumerate(zip(sampled, expected, strict=True)):
        assert abs(got - want) <= 25, f"channel {channel}: {sampled} vs {expected}"


def test_brand_colours_reach_the_svg(offer: PosterSpec, kit: BrandKit, repo_root: Path) -> None:
    svg = build_svg(offer, kit, root=repo_root)
    assert kit.color("palette.primary") in svg
    assert "palette." not in svg  # tokens are resolved, never leaked into output


def test_missing_token_is_an_error(offer: PosterSpec, repo_root: Path) -> None:
    broken = BrandKit.model_validate(
        {
            "client_id": "broken",
            "palette": {"text": "#000000"},
            "fonts": {
                "display": {"family": "Anton", "file": "assets/fonts/Anton-Regular.ttf"},
                "body": {"family": "Barlow", "file": "assets/fonts/Barlow-Regular.ttf"},
            },
        }
    )
    with pytest.raises(UnknownToken):
        build_svg(offer, broken, root=repo_root)


def test_golden_offer_poster(offer: PosterSpec, kit: BrandKit, repo_root: Path) -> None:
    """Catches unintended visual changes: any layout drift shows up here."""
    spec = apply_ops(
        offer,
        [
            SetText(layer="headline", text="20% off every thali"),
            SetText(layer="offer", text="Dine in, Friday to Sunday"),
            SetText(layer="cta", text="Book on 020 7946 0102"),
        ],
    )
    png = render_poster(spec, kit, root=repo_root)
    if not GOLDEN.exists():  # first run writes the baseline
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(png)
        pytest.skip("golden image created")
    diff = ImageChops.difference(_image(png), _image(GOLDEN.read_bytes()))
    # ImageStat gives a per-channel mean; histogram() buckets all three channels
    # into one 768-long list, which is easy to average wrongly.
    mean = max(ImageStat.Stat(diff).mean)
    assert mean <= MAX_MEAN_DIFF, f"render drifted from golden (mean diff {mean:.3f})"


FAKE_SHA = "a" * 64


def _checker(path: Path, size: int = 512, cell: int = 16) -> Path:
    """High-contrast photo stand-in: blur and dim both change it visibly."""
    image = Image.new("RGB", (size, size), (255, 255, 255))
    for top in range(0, size, cell):
        for left in range((top // cell) % 2 * cell, size, cell * 2):
            image.paste((20, 20, 20), (left, top, left + cell, top + cell))
    image.save(path, "PNG")
    return path


def _with_layer(spec: PosterSpec, layer_id: str, **changes: object) -> PosterSpec:
    layers = [
        layer.model_copy(update=changes) if layer.id == layer_id else layer for layer in spec.layers
    ]
    return spec.model_copy(update={"layers": layers})


def _inset_spec(**changes: object) -> PosterSpec:
    """A light canvas with one photo in the middle and nothing else."""
    return PosterSpec.model_validate(
        {
            "template_id": "inset_test",
            "canvas": {"aspect": "1:1", "base_width": 400},
            "layers": [
                {
                    "type": "shape",
                    "id": "paper",
                    "box": {"x": 0, "y": 0, "w": 1, "h": 1},
                    "fill": "palette.text_on_primary",
                },
                {
                    "type": "image",
                    "id": "photo",
                    "asset": FAKE_SHA,
                    "box": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
                    **changes,
                },
            ],
        }
    )


def _luma(png: bytes, xy: tuple[int, int]) -> int:
    value = Image.open(io.BytesIO(png)).convert("L").getpixel(xy)
    assert isinstance(value, int)
    return value


def test_dim_darkens_the_image(
    offer: PosterSpec, kit: BrandKit, repo_root: Path, tmp_path: Path
) -> None:
    assets = {FAKE_SHA: str(_checker(tmp_path / "photo.png"))}
    bound = _with_layer(offer, "bg", asset=FAKE_SHA)
    bright = render_poster(_with_layer(bound, "bg", dim=0.0), kit, assets=assets, root=repo_root)
    dimmed = render_poster(_with_layer(bound, "bg", dim=0.5), kit, assets=assets, root=repo_root)

    for xy in [(20, 20), (300, 300)]:
        assert _luma(dimmed, xy) < _luma(bright, xy) - 40


def test_blur_zero_is_identical_to_no_filter(
    kit: BrandKit, repo_root: Path, tmp_path: Path
) -> None:
    assets = {FAKE_SHA: str(_checker(tmp_path / "photo.png"))}
    no_field, zero = _inset_spec(), _inset_spec(blur=0.0)

    svg = build_svg(zero, kit, assets=assets, root=repo_root)
    assert "feGaussianBlur" not in svg and "clip-path" not in svg
    assert svg == build_svg(no_field, kit, assets=assets, root=repo_root)
    assert render_poster(zero, kit, assets=assets, root=repo_root) == render_poster(
        no_field, kit, assets=assets, root=repo_root
    )


def test_blur_softens_inside_and_stays_inside_the_box(
    kit: BrandKit, repo_root: Path, tmp_path: Path
) -> None:
    assets = {FAKE_SHA: str(_checker(tmp_path / "photo.png"))}
    sharp = Image.open(io.BytesIO(render_poster(_inset_spec(), kit, assets=assets, root=repo_root)))
    soft = Image.open(
        io.BytesIO(render_poster(_inset_spec(blur=0.5), kit, assets=assets, root=repo_root))
    )
    sharp_rgb, soft_rgb = sharp.convert("RGB"), soft.convert("RGB")

    inside = (150, 150, 250, 250)
    sharp_low, sharp_high = sharp_rgb.convert("L").crop(inside).getextrema()
    soft_low, soft_high = soft_rgb.convert("L").crop(inside).getextrema()
    assert sharp_high - sharp_low > 200
    assert soft_high - soft_low < 60, "a blurred checkerboard should be nearly flat grey"
    # Everything outside the 100..300 box must be untouched by the blur.
    for box in [(0, 0, 400, 98), (0, 302, 400, 400), (0, 0, 98, 400), (302, 0, 400, 400)]:
        diff = ImageChops.difference(sharp_rgb.crop(box), soft_rgb.crop(box))
        assert diff.getbbox() is None, f"blur bled outside the layer box into {box}"


def test_cli_writes_a_png(tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stratum_worker.cli import main

    monkeypatch.chdir(repo_root)
    out = tmp_path / "poster.png"
    code = main(
        [
            str(repo_root / "templates" / "offer_4x5.json"),
            "--brand",
            str(repo_root / "brandkits" / "sharmas_dhaba.json"),
            "--out",
            str(out),
            "--text",
            "headline=Fresh chole bhature",
        ]
    )
    assert code == 0
    assert _image(out.read_bytes()).size == (1080, 1350)
