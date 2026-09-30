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
    # layer id -> role, so the pipeline knows which photo goes where. A template
    # needing two photos ("before", "after") says so here.
    photos: dict[str, str]
    # Text the design owns, which no model may rewrite.
    fixed: dict[str, str] | None = None
    logo: str | None = None

    def limits(self, measured: dict[str, int]) -> dict[str, int]:
        """Character budgets per COPY FIELD, from measured layer capacities.

        The model is asked for a headline, a detail line and a CTA; which layer each
        lands in is this template's business. Looking the budget up by role instead
        of by layer id is what stops a quote-shaped template handing the headline a
        150-character budget that the request model then rejects.
        """
        by_field = {field: layer for layer, field in self.texts.items()}
        # Two different ceilings, and the tighter one wins. `measured` is what
        # physically fits; `editorial` is what a copywriter would allow - a headline
        # that fills three lines fits, and is still a bad headline.
        editorial = {"headline": 45, "offer_detail": 110, "cta": 38}
        bounds = {"headline": (10, 120), "offer_detail": (10, 200), "cta": (6, 80)}
        limits: dict[str, int] = {}
        for field, (low, high) in bounds.items():
            layer = by_field.get(field)
            fits = measured.get(layer, editorial[field]) if layer else editorial[field]
            value = min(fits, editorial[field])
            limits[field] = max(low, min(high, value))
        return limits


BINDINGS: dict[str, Binding] = {
    # capacity is what the MODEL is told to aim for, deliberately under the layer's
    # hard limit: auto-fit can rescue slightly-long copy, but a headline shrunk to
    # fit is a headline nobody reads.
    "offer_4x5": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg": "background"},
        logo="logo",
    ),
    "full_bleed": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg": "background"},
        logo="logo",
    ),
    # The words are the poster here, so the headline gets more room than anywhere
    # else - this is the layout for "20% OFF" and for weak photography.
    "headline_hero": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg_photo": "background"},
        logo="logo",
    ),
    "split_panel": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg": "background"},
        logo="logo",
    ),
    "corner_card": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg": "background"},
        logo="logo",
    ),
    "top_band": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg": "background"},
        logo="logo",
    ),
    # Social proof, not an offer: the quote carries the weight and there is no CTA.
    "quote_card": Binding(
        texts={"quote": "offer_detail", "attribution": "cta"},
        photos={"bg_photo": "background"},
        logo="logo",
    ),
    "framed": Binding(
        texts={"headline": "headline", "offer": "offer_detail", "cta": "cta"},
        photos={"bg_photo": "background"},
        logo="logo",
    ),
    # BEFORE and AFTER are labels the design owns, not copy: a model rewriting them
    # into "PREVIOUSLY" and "NOW" would break a layout everyone recognises.
    "before_after_4x5": Binding(
        texts={"headline": "headline", "contact": "cta"},
        fixed={"before_label": "BEFORE", "after_label": "AFTER"},
        photos={"before": "before", "after": "after"},
        logo="logo",
    ),
}


class UnknownTemplate(KeyError):
    """No binding for that template. Adding a template means adding its binding."""


def binding_for(template_id: str) -> Binding:
    if template_id in BINDINGS:
        return BINDINGS[template_id]
    # Imported here: the design space measures text with the renderer, which the
    # draft package also uses, and the two must not import each other at load time.
    from stratum_worker.design import UnknownDesign, binding_parts

    try:
        texts, photos = binding_parts(template_id)
    except UnknownDesign as exc:
        known = ", ".join(sorted(BINDINGS))
        raise UnknownTemplate(f"no binding for template {template_id!r}; have {known}") from exc
    return Binding(texts=texts, photos=photos, logo="logo")


__all__ = ["BINDINGS", "Binding", "UnknownTemplate", "binding_for"]
