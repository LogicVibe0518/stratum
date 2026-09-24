from __future__ import annotations

import hashlib
import importlib
import io
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageFilter
from stratum_spec import BrandKit, PosterSpec, SwapAsset, apply_op
from stratum_worker.assets import (
    MAX_FILE_BYTES,
    MAX_PIXELS,
    AssetMeta,
    AssetNotFound,
    AssetStore,
    CorruptImage,
    FileTooLarge,
    ImageTooLarge,
    UnsupportedFileType,
    ingest,
)
from stratum_worker.render import render_poster

EXIF_ORIENTATION = 0x0112
EXIF_MAKE = 0x010F
CAMERA_MAKE = "StratumTestCam"


def _scene(width: int = 800, height: int = 800, seed: int = 7) -> Image.Image:
    """A sharp, mid-brightness test photo: random grey-ish 16px blocks."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(60, 200, size=(height // 16 + 1, width // 16 + 1, 3), dtype=np.uint8)
    pixels = np.repeat(np.repeat(blocks, 16, axis=0), 16, axis=1)[:height, :width]
    return Image.fromarray(pixels, "RGB")


def _save(image: Image.Image, path: Path, fmt: str, **params: object) -> Path:
    image.save(path, fmt, **params)
    return path


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _variant_files(store_dir: Path, sha: str) -> list[Path]:
    return sorted((store_dir / sha).iterdir())


# --- orientation, colour, alpha ---------------------------------------------------


def test_exif_orientation_6_comes_out_upright(tmp_path: Path) -> None:
    """Orientation 6 = stored sideways, display rotated 90 degrees clockwise."""
    stored = Image.new("RGB", (600, 400), (0, 0, 255))
    stored.paste((255, 0, 0), (0, 0, 150, 100))  # red marker in the stored top-left
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6
    src = _save(stored, tmp_path / "sideways.jpg", "JPEG", exif=exif, quality=95)

    meta = ingest(src, tmp_path / "store")

    assert (meta.width, meta.height) == (400, 600)
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.size == (400, 600)
        # Rotating 90 degrees clockwise moves the top-left corner to the top-right.
        red, _, blue = out.convert("RGB").getpixel((400 - 20, 20))
        assert red > 200 and blue < 60


def test_cmyk_jpeg_comes_out_rgb(tmp_path: Path) -> None:
    src = _save(Image.new("CMYK", (300, 200), (0, 255, 255, 0)), tmp_path / "print.jpg", "JPEG")

    meta = ingest(src, tmp_path / "store")

    assert not meta.has_alpha
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"
        red, green, blue = out.getpixel((150, 100))
        assert red > 200 and green < 60 and blue < 60


def test_greyscale_jpeg_comes_out_rgb(tmp_path: Path) -> None:
    src = _save(Image.new("L", (300, 200), 128), tmp_path / "grey.jpg", "JPEG")

    meta = ingest(src, tmp_path / "store")

    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"


def test_embedded_icc_profile_is_applied_then_dropped(tmp_path: Path) -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _save(_scene(300, 300), tmp_path / "tagged.jpg", "JPEG", icc_profile=srgb)

    meta = ingest(src, tmp_path / "store")

    assert "colour_profile_ignored" not in meta.flags
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"
        assert "icc_profile" not in out.info


def test_broken_colour_profile_falls_back_and_is_flagged(tmp_path: Path) -> None:
    src = _save(_scene(), tmp_path / "whatsapp.jpg", "JPEG", icc_profile=b"not a real profile")

    meta = ingest(src, tmp_path / "store")

    assert "colour_profile_ignored" in meta.flags
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"
        assert "icc_profile" not in out.info


def test_mismatched_colour_profile_falls_back_and_is_flagged(tmp_path: Path) -> None:
    """An RGB profile on a CMYK image cannot be applied; that is not a decode failure."""
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    cmyk = Image.new("CMYK", (300, 200), (0, 255, 255, 0))
    meta = ingest(_save(cmyk, tmp_path / "odd.jpg", "JPEG", icc_profile=srgb), tmp_path / "store")

    assert "colour_profile_ignored" in meta.flags


def test_broken_profile_does_not_hide_an_undecodable_image(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    _scene().save(buffer, "JPEG", icc_profile=b"not a real profile")
    src = tmp_path / "cut_off.jpg"
    src.write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 2])

    with pytest.raises(CorruptImage, match="could not be decoded"):
        ingest(src, tmp_path / "store")


def test_16bit_greyscale_png_comes_out_8bit_rgb(tmp_path: Path) -> None:
    deep = Image.fromarray(np.full((200, 300), 40000, dtype=np.uint16))
    meta = ingest(_save(deep, tmp_path / "deep.png", "PNG"), tmp_path / "store")

    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"
        assert out.getpixel((10, 10)) == (156, 156, 156)  # 40000 / 257


def test_palette_png_keeps_transparency(tmp_path: Path) -> None:
    logo = Image.new("P", (200, 100), 0)
    logo.putpalette([255, 255, 255, 200, 30, 30] + [0, 0, 0] * 254)
    logo.paste(1, (50, 25, 150, 75))
    src = _save(logo, tmp_path / "logo.png", "PNG", transparency=0)

    meta = ingest(src, tmp_path / "store")

    assert meta.has_alpha
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGBA"
        assert out.getpixel((5, 5))[3] == 0
        assert out.getpixel((100, 50)) == (200, 30, 30, 255)


def test_opaque_alpha_channel_is_flattened(tmp_path: Path) -> None:
    src = _save(_scene(300, 300).convert("RGBA"), tmp_path / "opaque.png", "PNG")

    meta = ingest(src, tmp_path / "store")

    assert not meta.has_alpha
    with Image.open(tmp_path / "store" / meta.sha256 / "full.png") as out:
        assert out.mode == "RGB"


# --- file type and limits ---------------------------------------------------------


def test_jpeg_with_png_extension_is_accepted_by_content(tmp_path: Path) -> None:
    src = _save(_scene(), tmp_path / "actually_a_jpeg.png", "JPEG")

    meta = ingest(src, tmp_path / "store")

    assert (meta.width, meta.height) == (800, 800)


def test_text_file_renamed_to_jpg_is_rejected(tmp_path: Path) -> None:
    src = tmp_path / "invoice.jpg"
    src.write_text("definitely not a photo\n", encoding="utf-8")

    with pytest.raises(UnsupportedFileType, match="not a JPEG, PNG or WebP"):
        ingest(src, tmp_path / "store")
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").iterdir())


def test_truncated_jpeg_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    _scene().save(buffer, "JPEG")
    src = tmp_path / "cut_off.jpg"
    src.write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 2])

    with pytest.raises(CorruptImage, match="could not be decoded"):
        ingest(src, tmp_path / "store")


def test_heic_is_rejected_with_a_helpful_message(tmp_path: Path) -> None:
    src = tmp_path / "IMG_0042.jpg"  # iPhones often keep .jpg on AirDropped HEICs
    src.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64)

    with pytest.raises(UnsupportedFileType, match=r"^HEIC is not supported yet - export as JPEG$"):
        ingest(src, tmp_path / "store")


def _png_header_only(width: int, height: int) -> bytes:
    """A valid PNG signature and IHDR with no pixel data at all."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_pillow_pixel_limit_is_ours() -> None:
    assert Image.MAX_IMAGE_PIXELS == MAX_PIXELS == 8000 * 8000


