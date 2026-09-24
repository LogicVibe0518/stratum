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
