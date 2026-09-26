"""Generate templates/*.json from compact definitions.

Why a generator and not eleven hand-written JSON files: the numbers in a layout are
related. The side margin, the logo size, the gap between headline and body appear in
every template, and hand-editing eleven files to change one of them is how a template
library drifts into eleven slightly different design systems. Here the shared values
are named once at the top.

The JSON is still the source of truth at runtime - the renderer never runs this file -
and a test regenerates and compares, so the two can never disagree.

Run: uv run python tools/build_templates.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "templates"

# --- the shared rhythm ------------------------------------------------------------
M = 0.08  # side margin: everything breathes the same distance from the edge
GAP = 0.02  # vertical gap between related lines
LOGO_W, LOGO_H = 0.24, 0.085  # big enough to read - Kowalski asks every single time

# Story furniture covers the top ~14% and bottom ~20% of a 9:16 screen.
STORY_TOP, STORY_BOTTOM = 0.17, 0.78


def box(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def image(id_: str, b: dict[str, float], **kw: Any) -> dict[str, Any]:
    return {"type": "image", "id": id_, "box": b, "source": "client", "fit": "cover", **kw}


def shape(id_: str, b: dict[str, float], fill: str, **kw: Any) -> dict[str, Any]:
    return {"type": "shape", "id": id_, "box": b, "shape": "rect", "fill": fill, **kw}


def text(
    id_: str, b: dict[str, float], content: str, *, font: str, colour: str, **kw: Any
) -> dict[str, Any]:
    return {
        "type": "text",
        "id": id_,
        "box": b,
        "content": content,
        "font": font,
        "color": colour,
        **kw,
    }


def logo(b: dict[str, float]) -> dict[str, Any]:
    return {"type": "logo", "id": "logo", "box": b, "variant": "full", "safe_area": 0.02}


def head(id_: str, b: dict[str, float], colour: str, **kw: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "align": "left",
        "max_lines": 3,
        "min_size": 0.045,
        "max_size": 0.105,
        "uppercase": True,
    }
    opts.update(kw)
    return text(id_, b, "HEADLINE", font="font.display", colour=colour, **opts)


def body(id_: str, b: dict[str, float], colour: str, placeholder: str, **kw: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {"max_lines": 2, "min_size": 0.022, "max_size": 0.038}
    opts.update(kw)
    return text(id_, b, placeholder, font="font.body", colour=colour, **opts)


def cta(id_: str, b: dict[str, float], colour: str, **kw: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {"max_lines": 1, "min_size": 0.018, "max_size": 0.03}
    opts.update(kw)
    return text(id_, b, "CALL TO ACTION", font="font.body_bold", colour=colour, **opts)


ON_PRIMARY = "palette.text_on_primary"
ON_DARK = "palette.text_on_dark"
ON_SURFACE = "palette.text"
PRIMARY, SECONDARY, SURFACE = "palette.primary", "palette.secondary", "palette.surface"
SCRIM = "palette.text"  # every kit's text colour is its darkest - the right scrim


def template(id_: str, layers: list[dict[str, Any]], aspects: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "template_id": id_,
        "template_version": 1,
        "brand_kit_version": 1,
        "canvas": {"aspect": "4:5", "base_width": 1080},
        "layers": layers,
        "aspects": aspects,
    }


def _covers(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return (
        outer["x"] <= inner["x"] + 1e-9
        and outer["y"] <= inner["y"] + 1e-9
        and outer["x"] + outer["w"] >= inner["x"] + inner["w"] - 1e-9
        and outer["y"] + outer["h"] >= inner["y"] + inner["h"] - 1e-9
    )


def _pad(b: dict[str, float], amount: float = 0.022) -> dict[str, float]:
    x, y = max(0.0, b["x"] - amount), max(0.0, b["y"] - amount)
    return box(x, y, min(1.0 - x, b["w"] + 2 * amount), min(1.0 - y, b["h"] + 2 * amount))


def with_logo_chip(spec: dict[str, Any]) -> dict[str, Any]:
    """Put something behind a logo that sits directly on a photograph.

    A white wordmark on a pale wall is invisible, and which it is depends on the
    photo - so it cannot be judged when the template is written. Done here rather
    than by hand in each template because "I forgot the chip" is exactly the kind of
    mistake that ships: the rule now applies to every template ever added, including
    ones written months from now.
    """
    layers = spec["layers"]
    index = next(i for i, layer in enumerate(layers) if layer["type"] == "logo")
    logo_box = layers[index]["box"]
    if any(
        layer["type"] == "shape" and _covers(layer["box"], logo_box) for layer in layers[:index]
    ):
        return spec  # already sitting on a band, a card or a panel
    layers.insert(index, shape("logo_chip", _pad(logo_box), SCRIM, opacity=0.42, radius=0.012))
    for override in spec["aspects"].values():
        moved = override.get("logo", {}).get("box")
        if moved is not None:
            override["logo_chip"] = {"box": _pad(moved)}
    return spec


def build() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    # 1. offer_band - photo above, solid brand band below. The workhorse.
    out["offer_4x5"] = template(
        "offer_4x5",
        [
            image("bg", box(0, 0, 1, 1), dim=0.18),
            shape("band", box(0, 0.58, 1, 0.42), PRIMARY, opacity=0.94),
            head("headline", box(M, 0.62, 1 - 2 * M, 0.16), ON_PRIMARY),
            body("offer", box(M, 0.795, 0.66, 0.07), ON_PRIMARY, "OFFER DETAIL"),
            cta("cta", box(M, 0.885, 0.66, 0.05), ON_PRIMARY),
            logo(box(1 - M - LOGO_W, 0.06, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(1 - M - LOGO_W, STORY_TOP, LOGO_W, 0.06)},
                "band": {"box": box(0, 0.46, 1, STORY_BOTTOM - 0.46)},
                "headline": {"box": box(M, 0.49, 1 - 2 * M, 0.13), "max_size": 0.082},
                "offer": {"box": box(M, 0.635, 0.74, 0.055), "max_size": 0.028},
                "cta": {"box": box(M, 0.705, 0.74, 0.042), "max_size": 0.022},
            },
            "16:9": {
                "band": {"box": box(0, 0, 0.56, 1), "opacity": 0.95},
                "logo": {"box": box(0.05, 0.09, 0.18, 0.1)},
                "headline": {"box": box(0.05, 0.3, 0.46, 0.26), "max_size": 0.115},
                "offer": {"box": box(0.05, 0.61, 0.46, 0.1), "max_size": 0.05},
                "cta": {"box": box(0.05, 0.75, 0.46, 0.07), "max_size": 0.038},
            },
        },
    )

    # 2. full_bleed - photo everywhere, dark scrim only where the words are, with an
    #    accent rule. The most "designed" of the set and the safest over busy photos.
    out["full_bleed"] = template(
        "full_bleed",
        [
            image("bg", box(0, 0, 1, 1)),
            shape("scrim", box(0, 0.44, 1, 0.56), SCRIM, opacity=0.62),
            shape("rule", box(M, 0.5, 0.14, 0.008), SECONDARY),
            head("headline", box(M, 0.54, 1 - 2 * M, 0.18), ON_DARK),
            body("offer", box(M, 0.755, 0.72, 0.07), ON_DARK, "OFFER DETAIL"),
            cta("cta", box(M, 0.86, 0.72, 0.05), ON_DARK),
            logo(box(M, 0.06, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(M, STORY_TOP, LOGO_W, 0.06)},
                "scrim": {"box": box(0, 0.38, 1, STORY_BOTTOM - 0.38 + 0.02)},
                "rule": {"box": box(M, 0.43, 0.14, 0.006)},
                "headline": {"box": box(M, 0.46, 1 - 2 * M, 0.15), "max_size": 0.085},
                "offer": {"box": box(M, 0.63, 0.78, 0.06), "max_size": 0.028},
                "cta": {"box": box(M, 0.71, 0.78, 0.045), "max_size": 0.022},
            },
            "16:9": {
                "scrim": {"box": box(0, 0, 0.62, 1), "opacity": 0.72},
                "logo": {"box": box(0.05, 0.09, 0.18, 0.1)},
                "rule": {"box": box(0.05, 0.27, 0.1, 0.012)},
                "headline": {"box": box(0.05, 0.32, 0.5, 0.26), "max_size": 0.115},
                "offer": {"box": box(0.05, 0.62, 0.5, 0.1), "max_size": 0.05},
                "cta": {"box": box(0.05, 0.76, 0.5, 0.07), "max_size": 0.038},
            },
        },
    )

    # 3. headline_hero - the words ARE the poster, photo is a footer. For offers
    #    whose number is the whole message, and for weak photography.
    out["headline_hero"] = template(
        "headline_hero",
        [
            shape("bg", box(0, 0, 1, 1), SURFACE),
            head(
                "headline",
                box(M, 0.14, 1 - 2 * M, 0.3),
                ON_SURFACE,
                max_lines=3,
                min_size=0.06,
                max_size=0.16,
            ),
            shape("rule", box(M, 0.47, 0.18, 0.01), SECONDARY),
            body("offer", box(M, 0.5, 0.78, 0.08), ON_SURFACE, "OFFER DETAIL"),
            image("bg_photo", box(0, 0.63, 1, 0.37)),
            shape("cta_bar", box(0, 0.88, 1, 0.12), PRIMARY, opacity=0.96),
            cta("cta", box(M, 0.91, 1 - 2 * M, 0.06), ON_PRIMARY, max_size=0.034),
            logo(box(1 - M - LOGO_W, 0.05, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(1 - M - LOGO_W, STORY_TOP, LOGO_W, 0.06)},
                "headline": {"box": box(M, 0.24, 1 - 2 * M, 0.24), "max_size": 0.12},
                "rule": {"box": box(M, 0.5, 0.18, 0.007)},
                "offer": {"box": box(M, 0.53, 0.82, 0.06), "max_size": 0.028},
                "bg_photo": {"box": box(0, 0.62, 1, 0.16)},
                "cta_bar": {"box": box(0, 0.7, 1, 0.08)},
                "cta": {"box": box(M, 0.72, 1 - 2 * M, 0.045), "max_size": 0.024},
            },
            "16:9": {
                "headline": {"box": box(0.05, 0.16, 0.56, 0.3), "max_size": 0.18},
                "rule": {"box": box(0.05, 0.5, 0.12, 0.014)},
                "offer": {"box": box(0.05, 0.54, 0.52, 0.12), "max_size": 0.05},
                "bg_photo": {"box": box(0.64, 0, 0.36, 1)},
                "cta_bar": {"box": box(0, 0.86, 0.62, 0.14)},
                "cta": {"box": box(0.05, 0.89, 0.5, 0.08), "max_size": 0.04},
                "logo": {"box": box(0.05, 0.05, 0.16, 0.1)},
            },
        },
    )

    # 4. split_panel - photo one side, colour panel the other. Reads as a brochure
    #    rather than a social post, which suits trades selling a considered job.
    out["split_panel"] = template(
        "split_panel",
        [
            image("bg", box(0, 0, 1, 0.54)),
            shape("panel", box(0, 0.5, 1, 0.5), PRIMARY),
            shape("rule", box(M, 0.545, 0.14, 0.008), SECONDARY),
            head("headline", box(M, 0.58, 1 - 2 * M, 0.17), ON_PRIMARY),
            body("offer", box(M, 0.775, 0.72, 0.07), ON_PRIMARY, "OFFER DETAIL"),
            cta("cta", box(M, 0.875, 0.72, 0.05), ON_PRIMARY),
            logo(box(1 - M - LOGO_W, 0.055, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(1 - M - LOGO_W, STORY_TOP, LOGO_W, 0.06)},
                "bg": {"box": box(0, 0, 1, 0.46)},
                "panel": {"box": box(0, 0.42, 1, STORY_BOTTOM - 0.42 + 0.02)},
                "rule": {"box": box(M, 0.46, 0.14, 0.006)},
                "headline": {"box": box(M, 0.49, 1 - 2 * M, 0.14), "max_size": 0.085},
                "offer": {"box": box(M, 0.64, 0.78, 0.06), "max_size": 0.028},
                "cta": {"box": box(M, 0.715, 0.78, 0.045), "max_size": 0.022},
            },
            "16:9": {
                "bg": {"box": box(0.44, 0, 0.56, 1)},
                "panel": {"box": box(0, 0, 0.48, 1)},
                "rule": {"box": box(0.05, 0.26, 0.1, 0.012)},
                "headline": {"box": box(0.05, 0.3, 0.38, 0.26), "max_size": 0.105},
                "offer": {"box": box(0.05, 0.61, 0.38, 0.1), "max_size": 0.048},
                "cta": {"box": box(0.05, 0.75, 0.38, 0.07), "max_size": 0.036},
                "logo": {"box": box(0.05, 0.08, 0.16, 0.1)},
            },
        },
    )

    # 5. corner_card - photo full bleed, a card floating with margin all round. The
    #    margin is the whole trick: an edge-to-edge band reads as a template, a
    #    floating card reads as a decision.
    out["corner_card"] = template(
        "corner_card",
        [
            image("bg", box(0, 0, 1, 1), dim=0.12),
            shape("card", box(M, 0.5, 0.74, 0.42), PRIMARY, opacity=0.96, radius=0.02),
            head("headline", box(M + 0.05, 0.545, 0.64, 0.15), ON_PRIMARY, max_size=0.085),
            body("offer", box(M + 0.05, 0.71, 0.6, 0.07), ON_PRIMARY, "OFFER DETAIL"),
            cta("cta", box(M + 0.05, 0.815, 0.6, 0.05), ON_PRIMARY),
            logo(box(1 - M - LOGO_W, 0.06, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(1 - M - LOGO_W, STORY_TOP, LOGO_W, 0.06)},
                "card": {"box": box(M, 0.44, 0.84, 0.32)},
                "headline": {"box": box(M + 0.04, 0.47, 0.76, 0.13), "max_size": 0.075},
                "offer": {"box": box(M + 0.04, 0.615, 0.72, 0.055), "max_size": 0.026},
                "cta": {"box": box(M + 0.04, 0.69, 0.72, 0.042), "max_size": 0.021},
            },
            "16:9": {
                "card": {"box": box(0.05, 0.14, 0.46, 0.72)},
                "headline": {"box": box(0.09, 0.2, 0.38, 0.26), "max_size": 0.1},
                "offer": {"box": box(0.09, 0.51, 0.36, 0.11), "max_size": 0.046},
                "cta": {"box": box(0.09, 0.67, 0.36, 0.08), "max_size": 0.036},
                "logo": {"box": box(0.78, 0.08, 0.17, 0.1)},
            },
        },
    )

    # 6. top_band - headline first, photo second. Inverts the rhythm of every other
    #    template, which is the point: a feed of one rhythm looks like a mill.
    out["top_band"] = template(
        "top_band",
        [
            shape("band", box(0, 0, 1, 0.38), PRIMARY),
            logo(box(M, 0.05, LOGO_W, LOGO_H)),
            head("headline", box(M, 0.16, 1 - 2 * M, 0.17), ON_PRIMARY, max_size=0.09),
            image("bg", box(0, 0.36, 1, 0.64)),
            shape("footer", box(0, 0.82, 1, 0.18), SCRIM, opacity=0.66),
            body("offer", box(M, 0.845, 0.74, 0.06), ON_DARK, "OFFER DETAIL"),
            cta("cta", box(M, 0.92, 0.74, 0.045), ON_DARK),
        ],
        {
            "9:16": {
                "band": {"box": box(0, 0, 1, 0.34)},
                "logo": {"box": box(M, STORY_TOP, LOGO_W, 0.06)},
                "headline": {"box": box(M, 0.24, 1 - 2 * M, 0.09), "max_size": 0.062},
                "bg": {"box": box(0, 0.32, 1, 0.48)},
                "footer": {"box": box(0, 0.66, 1, 0.14)},
                "offer": {"box": box(M, 0.68, 0.78, 0.05), "max_size": 0.026},
                "cta": {"box": box(M, 0.74, 0.78, 0.04), "max_size": 0.021},
            },
            "16:9": {
                "band": {"box": box(0, 0, 0.52, 1)},
                "logo": {"box": box(0.05, 0.08, 0.17, 0.1)},
                "headline": {"box": box(0.05, 0.3, 0.42, 0.26), "max_size": 0.11},
                "bg": {"box": box(0.5, 0, 0.5, 1)},
                "footer": {"box": box(0.5, 0.74, 0.5, 0.26)},
                "offer": {"box": box(0.54, 0.78, 0.42, 0.09), "max_size": 0.046},
                "cta": {"box": box(0.54, 0.89, 0.42, 0.06), "max_size": 0.036},
            },
        },
    )

    # 7. quote_card - a customer's words on brand colour. Different job from the
    #    others: social proof, not an offer.
    out["quote_card"] = template(
        "quote_card",
        [
            shape("bg", box(0, 0, 1, 1), SURFACE),
            shape("rule", box(M, 0.2, 0.1, 0.012), SECONDARY),
            text(
                "quote",
                box(M, 0.25, 1 - 2 * M, 0.3),
                "CUSTOMER QUOTE",
                font="font.display",
                colour=ON_SURFACE,
                align="left",
                max_lines=5,
                min_size=0.035,
                max_size=0.075,
            ),
            body("attribution", box(M, 0.59, 0.7, 0.05), ON_SURFACE, "NAME, SUBURB"),
            image("bg_photo", box(0, 0.7, 1, 0.3)),
            logo(box(M, 0.06, LOGO_W, LOGO_H)),
        ],
        {
            "9:16": {
                "logo": {"box": box(M, STORY_TOP, LOGO_W, 0.06)},
                "rule": {"box": box(M, 0.3, 0.1, 0.008)},
                "quote": {"box": box(M, 0.34, 1 - 2 * M, 0.26), "max_size": 0.062},
                "attribution": {"box": box(M, 0.62, 0.74, 0.04), "max_size": 0.026},
                "bg_photo": {"box": box(0, 0.68, 1, 0.1)},
            },
            "16:9": {
                "rule": {"box": box(0.05, 0.22, 0.08, 0.016)},
                "quote": {"box": box(0.05, 0.28, 0.55, 0.34), "max_size": 0.095},
                "attribution": {"box": box(0.05, 0.68, 0.5, 0.07), "max_size": 0.044},
                "bg_photo": {"box": box(0.66, 0, 0.34, 1)},
                "logo": {"box": box(0.05, 0.08, 0.16, 0.1)},
            },
        },
    )

    # 8. framed - photo inset inside brand colour with a wide border. Reads as an
    #    announcement, which is what festival and opening-hours posts are.
    out["framed"] = template(
        "framed",
        [
            shape("bg", box(0, 0, 1, 1), PRIMARY),
            image("bg_photo", box(0.06, 0.08, 0.88, 0.46)),
            shape("rule", box(0.06, 0.58, 0.12, 0.009), SECONDARY),
            head("headline", box(0.06, 0.61, 0.88, 0.16), ON_PRIMARY, max_size=0.088),
            body("offer", box(0.06, 0.79, 0.7, 0.06), ON_PRIMARY, "OFFER DETAIL"),
            cta("cta", box(0.06, 0.88, 0.7, 0.045), ON_PRIMARY),
            logo(box(1 - M - LOGO_W, 0.885, LOGO_W, 0.07)),
        ],
        {
            "9:16": {
                "bg_photo": {"box": box(0.06, STORY_TOP, 0.88, 0.32)},
                "rule": {"box": box(0.06, 0.53, 0.12, 0.007)},
                "headline": {"box": box(0.06, 0.56, 0.88, 0.13), "max_size": 0.078},
                "offer": {"box": box(0.06, 0.7, 0.76, 0.05), "max_size": 0.026},
                "cta": {"box": box(0.06, 0.755, 0.76, 0.04), "max_size": 0.021},
                "logo": {"box": box(1 - M - LOGO_W, 0.74, LOGO_W, 0.05)},
            },
            "16:9": {
                "bg_photo": {"box": box(0.52, 0.06, 0.42, 0.88)},
                "rule": {"box": box(0.06, 0.26, 0.1, 0.013)},
                "headline": {"box": box(0.06, 0.3, 0.4, 0.24), "max_size": 0.105},
                "offer": {"box": box(0.06, 0.58, 0.38, 0.1), "max_size": 0.046},
                "cta": {"box": box(0.06, 0.72, 0.38, 0.07), "max_size": 0.036},
                "logo": {"box": box(0.06, 0.84, 0.18, 0.09)},
            },
        },
    )

    # 9. before_after - two photos, one claim. The most persuasive thing a trade can
    #    post, and the only template that needs two pictures.
    out["before_after_4x5"] = template(
        "before_after_4x5",
        [
            shape("bg", box(0, 0, 1, 1), SURFACE),
            image("before", box(0, 0.1, 0.5, 0.37)),
            image("after", box(0.5, 0.1, 0.5, 0.37)),
            shape("chip_before", box(0, 0.4, 0.5, 0.07), SCRIM, opacity=0.72),
            text(
                "before_label",
                box(0.03, 0.41, 0.44, 0.05),
                "BEFORE",
                font="font.body_bold",
                colour=ON_DARK,
                max_lines=1,
                min_size=0.018,
                max_size=0.028,
                uppercase=True,
            ),
            shape("chip_after", box(0.5, 0.4, 0.5, 0.07), SCRIM, opacity=0.72),
            text(
                "after_label",
                box(0.53, 0.41, 0.44, 0.05),
                "AFTER",
                font="font.body_bold",
                colour=ON_DARK,
                max_lines=1,
                min_size=0.018,
                max_size=0.028,
                uppercase=True,
            ),
            shape("rule", box(M, 0.53, 0.14, 0.009), SECONDARY),
            head("headline", box(M, 0.56, 1 - 2 * M, 0.16), ON_SURFACE, max_size=0.085),
            cta("contact", box(M, 0.76, 0.7, 0.05), ON_SURFACE),
            logo(box(1 - M - LOGO_W, 0.855, LOGO_W, 0.075)),
        ],
        {
            "9:16": {
                "before": {"box": box(0, 0.19, 0.5, 0.26)},
                "after": {"box": box(0.5, 0.19, 0.5, 0.26)},
                "chip_before": {"box": box(0, 0.39, 0.5, 0.06)},
                "before_label": {"box": box(0.03, 0.4, 0.44, 0.04), "max_size": 0.022},
                "chip_after": {"box": box(0.5, 0.39, 0.5, 0.06)},
                "after_label": {"box": box(0.53, 0.4, 0.44, 0.04), "max_size": 0.022},
                "rule": {"box": box(M, 0.49, 0.14, 0.007)},
                "headline": {"box": box(M, 0.52, 1 - 2 * M, 0.13), "max_size": 0.078},
                "contact": {"box": box(M, 0.67, 0.74, 0.045), "max_size": 0.022},
                "logo": {"box": box(1 - M - LOGO_W, 0.72, LOGO_W, 0.055)},
            },
            "16:9": {
                "before": {"box": box(0, 0, 0.31, 1)},
                "after": {"box": box(0.31, 0, 0.31, 1)},
                "chip_before": {"box": box(0, 0.88, 0.31, 0.12)},
                "before_label": {"box": box(0.02, 0.91, 0.27, 0.06), "max_size": 0.04},
                "chip_after": {"box": box(0.31, 0.88, 0.31, 0.12)},
                "after_label": {"box": box(0.33, 0.91, 0.27, 0.06), "max_size": 0.04},
                "rule": {"box": box(0.66, 0.26, 0.1, 0.013)},
                "headline": {"box": box(0.66, 0.3, 0.3, 0.26), "max_size": 0.1},
                "contact": {"box": box(0.66, 0.6, 0.3, 0.07), "max_size": 0.036},
                "logo": {"box": box(0.66, 0.1, 0.18, 0.1)},
            },
        },
    )

    return out


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for template_id, data in {k: with_logo_chip(v) for k, v in build().items()}.items():
        path = OUT / f"{template_id}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({len(data['layers'])} layers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
