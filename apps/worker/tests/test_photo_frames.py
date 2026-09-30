"""M10b: the photo decides the crop.

Before this, every photo was cut evenly from both sides. A 3:2 phone photo in a 4:5
post lost a sixth of each side whatever was there, a story lost more than half, and
the headline landed wherever the template put it, over a face as often as over sky.

These tests pin the four parts down:
- geometry: the crop window, and SVG and PowerPoint drawing the same window;
- the read: faces (YuNet), what stands out, and how sure we are there is a subject;
- the choice: faces whole and clear of the words, the subject kept, the words over
  the calm part, and no change at all when there is nothing to gain;
- the product: a draft stores its crops, a swapped photo is framed again, and a face
  that no crop can save is flagged for a person.

The photos are drawn here (no real person's photo is allowed in the repository), so
tests that need a face give the read a face directly; the detector itself is tested
on its decoding, its file check and on scenes that must NOT contain a face.
"""

from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image, ImageDraw
from pptx import Presentation
from stratum_spec import (
    Box,
    BrandKit,
    Canvas,
    Crop,
    ImageLayer,
    LayoutOverride,
    LogoLayer,
    PosterSpec,
    ShapeLayer,
    TextLayer,
    retarget,
)
from stratum_worker.export import export_pptx
from stratum_worker.photo import faces as face_model
from stratum_worker.photo.faces import Face, FaceModelError, decode, distinct, find_faces
from stratum_worker.photo.frame import (
    Cover,
    choose_crop,
    covers_over,
    face_flags,
    frame_photos,
)
from stratum_worker.photo.read import PhotoRead, read_image
from stratum_worker.render import render_poster
from stratum_worker.render.layout import crop_window
from stratum_worker.render.svg import build_svg

PHOTO = "a" * 64


# --- drawn photos ------------------------------------------------------------------------


def disc_scene(width: int = 1500, height: int = 1000, *, at: float = 0.25) -> Image.Image:
    """A red disc on a plain, slightly textured wall: one clear subject at x=`at`."""
    rng = np.random.default_rng(3)
    wall = 205 + rng.integers(-4, 5, (height, width, 1))
    image = Image.fromarray(np.repeat(wall, 3, axis=2).astype("uint8"))
    radius = height // 6
    cx, cy = int(at * width), height // 2
    ImageDraw.Draw(image).ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], fill=(200, 30, 30)
    )
    return image


