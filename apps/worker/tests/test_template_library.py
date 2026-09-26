"""The template library is a design system, so it gets checked like one.

Every rule here is one I broke by hand at least once while authoring these: a token
a kit did not define, text floating on a photo with nothing behind it, a logo pushed
into the part of a story that Instagram covers with its own buttons.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from stratum_spec import BrandKit, load_template, load_templates, load_variants, retarget
from stratum_spec.colour import blend, contrast_ratio, min_contrast_for
from stratum_spec.models import Box, PosterSpec, ShapeLayer, TextLayer
from stratum_worker.draft.bindings import BINDINGS, binding_for
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = load_templates(REPO_ROOT / "templates")
KITS = {
    path.parent.name: BrandKit.model_validate(json.loads(path.read_text()))
    for path in sorted((REPO_ROOT / "clients").glob("*/brand.json"))
}
IDS = sorted(TEMPLATES)


def _covers(outer: Box, inner: Box) -> bool:
    return (
        outer.x <= inner.x + 1e-9
        and outer.y <= inner.y + 1e-9
        and outer.x + outer.w >= inner.x + inner.w - 1e-9
        and outer.y + outer.h >= inner.y + inner.h - 1e-9
    )


def _backing(spec: PosterSpec, index: int) -> ShapeLayer | None:
    text = spec.layers[index]
    for layer in reversed(spec.layers[:index]):
        if isinstance(layer, ShapeLayer) and _covers(layer.box, text.box):
            return layer
    return None


def test_generated_templates_match_their_generator() -> None:
    """The JSON is what runs; the generator is how it is maintained. If the two
    disagree, the next person to edit a margin silently loses someone's change."""
    before = {path: path.read_bytes() for path in sorted((REPO_ROOT / "templates").glob("*.json"))}
    subprocess.run(  # noqa: S603
        [sys.executable, str(REPO_ROOT / "tools" / "build_templates.py")],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    changed = [p.name for p, data in before.items() if p.read_bytes() != data]
    assert not changed, f"regenerating changed {changed} - commit the generator's output"


@pytest.mark.parametrize("template_id", IDS)
@pytest.mark.parametrize("client", sorted(KITS))
def test_every_template_resolves_against_every_brand_kit(template_id: str, client: str) -> None:
    """A template naming a colour a client does not have is a poster with a hole in
    it. Cheaper to catch here than in front of the client."""
    kit = KITS[client]
    for layer in TEMPLATES[template_id].layers:
        for field in ("color", "fill"):
            token = getattr(layer, field, None)
            if isinstance(token, str):
                kit.color(token)
        font = getattr(layer, "font", None)
        if isinstance(font, str):
            kit.font(font)


@pytest.mark.parametrize("template_id", IDS)
def test_every_word_has_something_behind_it(template_id: str) -> None:
    """Text straight on a photograph is readable or not depending on the photograph,
    which means nobody can promise it is readable. Every text layer therefore sits on
    a shape - a band, a card, a scrim - whose contrast we CAN compute."""
    spec = TEMPLATES[template_id]
    for index, layer in enumerate(spec.layers):
        if isinstance(layer, TextLayer):
            assert _backing(spec, index) is not None, (
                f"{template_id}: '{layer.id}' has no shape behind it"
            )


@pytest.mark.parametrize("template_id", IDS)
@pytest.mark.parametrize("client", sorted(KITS))
def test_every_template_is_readable_in_every_brand(template_id: str, client: str) -> None:
    """WCAG contrast, computed against mid-grey - the worst case for light AND dark
    text, so passing here means passing over any photograph."""
    kit = KITS[client]
    spec = TEMPLATES[template_id]
    for index, layer in enumerate(spec.layers):
        if not isinstance(layer, TextLayer):
            continue
        backing = _backing(spec, index)
        assert backing is not None
        behind = blend(kit.color(backing.fill), "#808080", backing.opacity)
        ratio = contrast_ratio(kit.color(layer.color), behind)
        needed = min_contrast_for(layer.max_size)
        assert ratio >= needed, (
            f"{template_id}/{client}: '{layer.id}' is {ratio:.1f}:1, needs {needed:.1f}:1"
        )


@pytest.mark.parametrize("template_id", IDS)
def test_stories_keep_out_of_the_platform_furniture(template_id: str) -> None:
    """Instagram draws its own profile row and buttons over the top and bottom of a
    9:16. Anything we put there is rendered and invisible."""
    path = REPO_ROOT / "templates" / f"{template_id}.json"
    story = retarget(load_template(path), "9:16", load_variants(path))
    for layer in story.layers:
        if isinstance(layer, TextLayer) or layer.type == "logo":
            assert layer.box.y >= STORY_SAFE_TOP - 1e-9, f"{template_id}: '{layer.id}' too high"
            assert layer.box.y + layer.box.h <= STORY_SAFE_BOTTOM + 1e-9, (
                f"{template_id}: '{layer.id}' too low"
            )


@pytest.mark.parametrize("template_id", IDS)
def test_every_template_can_be_filled(template_id: str) -> None:
    """A template with no binding cannot receive copy, so it is decoration."""
    binding = binding_for(template_id)
    ids = {layer.id for layer in TEMPLATES[template_id].layers}
    text_ids = {layer.id for layer in TEMPLATES[template_id].layers if isinstance(layer, TextLayer)}
    named = set(binding.texts) | set(binding.fixed or {})
    assert named == text_ids, f"{template_id}: binding covers {named}, template has {text_ids}"
    assert set(binding.photos) <= ids
    assert binding.logo in ids


def test_no_binding_names_a_template_that_does_not_exist() -> None:
    assert set(BINDINGS) == set(TEMPLATES)


@pytest.mark.parametrize("template_id", IDS)
def test_every_logo_has_something_behind_it(template_id: str) -> None:
    """Same rule as for text, for the same reason. A white wordmark on a pale wall is
    invisible, and the wall is the client's photo, so it cannot be judged in advance."""
    spec = TEMPLATES[template_id]
    index = next(i for i, layer in enumerate(spec.layers) if layer.type == "logo")
    assert _backing(spec, index) is not None, f"{template_id}: the logo has no backing"


@pytest.mark.parametrize("template_id", IDS)
def test_a_logo_is_never_squashed_into_a_thin_strip(template_id: str) -> None:
    """A logo is fitted, not cropped, so an absurd box just floats it in white space.
    Kowalski asks for a bigger logo on every single post; give it real room."""
    for aspect in ("4:5", "9:16", "16:9"):
        path = REPO_ROOT / "templates" / f"{template_id}.json"
        spec = retarget(load_template(path), aspect, load_variants(path))
        logo = next(layer for layer in spec.layers if layer.type == "logo")
        width, height = spec.canvas.size
        assert logo.box.w * width >= 120, f"{template_id}/{aspect}: logo under 120px wide"
        assert logo.box.h * height >= 45, f"{template_id}/{aspect}: logo under 45px tall"
