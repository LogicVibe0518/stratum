"""The subject of a photo (M10f): U^2-Net outlines what the photo is of, and the crop,
the ranking of designs and the photo picker all use the outline.

The no-model salience finds what differs in colour. In front of a busy street it
picks the brightest sign, so a crop could leave the person under the words, or out
of the frame. With the outline, the person is the subject wherever they stand.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from stratum_spec import BrandKit, Crop
from stratum_worker.app import service
from stratum_worker.design import choose as chooser
from stratum_worker.design.choose import Material, candidates, choose, seed_for
from stratum_worker.photo import read as photo_read
from stratum_worker.photo import subject as subject_model
from stratum_worker.photo.check import CUT_OFF, fitness, upload_check
from stratum_worker.photo.frame import Cover, choose_crop, crop_cost, framing_cost
from stratum_worker.photo.read import read_image, with_subject
from stratum_worker.photo.subject import (
    INSIDE,
    SubjectModelError,
    find_subject,
    probability,
    subject_of,
)
from stratum_worker.queue import fake
from stratum_worker.queue.fake import World
from stratum_worker.render.layout import crop_window

REPO = Path(__file__).resolve().parents[3]
BOX = (1080.0, 1350.0)  # a 4:5 feed post
WIDTH, HEIGHT = 1500, 1000


# --- drawn photos ----------------------------------------------------------------------


def wall(*, grain: int = 4) -> Image.Image:
    rng = np.random.default_rng(3)
    grey = 195 + rng.integers(-grain, grain + 1, (HEIGHT, WIDTH, 1))
    return Image.fromarray(np.repeat(grey, 3, axis=2).clip(0, 255).astype("uint8"))


def disc(at: tuple[float, float] = (0.25, 0.5), *, grain: int = 4) -> Image.Image:
    """A red disc on a plain wall: one clear subject."""
    image = wall(grain=grain)
    radius, x, y = HEIGHT // 6, int(at[0] * WIDTH), int(at[1] * HEIGHT)
    ImageDraw.Draw(image).ellipse([x - radius, y - radius, x + radius, y + radius], (200, 30, 30))
    return image


def street(at: float) -> Image.Image:
    """A person (a head and an orange jacket) in front of a busy street of signs."""
    rng = np.random.default_rng(7)
    image = Image.new("RGB", (WIDTH, HEIGHT), (120, 120, 125))
    draw = ImageDraw.Draw(image)
    for _ in range(120):
        x, y = int(rng.integers(0, WIDTH)), int(rng.integers(0, HEIGHT))
        w, h = int(rng.integers(30, 200)), int(rng.integers(30, 300))
        draw.rectangle([x, y, x + w, y + h], tuple(int(v) for v in rng.integers(0, 255, 3)))
    centre = int(at * WIDTH)
    draw.ellipse([centre - 90, 200, centre + 90, 400], (225, 185, 150))
    draw.rectangle([centre - 170, 400, centre + 170, HEIGHT], (240, 120, 20))
    return image


def noise() -> Image.Image:
    rng = np.random.default_rng(0)
    return Image.fromarray(rng.integers(0, 255, (HEIGHT, WIDTH, 3)).astype("uint8"))


def checks() -> Image.Image:
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
    on = ((x // 6) + (y // 6)) % 2 == 1
    return Image.fromarray(np.where(on[..., None], 60, 170).repeat(3, axis=2).astype("uint8"))


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


NO_SUBJECT = subject_of(np.zeros((10, 15), dtype=np.float32))


# --- the model -------------------------------------------------------------------------


def test_the_subject_model_is_the_file_the_code_was_written_for() -> None:
    digest = hashlib.sha256(subject_model.MODEL.read_bytes()).hexdigest()
    assert digest == subject_model.MODEL_SHA256
    licence = (subject_model.MODEL.parent / "LICENSE-u2net.txt").read_text()
    assert licence.startswith("U^2-Net small") and "Apache License" in licence
    assert subject_model.MODEL_SHA256 in licence


def test_a_swapped_model_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    swapped = tmp_path / subject_model.MODEL.name
    shutil.copy(subject_model.MODEL, swapped)
    with swapped.open("ab") as handle:
        handle.write(b"\0")
    monkeypatch.setattr(subject_model, "MODEL", swapped)
    subject_model._session.cache_clear()
    try:
        with pytest.raises(SubjectModelError, match="not the U\\^2-Net file"):
            subject_model._session()
    finally:
        subject_model._session.cache_clear()


def test_a_missing_model_file_says_where_it_should_be(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subject_model, "MODEL", tmp_path / "u2netp.onnx")
    subject_model._session.cache_clear()
    try:
        with pytest.raises(SubjectModelError, match="missing"):
            subject_model._session()
    finally:
        subject_model._session.cache_clear()


def test_the_same_photo_runs_the_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[int] = []
    real = subject_model._run

    def counted(pixels: np.ndarray) -> np.ndarray:  # type: ignore[type-arg]
        runs.append(1)
        return real(pixels)

    monkeypatch.setattr(subject_model, "_run", counted)
    monkeypatch.setattr(subject_model, "_kept", type(subject_model._kept)())
    first = probability(disc((0.4, 0.5)))
    again = probability(disc((0.4, 0.5)))
    assert len(runs) == 1 and np.array_equal(first, again)
    probability(disc((0.6, 0.5)))
    assert len(runs) == 2


# --- finding the subject ---------------------------------------------------------------


def test_a_clear_subject_is_found_where_it_is() -> None:
    found = find_subject(disc((0.25, 0.5)))
    assert found.clear
    assert centre(found.box) == pytest.approx((0.25, 0.5), abs=0.03)
    # A disc a third of the height across: pi * (1/6 * 1000)^2 / (1500 * 1000) = 5.8%.
    assert found.cover == pytest.approx(0.058, abs=0.01)
    assert found.top == 0.0


def test_a_person_in_front_of_a_busy_street_is_the_subject() -> None:
    found = find_subject(street(0.7))
    assert found.clear
    assert centre(found.box)[0] == pytest.approx(0.7, abs=0.04)


@pytest.mark.parametrize(
    "image",
    [Image.new("RGB", (WIDTH, HEIGHT), (120, 140, 160)), noise(), checks()],
    ids=["flat", "noise", "checks"],
)
def test_a_flat_noisy_or_patterned_photo_has_no_clear_subject(image: Image.Image) -> None:
    found = find_subject(image)
    assert not found.clear
    # The model's own answer, never stretched: nothing reaches "inside" by force.
    assert float(probability(image).max()) < INSIDE


def _mask(inside: float, outside: float, rect: tuple[int, int, int, int]) -> np.ndarray:  # type: ignore[type-arg]
    mask = np.full((100, 150), outside, dtype=np.float32)
    top, left, bottom, right = rect
    mask[top:bottom, left:right] = inside
    return mask


@pytest.mark.parametrize(
    ("mask", "clear"),
    [
        (_mask(0.95, 0.02, (40, 60, 70, 90)), True),  # 6% of the photo, decisive
        (_mask(0.95, 0.02, (49, 74, 52, 77)), False),  # a speck: 0.06%
        (_mask(0.95, 0.02, (0, 0, 100, 140)), False),  # nearly the whole photo
        (_mask(0.6, 0.4, (40, 60, 70, 90)), False),  # not decisive
    ],
    ids=["clear", "speck", "whole-photo", "unsure"],
)
def test_what_counts_as_one_clear_subject(mask: np.ndarray, clear: bool) -> None:  # type: ignore[type-arg]
    assert subject_of(mask).clear is clear


def test_the_box_and_the_top_edge_are_measured() -> None:
    found = subject_of(_mask(0.9, 0.0, (0, 30, 50, 90)))
    assert found.box == pytest.approx((0.2, 0.0, 0.4, 0.5))
    assert found.top == pytest.approx(0.4)
    assert found.separation == pytest.approx(0.9)
    assert NO_SUBJECT.box == (0.0, 0.0, 0.0, 0.0) and NO_SUBJECT.separation == 0.0


def test_the_outline_fits_any_grid() -> None:
    found = subject_of(_mask(1.0, 0.0, (0, 0, 50, 75)))  # the top-left quarter
    grid = found.on((10, 12))
    assert grid.shape == (10, 12)
    assert grid[:5, :6].min() == pytest.approx(1.0) and grid[5:, 6:].max() == pytest.approx(0.0)
    assert grid.mean() == pytest.approx(0.25, abs=0.01)


# --- the read: salience and strength ---------------------------------------------------


def test_with_no_clear_subject_the_read_is_exactly_as_before() -> None:
    sal = np.full((4, 6), 1 / 24)
    out, sure = with_subject(sal, 0.3, NO_SUBJECT)
    assert out is sal and sure == 0.3


def _plain(image: Image.Image, monkeypatch: pytest.MonkeyPatch) -> photo_read.PhotoRead:
    """The read as it was before the subject model: colour and detail alone."""
    with monkeypatch.context() as patch:
        patch.setattr(photo_read, "find_subject", lambda image: NO_SUBJECT)
        return read_image(image)


def test_the_outline_carries_the_salience_and_the_certainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = street(0.7)
    read, plain = read_image(image), _plain(image, monkeypatch)
    assert read.subject is not None and read.subject.clear
    inside = read.subject.on(read.salience.shape) >= INSIDE
    # By colour alone, 3% of where the eye goes was the person; with the outline, most.
    assert float(plain.salience[inside].sum()) < 0.1
    assert float(read.salience[inside].sum()) > 0.7
    assert float(read.salience.sum()) == pytest.approx(1.0)
    # Colour alone is unsure on a busy street; the outline is not.
    assert plain.strength < 0.5 < 0.9 < read.strength


def _person_in_box(read: photo_read.PhotoRead, crop: Crop, at: float) -> float:
    """Where the person's centre lands across the poster's box, 0 left .. 1 right."""
    x, _, w, _ = crop_window(read.size, BOX, crop)
    return (at * WIDTH - x) / w


@pytest.mark.parametrize("at", [0.3, 0.7])
def test_on_a_busy_street_the_person_stays_in_frame_and_clear_of_the_words(
    at: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = street(at)
    words = [Cover(0.05, 0.1, 0.4, 0.8, weight=1.0, words=1.0)]  # the left of the box
    read = read_image(image)
    crop = choose_crop(read, BOX, words)
    assert 0.5 < _person_in_box(read, crop, at) < 1.0
    # By colour alone the crop went for the signs: the person ended up under the
    # words (at 0.3) or outside the frame (at 0.7).
    plain = _plain(image, monkeypatch)
    landed = _person_in_box(plain, choose_crop(plain, BOX, words), at)
    assert landed < 0.45 or landed > 1.0
    # And the ranking of designs sees it: words over the person now cost more.
    assert crop_cost(read, BOX, words, Crop(x=at, y=0.5)) > crop_cost(read, BOX, words, crop)


def test_the_designs_shown_keep_the_person_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real ranking: the top three, framed, judged by where the person is."""
    monkeypatch.setattr(chooser, "MAX_CANDIDATES", 48)
    monkeypatch.setattr(chooser, "SHORTLIST", 10)
    monkeypatch.setattr(chooser, "FAMILY_FLOOR", 2)
    kit = BrandKit.model_validate(
        json.loads((REPO / "clients/kowalski-plumbing/brand.json").read_text())
    )
    image = street(0.7)
    truth = read_image(image)

    def shown(read: photo_read.PhotoRead) -> list[float]:
        stuff = Material(
            words={
                "headline": "Winter boiler checks",
                "offer_detail": "20% off until 31 October. Gas Safe engineers.",
                "cta": "Book today",
            },
            kit=kit,
            photos=("a" * 64,),
            background=None,
            logo="c" * 64,
            logo_aspect=2.0,
            ink=None,
            reads={"a" * 64: read},
        )
        ranked = candidates(stuff, ["4:5"], REPO, seed=seed_for("street"))
        top = choose(ranked, seed=1, explore=0.0)
        # How badly the person sits in each poster shown, measured with the outline.
        return [framing_cost(o.candidate.spec, {"a" * 64: truth}, kit, REPO) for o in top]

    assert max(shown(truth)) < 3.0
    assert max(shown(_plain(image, monkeypatch))) > 5.0  # colour alone covered the person