def half_busy(width: int = 1500, height: int = 1000, *, busy_left: bool = True) -> Image.Image:
    """Fine checks on one half (brickwork), flat colour on the other (sky)."""
    y, x = np.mgrid[0:height, 0:width]
    checks = ((x // 6) + (y // 6)) % 2 == 1
    busy_side = x < width // 2 if busy_left else x >= width // 2
    grey = np.where(busy_side & checks, 60, 170).astype("uint8")
    return Image.fromarray(np.stack([grey] * 3, axis=-1))


def read_with(
    size: tuple[int, int], *, faces: tuple[Face, ...] = (), grid: tuple[int, int] = (32, 48)
) -> PhotoRead:
    """A read with no subject and no busyness, and the given faces."""
    rows, cols = grid
    return PhotoRead(
        size=size,
        faces=faces,
        salience=np.full((rows, cols), 1.0 / (rows * cols)),
        busy=np.zeros((rows, cols)),
        strength=0.0,
    )


def words(x: float, y: float, w: float, h: float) -> Cover:
    return Cover(x, y, w, h, weight=1.0, words=1.0)


def shown(crop: Crop, size: tuple[int, int], box: tuple[float, float]) -> tuple[float, ...]:
    """The crop window as fractions of the photo."""
    x, y, w, h = crop_window(size, box, crop)
    return x / size[0], y / size[1], w / size[0], h / size[1]


def in_box(point: tuple[float, float], window: tuple[float, ...]) -> tuple[float, float]:
    """Where a point of the photo lands in the box, as fractions of the box."""
    wx, wy, ww, wh = window
    return (point[0] - wx) / ww, (point[1] - wy) / wh


# --- geometry ------------------------------------------------------------------------------


def test_no_crop_is_the_even_cut_every_poster_had_before() -> None:
    # A wide photo in a tall box loses the same from each side.
    x, y, w, h = crop_window((1500, 1000), (1080, 1350), None)
    assert (y, h) == (0.0, 1000.0)
    assert w == pytest.approx(800.0)
    assert x == pytest.approx(350.0)
    # A tall photo in a wide box loses the same from top and bottom.
    x, y, w, h = crop_window((1000, 1500), (1920, 1080), None)
    assert (x, w) == (0.0, 1000.0)
    assert y == pytest.approx((1500 - 562.5) / 2)


def test_a_crop_moves_the_window_and_never_past_the_edge() -> None:
    left = crop_window((1500, 1000), (1080, 1350), Crop(x=0.0))
    right = crop_window((1500, 1000), (1080, 1350), Crop(x=1.0))
    assert left[0] == 0.0
    assert right[0] + right[2] == pytest.approx(1500.0)


def test_zoom_shows_less_of_the_photo_about_the_same_centre() -> None:
    whole = crop_window((1500, 1000), (1080, 1350), Crop(zoom=1.0))
    zoomed = crop_window((1500, 1000), (1080, 1350), Crop(zoom=1.25))
    assert zoomed[2] == pytest.approx(whole[2] / 1.25)
    assert zoomed[0] + zoomed[2] / 2 == pytest.approx(whole[0] + whole[2] / 2)


@given(
    width=st.integers(50, 5000),
    height=st.integers(50, 5000),
    box_w=st.floats(50, 3000),
    box_h=st.floats(50, 3000),
    x=st.floats(0, 1),
    y=st.floats(0, 1),
    zoom=st.floats(1, 3),
)
def test_the_window_is_always_inside_the_photo_and_has_the_box_shape(
    width: int, height: int, box_w: float, box_h: float, x: float, y: float, zoom: float
) -> None:
    wx, wy, ww, wh = crop_window((width, height), (box_w, box_h), Crop(x=x, y=y, zoom=zoom))
    assert wx >= -1e-6 and wx + ww <= width + 1e-6
    assert wy >= -1e-6 and wy + wh <= height + 1e-6
    assert ww / wh == pytest.approx(box_w / box_h, rel=1e-6)


def _one_photo_poster(crop: Crop | None) -> PosterSpec:
    return PosterSpec(
        template_id="photo_only",
        canvas=Canvas(aspect="4:5", base_width=270),
        layers=[ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1), asset=PHOTO, crop=crop)],
    )


@pytest.fixture
def halves(tmp_path: Path) -> str:
    """Red on the left, blue on the right: which half shows is which colour we see."""
    image = Image.new("RGB", (1500, 1000), (220, 20, 20))
    ImageDraw.Draw(image).rectangle([750, 0, 1500, 1000], fill=(20, 20, 220))
    path = tmp_path / "halves.png"
    image.save(path)
    return str(path)