def test_decompression_bomb_is_rejected_from_the_header(tmp_path: Path) -> None:
    """A few hundred bytes claiming 2.5 billion pixels. There is no pixel data, so if
    this reached the decoder it would fail as CorruptImage instead."""
    src = tmp_path / "bomb.png"
    src.write_bytes(_png_header_only(50_000, 50_000))

    with pytest.raises(ImageTooLarge):
        ingest(src, tmp_path / "store")


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_header_over_8000px_is_rejected_before_decode(tmp_path: Path) -> None:
    src = tmp_path / "wide.png"
    src.write_bytes(_png_header_only(9000, 100))

    with pytest.raises(ImageTooLarge, match="9000x100"):
        ingest(src, tmp_path / "store")


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_image_over_8000px_is_rejected(tmp_path: Path) -> None:
    # Mode "1" keeps the fixture cheap: ~10 MB in memory, a few KB on disk.
    src = _save(Image.new("1", (9000, 9000), 1), tmp_path / "huge.png", "PNG")

    with pytest.raises(ImageTooLarge, match="9000x9000"):
        ingest(src, tmp_path / "store")


def test_file_over_25mb_is_rejected(tmp_path: Path) -> None:
    src = tmp_path / "enormous.jpg"
    with src.open("wb") as handle:
        handle.write(b"\xff\xd8\xff")
        handle.truncate(MAX_FILE_BYTES + 1)  # sparse: no real 25 MB written

    with pytest.raises(FileTooLarge):
        ingest(src, tmp_path / "store")