# --- the photo check at upload ---------------------------------------------------------


def test_a_subject_cut_off_at_the_top_is_flagged() -> None:
    numbers, flags = upload_check(disc((0.5, 0.1)))
    assert flags == [CUT_OFF]
    assert numbers["subject_top"] > 0.15


def test_a_subject_with_room_around_it_is_not_flagged() -> None:
    numbers, flags = upload_check(disc((0.25, 0.5)))
    assert flags == []
    assert numbers["subject_cover"] == pytest.approx(0.058, abs=0.01)
    assert numbers["subject_separation"] > 0.9
    assert numbers["calm_room"] > 0.8  # the plain wall beside the disc


def test_a_busy_photo_has_no_room_for_words() -> None:
    numbers, flags = upload_check(checks())
    assert flags == [] and numbers["calm_room"] < 0.1


# --- the photo picker's ranking --------------------------------------------------------

SHARP = {"sharpness": 900.0}
CLEAR = {"subject_cover": 0.2, "subject_separation": 0.95, "subject_top": 0.0}
NONE = {"subject_cover": 0.0, "subject_separation": 0.0, "subject_top": 0.0}


def test_a_sharp_photo_beats_a_soft_one() -> None:
    soft = {"sharpness": 60.0} | CLEAR | {"calm_room": 0.5}
    assert fitness(SHARP | CLEAR | {"calm_room": 0.5}) > fitness(soft)


