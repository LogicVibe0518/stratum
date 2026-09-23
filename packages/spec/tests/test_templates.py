from __future__ import annotations

from pathlib import Path

from stratum_spec import load_templates

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"


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
