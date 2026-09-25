from __future__ import annotations

import json
from pathlib import Path

import pytest
from stratum_spec import load_template, load_templates, load_variants, retarget
from stratum_spec.templates import NotALayoutField

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
OFFER = TEMPLATE_DIR / "offer_4x5.json"


def test_shipped_templates_are_valid() -> None:
    """Every template in templates/ must validate against the current schema."""
    templates = load_templates(TEMPLATE_DIR)
    assert set(templates) == {"offer_4x5", "before_after_4x5", "testimonial_1x1"}


def test_every_template_has_a_logo_and_a_headline_or_quote() -> None:
    for template in load_templates(TEMPLATE_DIR).values():
        kinds = {layer.type for layer in template.layers}
        ids = {layer.id for layer in template.layers}
        assert "logo" in kinds, template.template_id
        assert ids & {"headline", "quote"}, template.template_id


def test_landscape_keeps_the_shorter_side_at_full_size() -> None:
    """A 16:9 from a 1080-wide template would otherwise be 1080x608 - unpostable."""
    spec = load_template(OFFER)
    variants = load_variants(OFFER)
    assert retarget(spec, "16:9", variants).canvas.size == (1920, 1080)
    assert retarget(spec, "9:16", variants).canvas.size == (1080, 1920)
    assert retarget(spec, "1:1", variants).canvas.size == (1080, 1080)


def test_an_aspect_override_moves_a_layer_without_touching_its_words() -> None:
    spec = load_template(OFFER)
    variants = load_variants(OFFER)
    story = retarget(spec, "9:16", variants)
    before, after = spec.layer("band"), story.layer("band")
    assert after.box != before.box
    assert [layer.id for layer in story.layers] == [layer.id for layer in spec.layers]
    for layer_id in ("headline", "offer", "cta"):
        original, moved = spec.layer(layer_id), story.layer(layer_id)
        assert moved.content == original.content
        assert moved.color == original.color and moved.font == original.font


def test_an_override_may_not_change_content_or_brand(tmp_path: Path) -> None:
    """Overrides are layout. A variant that could reword or recolour a poster would
    mean the client approved one thing and four different things shipped."""
    source = json.loads((OFFER).read_text())
    source["aspects"] = {"1:1": {"headline": {"content": "SOMETHING ELSE"}}}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(NotALayoutField, match="content"):
        load_variants(path)