def test_among_sharp_photos_a_clear_subject_with_room_comes_first() -> None:
    best = fitness(SHARP | CLEAR | {"calm_room": 0.6})
    assert best > fitness(SHARP | NONE | {"calm_room": 0.6})
    assert best > fitness(SHARP | CLEAR | {"calm_room": 0.0})


def test_a_subject_cut_off_at_the_top_costs() -> None:
    cut = CLEAR | {"subject_top": 0.4}
    assert fitness(SHARP | cut | {"calm_room": 0.6}) < fitness(SHARP | CLEAR | {"calm_room": 0.6})


def test_a_photo_uploaded_before_the_check_scores_the_middle() -> None:
    old = fitness(SHARP)
    assert (
        fitness(SHARP | NONE | {"calm_room": 0.0})
        < old
        < fitness(SHARP | CLEAR | {"calm_room": 1.0})
    )


# --- through the service ---------------------------------------------------------------


@pytest.fixture
async def ctx(tmp_path: Path) -> tuple[service.Context, str]:
    world = World(tmp_path / "db.sqlite")
    agency, client_id = await fake.seed(world, REPO)
    supa = world.factory(agency)
    for row in await supa.select("assets", kind="eq.photo"):
        await supa.update("assets", {"kind": "retired"}, id=f"eq.{row['id']}")
    context = service.Context(
        supa=supa, settings=world.settings(REPO), gateway=fake.gateway(), agency_id=agency
    )
    return context, client_id


async def test_an_upload_records_its_subject_and_flags_a_cut_off_one(
    ctx: tuple[service.Context, str],
) -> None:
    context, client_id = ctx
    sha = await service.store_image(
        context, client_id, png(disc((0.5, 0.1), grain=25)), kind="photo"
    )
    [row] = await context.supa.select("assets", sha256=f"eq.{sha}")
    assert row["quality"]["subject_top"] > 0.15
    assert {"subject_cover", "subject_separation", "calm_room"} <= set(row["quality"])
    assert row["flags"] == [CUT_OFF]
    [logo] = await context.supa.select("assets", kind="eq.logo")
    assert "subject_cover" not in logo["quality"]  # a logo is not checked for a subject


async def test_the_picker_prefers_a_clear_subject_to_a_newer_pattern(
    ctx: tuple[service.Context, str],
) -> None:
    context, client_id = ctx
    subject = await service.store_image(
        context, client_id, png(disc((0.3, 0.5), grain=25)), kind="photo"
    )
    await service.store_image(context, client_id, png(checks()), kind="photo")  # newer, sharper
    picked, _ = await service.pick_photos(context, client_id, {"bg": "background"})
    assert picked == {"bg": subject}