def _centre_colour(png: bytes) -> tuple[int, ...]:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    return tuple(image.getpixel((image.width // 2, image.height // 2)))  # type: ignore[arg-type]


def test_the_renderer_draws_the_part_of_the_photo_the_crop_names(
    halves: str, kit: BrandKit, repo_root: Path
) -> None:
    assets = {PHOTO: halves}
    left = render_poster(_one_photo_poster(Crop(x=0.0)), kit, assets=assets, root=repo_root)
    right = render_poster(_one_photo_poster(Crop(x=1.0)), kit, assets=assets, root=repo_root)
    red, blue = _centre_colour(left), _centre_colour(right)
    assert red[0] > 180 and red[2] < 60
    assert blue[2] > 180 and blue[0] < 60


def test_a_poster_without_a_crop_is_drawn_exactly_as_before(
    halves: str, kit: BrandKit, repo_root: Path
) -> None:
    svg = build_svg(_one_photo_poster(None), kit, assets={PHOTO: halves}, root=repo_root)
    assert 'preserveAspectRatio="xMidYMid slice"' in svg
    assert "crop-" not in svg
    framed = build_svg(_one_photo_poster(Crop(x=0.2)), kit, assets={PHOTO: halves}, root=repo_root)
    assert 'preserveAspectRatio="none"' in framed and 'clip-path="url(#crop-bg)"' in framed


def test_a_blurred_photo_is_blurred_and_framed(halves: str, kit: BrandKit, repo_root: Path) -> None:
    spec = _one_photo_poster(Crop(x=0.0))
    blurred = spec.model_copy(update={"layers": [spec.layers[0].model_copy(update={"blur": 0.2})]})
    png = render_poster(blurred, kit, assets={PHOTO: halves}, root=repo_root)
    assert _centre_colour(png)[0] > 150  # still the left (red) half


def test_the_powerpoint_trims_the_same_window_the_poster_shows(
    halves: str, kit: BrandKit, repo_root: Path
) -> None:
    spec = _one_photo_poster(Crop(x=0.1, zoom=1.2))
    deck = Presentation(io.BytesIO(export_pptx(spec, kit, assets={PHOTO: halves}, root=repo_root)))
    [picture] = [shape for shape in deck.slides[0].shapes if shape.shape_type == 13]
    x, y, w, h = crop_window((1500, 1000), spec.canvas.size, spec.layers[0].crop)  # type: ignore[union-attr]
    assert picture.crop_left == pytest.approx(x / 1500, abs=1e-4)
    assert picture.crop_right == pytest.approx(1 - (x + w) / 1500, abs=1e-4)
    assert picture.crop_top == pytest.approx(y / 1000, abs=1e-4)
    assert picture.crop_bottom == pytest.approx(1 - (y + h) / 1000, abs=1e-4)


# --- reading a photo ---------------------------------------------------------------------


def test_a_clear_subject_is_found_where_it_is() -> None:
    read = read_image(disc_scene(at=0.25))
    cols = read.salience.shape[1]
    centre_x = float((read.salience.sum(0) * (np.arange(cols) + 0.5) / cols).sum())
    assert read.strength > 0.7
    assert centre_x == pytest.approx(0.25, abs=0.08)
    assert read.faces == ()  # a red disc is not a face


def test_a_flat_or_evenly_busy_photo_has_no_subject_to_protect() -> None:
    flat = read_image(Image.new("RGB", (1200, 900), (120, 140, 160)))
    rng = np.random.default_rng(0)
    noise = read_image(Image.fromarray(rng.integers(0, 255, (900, 1200, 3)).astype("uint8")))
    assert flat.strength == pytest.approx(0.0, abs=1e-6)
    assert noise.strength < 0.2


def test_busyness_is_high_on_brickwork_and_low_on_sky() -> None:
    read = read_image(half_busy(busy_left=True))
    cols = read.busy.shape[1]
    assert read.busy[:, : cols // 3].mean() > 3 * read.busy[:, -cols // 3 :].mean()


# --- the face model ------------------------------------------------------------------------


def test_the_face_model_is_the_file_the_code_was_written_for() -> None:
    digest = hashlib.sha256(face_model.MODEL.read_bytes()).hexdigest()
    assert digest == face_model.MODEL_SHA256
    assert (face_model.MODEL.parent / "LICENSE-yunet.txt").read_text().startswith("MIT License")


def test_a_swapped_model_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / face_model.MODEL.name
    shutil.copy(face_model.MODEL, fake)
    with fake.open("ab") as handle:
        handle.write(b"\0")
    monkeypatch.setattr(face_model, "MODEL", fake)
    face_model._session.cache_clear()
    try:
        with pytest.raises(FaceModelError, match="not the YuNet file"):
            face_model._session()
    finally:
        face_model._session.cache_clear()


def test_drawn_scenes_contain_no_faces() -> None:
    for image in (disc_scene(), half_busy(), Image.new("RGB", (640, 480), (30, 30, 30))):
        assert find_faces(image) == ()


def _outputs(hits: dict[int, tuple[int, float, float, float, float, float]]) -> dict[str, Any]:
    """YuNet-shaped outputs, empty except the given (stride -> index, score, box) hits."""
    out: dict[str, Any] = {}
    for stride in (8, 16, 32):
        cells = (640 // stride) ** 2
        out[f"cls_{stride}"] = np.zeros((1, cells, 1), dtype=np.float32)
        out[f"obj_{stride}"] = np.zeros((1, cells, 1), dtype=np.float32)
        out[f"bbox_{stride}"] = np.zeros((1, cells, 4), dtype=np.float32)
        out[f"kps_{stride}"] = np.zeros((1, cells, 10), dtype=np.float32)
    for stride, (index, score, dx, dy, log_w, log_h) in hits.items():
        out[f"cls_{stride}"][0, index, 0] = score
        out[f"obj_{stride}"][0, index, 0] = score
        out[f"bbox_{stride}"][0, index] = (dx, dy, log_w, log_h)
    return out


def test_decoding_turns_a_grid_cell_into_a_face_box() -> None:
    # Stride 32: the grid is 20 wide. Cell 45 is row 2, column 5. A centre offset of
    # (0.5, 0.5) puts the centre at (176, 80); log size ln(2) makes it 64 x 64.
    outputs = _outputs({32: (45, 0.95, 0.5, 0.5, float(np.log(2)), float(np.log(2)))})
    [face] = decode(outputs, scale=0.5, size=(1280, 960))
    # scale 0.5: model pixels are half the photo's, so the photo box is twice as big.
    assert face.x == pytest.approx((176 - 32) * 2 / 1280)
    assert face.y == pytest.approx((80 - 32) * 2 / 960)
    assert face.w == pytest.approx(128 / 1280)
    assert face.h == pytest.approx(128 / 960)
    assert face.score == pytest.approx(0.95)


def test_weak_hits_are_not_faces_and_one_face_found_twice_is_one() -> None:
    outputs = _outputs({32: (45, 0.5, 0.5, 0.5, 0.0, 0.0)})
    assert decode(outputs, scale=1.0, size=(640, 640)) == []
    one = Face(0.40, 0.20, 0.10, 0.15, 0.9)
    again = Face(0.41, 0.21, 0.10, 0.15, 0.8)
    other = Face(0.70, 0.20, 0.10, 0.15, 0.85)
    assert distinct([again, one, other]) == (one, other)


# --- choosing a crop -------------------------------------------------------------------------

BOX = (1080.0, 1350.0)


def test_with_nothing_to_gain_the_photo_stays_centred() -> None:
    crop = choose_crop(read_with((1500, 1000)), BOX, [words(0.1, 0.6, 0.8, 0.2)])
    assert crop == Crop()


def test_the_subject_moves_out_from_under_the_words() -> None:
    read = read_image(disc_scene(at=0.3))
    # Words over the left 45% of the box: the disc should end up on the right.
    crop = choose_crop(read, BOX, [words(0.05, 0.1, 0.4, 0.8)])
    x, _ = in_box((0.3, 0.5), shown(crop, read.size, BOX))
    assert x > 0.5
    # Words on the right: the disc should end up on the left.
    crop = choose_crop(read, BOX, [words(0.55, 0.1, 0.4, 0.8)])
    x, _ = in_box((0.3, 0.5), shown(crop, read.size, BOX))
    assert x < 0.5


def test_the_words_go_over_the_calm_part_of_the_photo() -> None:
    read = read_image(half_busy(busy_left=True))
    crop = choose_crop(read, BOX, [words(0.05, 0.05, 0.9, 0.3)])
    # The window slides right, to the flat half.
    assert crop.x > 0.5


def test_a_head_is_never_cut_when_a_crop_can_keep_it() -> None:
    # A tall photo in a wide (16:9) box. The face is near the top: the even cut takes
    # the top off the head.
    face = Face(0.40, 0.08, 0.2, 0.13, 0.95)
    read = read_with((1000, 1500), faces=(face,))
    box = (1920.0, 1080.0)
    even = shown(Crop(), read.size, box)
    assert even[1] > face.head()[1]  # the even cut starts below the top of the head
    crop = choose_crop(read, box, [])
    _, wy, _, wh = shown(crop, read.size, box)
    _, hy, _, hh = face.head()
    assert wy <= max(0.0, hy) + 1e-6 and hy + hh <= wy + wh


def test_a_face_moves_out_from_under_the_words() -> None:
    face = Face(0.30, 0.35, 0.1, 0.15, 0.95)
    read = read_with((1500, 1000), faces=(face,))
    crop = choose_crop(read, BOX, [words(0.0, 0.2, 0.5, 0.6)])
    fx, _ = in_box((face.x + face.w / 2, face.y), shown(crop, read.size, BOX))
    assert fx > 0.5


def test_a_small_photo_is_never_zoomed() -> None:
    read = read_image(disc_scene(700, 500, at=0.5))
    crop = choose_crop(read, BOX, [words(0.0, 0.3, 1.0, 0.4)])
    assert crop.zoom == 1.0


# --- a whole poster -----------------------------------------------------------------------


def poster() -> PosterSpec:
    """A photo under a headline on the left, with its own story layout."""
    return PosterSpec(
        template_id="left_words",
        canvas=Canvas(aspect="4:5"),
        layers=[
            ImageLayer(id="bg", box=Box(x=0, y=0, w=1, h=1), asset=PHOTO),
            ShapeLayer(id="scrim", box=Box(x=0, y=0, w=0.5, h=1), opacity=0.3),
            TextLayer(
                id="headline",
                box=Box(x=0.06, y=0.3, w=0.4, h=0.4),
                content="Winter boiler checks, 20% off",
                font="font.display",
                max_lines=4,
            ),
            LogoLayer(id="logo", box=Box(x=0.7, y=0.05, w=0.24, h=0.08), asset="c" * 64),
        ],
        variants={"9:16": {"bg": LayoutOverride(dim=0.1)}},
    )


def test_a_draft_frames_every_photo_in_every_size(kit: BrandKit, repo_root: Path) -> None:
    read = read_image(disc_scene(at=0.3))
    framed = frame_photos(poster(), {PHOTO: read}, kit, repo_root, ["4:5", "9:16", "16:9"])
    base = framed.layer("bg")
    assert isinstance(base, ImageLayer) and base.crop is not None and base.crop != Crop()
    story = framed.variants["9:16"]["bg"]
    assert story.crop is not None and story.dim == 0.1  # the story's own dim is kept
    assert framed.variants["16:9"]["bg"].crop is not None
    # The logo and the words are untouched.
    assert framed.layer("logo") == poster().layer("logo")
    assert framed.layer("headline") == poster().layer("headline")


def test_only_the_named_photos_are_framed(kit: BrandKit, repo_root: Path) -> None:
    read = read_image(disc_scene(at=0.3))
    same = frame_photos(poster(), {PHOTO: read}, kit, repo_root, ["4:5"], only={"other"})
    assert same == poster()


def test_a_photo_without_a_read_keeps_its_crop(kit: BrandKit, repo_root: Path) -> None:
    assert frame_photos(poster(), {}, kit, repo_root, ["4:5", "9:16"]) == poster()


def test_a_poster_laid_out_from_its_template_file_is_framed_in_its_own_size(
    kit: BrandKit, repo_root: Path
) -> None:
    legacy = poster().model_copy(update={"variants": {}})
    framed = frame_photos(legacy, {PHOTO: read_image(disc_scene())}, kit, repo_root, ["9:16"])
    assert framed.variants == {}
    assert isinstance(framed.layer("bg"), ImageLayer)
    assert framed.layer("bg").crop is not None  # type: ignore[union-attr]


def test_the_words_cover_what_they_really_cover(kit: BrandKit, repo_root: Path) -> None:
    spec = poster()
    [headline, *_] = [c for c in covers_over(spec, 0, kit, repo_root) if c.words > 0]
    # Its letters, not its whole box, and read through the 30% scrim.
    assert headline.w <= 0.4 + 1e-9
    assert headline.words == pytest.approx(0.7)


def test_a_face_no_crop_can_save_is_flagged(kit: BrandKit, repo_root: Path) -> None:
    # A square photo in a 4:5 box cannot move up or down; the face is behind the words.
    face = Face(0.10, 0.40, 0.25, 0.25, 0.95)
    read = read_with((1000, 1000), faces=(face,))
    spec = poster()
    flags = face_flags(retarget(spec, "4:5"), {PHOTO: read}, kit, repo_root)
    assert [flag.code for flag in flags] == ["face_covered"]


def test_a_face_the_crop_saved_is_not_flagged(kit: BrandKit, repo_root: Path) -> None:
    face = Face(0.25, 0.40, 0.08, 0.12, 0.95)
    read = read_with((1500, 1000), faces=(face,))
    framed = frame_photos(poster(), {PHOTO: read}, kit, repo_root, ["4:5"])
    assert face_flags(framed, {PHOTO: read}, kit, repo_root) == ()
    # The even cut left it under the words.
    assert [f.code for f in face_flags(poster(), {PHOTO: read}, kit, repo_root)] == ["face_covered"]


def test_a_head_cut_by_the_crop_is_flagged(kit: BrandKit, repo_root: Path) -> None:
    face = Face(0.02, 0.40, 0.2, 0.2, 0.95)  # at the very left of a wide photo
    read = read_with((2000, 800), faces=(face,))
    spec = poster().model_copy(update={"layers": [poster().layers[0]]})
    assert [f.code for f in face_flags(spec, {PHOTO: read}, kit, repo_root)] == ["face_cut"]


# --- the product ------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[3]


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


async def test_a_draft_stores_its_crops_and_a_new_photo_is_framed_again(tmp_path: Path) -> None:
    from stratum_worker.app import service
    from stratum_worker.gateway.budget import Budget
    from stratum_worker.gateway.gateway import Gateway
    from stratum_worker.gateway.providers.registry import offline_router
    from stratum_worker.queue import fake
    from stratum_worker.queue.fake import World

    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    supa = world.factory(agency)
    for row in await supa.select("assets", kind="eq.photo"):
        await supa.update("assets", {"kind": "retired"}, id=f"eq.{row['id']}")
    ctx = service.Context(
        supa=supa,
        settings=world.settings(REPO),
        gateway=Gateway(router=offline_router([fake.FAKE_COPY] * 4), budget=Budget(2.0)),
        agency_id=agency,
    )
    shas = [
        await service.store_image(ctx, client_id, _png(disc_scene(at=at)), kind="photo")
        for at in (0.2, 0.8)
    ]
    [client] = await supa.select("clients", id=f"eq.{client_id}")
    [post] = await supa.insert(
        "posts",
        {
            "agency_id": agency,
            "client_id": client_id,
            "slug": "framed-post",
            "brief": "Winter boiler checks.",
            "template_id": "ds.overlay.1.000000",
            "aspects": ["4:5", "9:16"],
        },
    )
    first = service.load_spec((await service.draft(ctx, post, client))["spec"])
    photo = first.layer("bg")
    assert isinstance(photo, ImageLayer) and photo.asset in shas
    assert photo.crop is not None
    assert first.variants["9:16"]["bg"].crop is not None

    await service.quick(ctx, post, client, "next_photo")
    [latest] = await supa.select(
        "post_versions", post_id=f"eq.{post['id']}", order="version.desc", limit="1"
    )
    second = service.load_spec(latest["spec"])
    swapped = second.layer("bg")
    assert isinstance(swapped, ImageLayer) and swapped.asset != photo.asset
    # The disc moved from one side of the photo to the other, and so did the window.
    assert swapped.crop is not None and swapped.crop != photo.crop
    assert second.variants["9:16"]["bg"].crop is not None
    # Nothing else about the poster changed.
    assert [layer for layer in second.layers if layer.id != "bg"] == [
        layer for layer in first.layers if layer.id != "bg"
    ]
