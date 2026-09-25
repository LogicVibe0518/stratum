"""Which copy field goes in which layer, per template.

A table in Python rather than a field in the template JSON: adding a `bind` field to
TextLayer means a schema-version bump and an upgrader, and there are three templates.
This moves into the template files the day a template can be added without touching
code - that is the moment the complexity earns itself.

`capacity` is what the model is TOLD to aim for, not what the renderer enforces. It
is smaller than the spec's hard limits on purpose: auto-fit can rescue copy that is
slightly long, but a headline shrunk to fit is a headline nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Binding:
    """How one template is filled in."""

    # layer id -> attribute on PostCopy.
    texts: dict[str, str]
    # layer id -> characters that layer really fits (see the note above).
    capacity: dict[str, int]
    # layer id -> role, so the pipeline knows which photo goes where. A template
    # needing two photos ("before", "after") says so here.
    photos: dict[str, str]
    # Text the design owns, which no model may rewrite.
    fixed: dict[str, str] | None = None
    logo: str | None = None


BINDINGS: dict[str, Binding] = {
    "offer_4x5": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        capacity={"headline": 42, "offer": 90, "cta": 34},
        photos={"bg": "background"},
        logo="logo",
    ),
    # BEFORE and AFTER are labels on the design, not copy: a model rewriting them
    # into "PREVIOUSLY" and "NOW" would break a layout everyone recognises.
    "before_after_4x5": Binding(
        texts={"headline": "headline", "contact": "cta"},
        capacity={"headline": 36, "contact": 34},
        fixed={"before_label": "BEFORE", "after_label": "AFTER"},
        photos={"before": "before", "after": "after"},
        logo="logo",
    ),
    "testimonial_1x1": Binding(
        texts={"quote": "offer_detail", "attribution": "cta"},
        capacity={"quote": 140, "attribution": 40},
        photos={"subject": "subject"},
        logo="logo",
    ),
}


class UnknownTemplate(KeyError):
    """No binding for that template. Adding a template means adding its binding."""


def binding_for(template_id: str) -> Binding:
    try:
        return BINDINGS[template_id]
    except KeyError as exc:
        known = ", ".join(sorted(BINDINGS))
        raise UnknownTemplate(f"no binding for template {template_id!r}; have {known}") from exc


__all__ = ["BINDINGS", "Binding", "UnknownTemplate", "binding_for"]