# --- quality flags ----------------------------------------------------------------


def test_small_image_is_flagged_too_small(tmp_path: Path) -> None:
    src = _save(_scene(40, 40), tmp_path / "tiny.png", "PNG")

    meta = ingest(src, tmp_path / "store")

    assert "too_small" in meta.flags
    assert meta.quality["min_dimension"] == 40


def test_blurred_image_is_flagged_and_sharp_one_is_not(tmp_path: Path) -> None:
    sharp = _scene()
    blurred = sharp.filter(ImageFilter.GaussianBlur(radius=12))
    sharp_meta = ingest(_save(sharp, tmp_path / "sharp.png", "PNG"), tmp_path / "store")
    blurred_meta = ingest(_save(blurred, tmp_path / "blurred.png", "PNG"), tmp_path / "store")

    assert "too_blurry" in blurred_meta.flags
    assert "too_blurry" not in sharp_meta.flags
    assert sharp_meta.flags == []
    assert sharp_meta.quality["sharpness"] > blurred_meta.quality["sharpness"]


def test_dark_image_is_flagged_too_dark(tmp_path: Path) -> None:
    dark = Image.eval(_scene(), lambda value: value // 8)
    meta = ingest(_save(dark, tmp_path / "night.png", "PNG"), tmp_path / "store")

    assert "too_dark" in meta.flags
    assert meta.quality["brightness"] < 40


# --- normalisation, privacy, content addressing ---------------------------------


def test_exif_is_absent_from_every_output(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 1
    exif[EXIF_MAKE] = CAMERA_MAKE
    src = _save(_scene(), tmp_path / "phone.jpg", "JPEG", exif=exif)
    assert CAMERA_MAKE.encode() in src.read_bytes()  # the fixture really carries EXIF

    meta = ingest(src, tmp_path / "store")

    for path in _variant_files(tmp_path / "store", meta.sha256):
        raw = path.read_bytes()
        assert CAMERA_MAKE.encode() not in raw
        assert b"Exif" not in raw and b"eXIf" not in raw
        if path.suffix == ".png":
            with Image.open(path) as out:
                assert len(out.getexif()) == 0
                assert "exif" not in out.info


def test_outputs_have_expected_variants_and_sizes(tmp_path: Path) -> None:
    src = _save(_scene(2400, 1600), tmp_path / "big.jpg", "JPEG")

    meta = ingest(src, tmp_path / "store")

    folder = tmp_path / "store" / meta.sha256
    assert {p.name for p in folder.iterdir()} == {"full.png", "web.png", "thumb.png", "meta.json"}
    with Image.open(folder / "full.png") as full:
        assert full.size == (2400, 1600)
    with Image.open(folder / "web.png") as web:
        assert web.size == (1080, 720)
    with Image.open(folder / "thumb.png") as thumb:
        assert thumb.size == (512, 341)
    assert AssetMeta.model_validate_json((folder / "meta.json").read_text()) == meta


def test_small_image_is_never_upscaled(tmp_path: Path) -> None:
    meta = ingest(_save(_scene(300, 200), tmp_path / "s.png", "PNG"), tmp_path / "store")

    with Image.open(tmp_path / "store" / meta.sha256 / "web.png") as web:
        assert web.size == (300, 200)


def test_sha_is_of_normalised_pixels_not_file_bytes(tmp_path: Path) -> None:
    """The same pixels in two containers are one asset."""
    scene = _scene(300, 300)
    as_png = ingest(_save(scene, tmp_path / "a.png", "PNG"), tmp_path / "store")
    as_webp = ingest(_save(scene, tmp_path / "a.webp", "WEBP", lossless=True), tmp_path / "store")

    assert as_png.sha256 == as_webp.sha256


def test_ingesting_twice_is_idempotent(tmp_path: Path) -> None:
    src = _save(_scene(), tmp_path / "van.jpg", "JPEG")
    store_dir = tmp_path / "store"

    first = ingest(src, store_dir)
    stamps = {p: p.stat().st_mtime_ns for p in _variant_files(store_dir, first.sha256)}
    second = ingest(src, store_dir)

    assert second == first
    assert {p: p.stat().st_mtime_ns for p in _variant_files(store_dir, first.sha256)} == stamps
    assert [p.name for p in store_dir.iterdir()] == [first.sha256]


def test_input_file_is_never_modified(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6
    src = _save(_scene(), tmp_path / "original.jpg", "JPEG", exif=exif)
    before, mtime = _sha_file(src), src.stat().st_mtime_ns

    ingest(src, tmp_path / "store")

    assert _sha_file(src) == before
    assert src.stat().st_mtime_ns == mtime


def test_sha_includes_the_normalisation_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _save(_scene(300, 300), tmp_path / "s.png", "PNG")
    current = ingest(src, tmp_path / "a")
    module = importlib.import_module("stratum_worker.assets.ingest")
    monkeypatch.setattr(module, "NORMALISATION_VERSION", b"n2")

    assert ingest(src, tmp_path / "b").sha256 != current.sha256


def test_source_is_recorded(tmp_path: Path) -> None:
    meta = ingest(_save(_scene(), tmp_path / "s.png", "PNG"), tmp_path / "store", source="stock")

    assert meta.sources == frozenset({"stock"})


def test_new_source_for_same_pixels_is_added_not_discarded(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    scene = _scene()
    first = ingest(_save(scene, tmp_path / "from_client.png", "PNG"), store_dir, source="client")
    again = ingest(_save(scene, tmp_path / "from_stock.png", "PNG"), store_dir, source="stock")

    assert again.sha256 == first.sha256
    assert again.sources == frozenset({"client", "stock"})
    assert AssetStore(store_dir).meta_for(first.sha256).sources == {"client", "stock"}
    assert again.model_copy(update={"sources": first.sources}) == first  # nothing else moved


def test_repeat_source_does_not_rewrite_meta(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    src = _save(_scene(), tmp_path / "s.png", "PNG")
    meta = ingest(src, store_dir, source="client")
    meta_path = store_dir / meta.sha256 / "meta.json"
    stamp = meta_path.stat().st_mtime_ns

    ingest(src, store_dir, source="client")

    assert meta_path.stat().st_mtime_ns == stamp


def test_meta_json_lists_sources_in_a_stable_order(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    src = _save(_scene(), tmp_path / "s.png", "PNG")
    for source in ("stock", "generated", "client"):
        meta = ingest(src, store_dir, source=source)

    text = (store_dir / meta.sha256 / "meta.json").read_text()
    assert '"sources": [\n    "client",\n    "generated",\n    "stock"\n  ]' in text


def test_store_put_same_file_with_new_source_adds_it(tmp_path: Path) -> None:
    """The source index fast path must not skip recording the new source."""
    store = AssetStore(tmp_path / "store")
    src = _save(_scene(), tmp_path / "s.jpg", "JPEG")
    store.put(src, source="client")

    assert store.put(src, source="stock").sources == {"client", "stock"}


def test_asset_meta_is_frozen(tmp_path: Path) -> None:
    meta = ingest(_save(_scene(), tmp_path / "s.png", "PNG"), tmp_path / "store")

    with pytest.raises(ValueError, match="frozen"):
        meta.width = 1  # type: ignore[misc]


# --- AssetStore -------------------------------------------------------------------


def test_store_put_path_for_and_meta_for(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "store")
    meta = store.put(_save(_scene(), tmp_path / "s.jpg", "JPEG"))

    assert store.meta_for(meta.sha256) == meta
    for variant in ("full", "web", "thumb"):
        assert store.path_for(meta.sha256, variant).is_file()


def test_store_put_twice_skips_decoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AssetStore(tmp_path / "store")
    src = _save(_scene(), tmp_path / "s.jpg", "JPEG")
    first = store.put(src)

    def _fail(*_: object, **__: object) -> AssetMeta:
        raise AssertionError("second put must not re-ingest")

    monkeypatch.setattr("stratum_worker.assets.store.ingest", _fail)
    assert store.put(src) == first


def test_store_unknown_sha_raises(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "store")

    with pytest.raises(AssetNotFound):
        store.meta_for("0" * 64)
    with pytest.raises(AssetNotFound):
        store.path_for("0" * 64, "full")


def test_store_rejects_non_sha_ids(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "store")

    with pytest.raises(ValueError, match="sha256"):
        store.path_for("../../etc/passwd", "full")


# --- rendering with a bound asset -------------------------------------------------


def test_bound_asset_changes_the_render(
    tmp_path: Path, offer: PosterSpec, kit: BrandKit, repo_root: Path
) -> None:
    store = AssetStore(tmp_path / "store")
    meta = store.put(_save(_scene(1080, 1350), tmp_path / "job.jpg", "JPEG"))
    bound = apply_op(offer, SwapAsset(layer="bg", asset=meta.sha256))

    without = render_poster(offer, kit, root=repo_root)
    with_asset = render_poster(
        bound, kit, assets={meta.sha256: str(store.path_for(meta.sha256, "full"))}, root=repo_root
    )

    assert with_asset != without
    top_left = Image.open(io.BytesIO(with_asset)).convert("RGB").getpixel((5, 5))
    placeholder = Image.open(io.BytesIO(without)).convert("RGB").getpixel((5, 5))
    assert top_left != placeholder


def test_cli_binds_asset(tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stratum_worker.cli import main

    monkeypatch.chdir(repo_root)
    photo = _save(_scene(1080, 1350), tmp_path / "job.jpg", "JPEG")
    args = [
        str(repo_root / "templates" / "offer_4x5.json"),
        "--brand",
        str(repo_root / "brandkits" / "sharmas_dhaba.json"),
        "--store",
        str(tmp_path / "store"),
    ]
    plain, bound = tmp_path / "plain.png", tmp_path / "bound.png"

    assert main([*args, "--out", str(plain)]) == 0
    assert main([*args, "--out", str(bound), "--asset", f"bg={photo}"]) == 0
    assert plain.read_bytes() != bound.read_bytes()


def test_cli_rejects_asset_on_text_layer(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stratum_worker.cli import main

    monkeypatch.chdir(repo_root)
    photo = _save(_scene(), tmp_path / "job.jpg", "JPEG")
    with pytest.raises(SystemExit, match="holds no asset"):
        main(
            [
                str(repo_root / "templates" / "offer_4x5.json"),
                "--brand",
                str(repo_root / "brandkits" / "sharmas_dhaba.json"),
                "--store",
                str(tmp_path / "store"),
                "--out",
                str(tmp_path / "x.png"),
                "--asset",
                f"headline={photo}",
            ]
        )
