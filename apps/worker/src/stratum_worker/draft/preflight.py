"""Checks that run after a model has written the copy, before a human sees it.

The prompt asks for all of this. Asking is not a guarantee - a prompt is a request to
a system that sometimes ignores requests, and a brief from a client is untrusted
input that may be trying to talk to the model. So everything the client's rules
promise is re-checked here, in code, on the actual output.

Nothing here blocks a draft from being produced. A person has to see the poster to
judge it, so preflight attaches flags and lets the draft through; the review step
decides what to do with a blocker.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from stratum_spec.brand import BrandKit
from stratum_spec.client import ClientRules
from stratum_spec.colour import blend, contrast_ratio, min_contrast_for
from stratum_spec.models import LogoLayer, PosterSpec, ShapeLayer, TextLayer

from stratum_worker.draft.backdrop import ASSUMED_BACKDROP, covers
from stratum_worker.draft.logo_fit import LogoInk, check_logos
from stratum_worker.draft.models import Flag, PostCopy
from stratum_worker.render.text import fit, load_metrics

# Money and percentages: "£49", "$1,299.00", "20%", "20 %".
CLAIM = re.compile(r"[£$€]\s?\d[\d,]*(?:\.\d{1,2})?|\d+(?:\.\d+)?\s?%")
# Runs of digits long enough to be a phone number, licence or registration number.
LONG_NUMBER = re.compile(r"\d[\d\s()+\-]{4,}\d")
SENTENCE_END = re.compile(r"(?<=[.!?])\s|\n")


# "GBP 12.50" in the brief and "£12.50" on the poster are the same money. Without
# this, every correctly-priced post is flagged and the check becomes noise that gets
# ignored - which is worse than not having it, because the one real mispricing then
# scrolls past with the rest.
CURRENCY = {"£": "gbp", "$": "usd", "€": "eur", "gbp": "gbp", "usd": "usd", "eur": "eur"}


def _normalise(text: str) -> str:
    """Strip everything that is presentation, keep everything that is meaning."""
    folded = re.sub(r"[\s,]", "", text).casefold()
    for symbol, code in CURRENCY.items():
        folded = folded.replace(symbol, code)
    return folded


def first_sentence(caption: str) -> str:
    return SENTENCE_END.split(caption.strip(), maxsplit=1)[0]


def _invented_in(text: str, rules: ClientRules, sources: str) -> list[Flag]:
    """Numbers in the copy that are not in the brief.

    This is the check that matters most. A model that turns "20% off" into "25% off",
    or invents a phone number, produces a poster that looks perfect and is a lie the
    client publishes under their own name. Everything numeric must be traceable to
    something a human wrote.
    """
    allowed = _normalise(sources + " " + " ".join(rules.must_include))
    flags: list[Flag] = []
    seen: set[str] = set()
    for pattern, kind in ((CLAIM, "figure"), (LONG_NUMBER, "number")):
        for match in pattern.finditer(text):
            raw = match.group().strip()
            key = _normalise(raw)
            if len(key) < 2 or key in seen or key in allowed:
                continue
            seen.add(key)
            flags.append(
                Flag(
                    code="invented_number",
                    message=(
                        f"The copy says {raw!r}, which nobody asked for. "
                        f"Check this {kind} before anything goes out."
                    ),
                    severity="blocker",
                )
            )
    return flags


def check_claims(text: str, rules: ClientRules, sources: str) -> tuple[Flag, ...]:
    """The checks that ask "did a human authorise this?", over any text.

    `sources` is everything a person actually wrote: the brief, and every tweak they
    have typed since. A designer who asks for "50% rather than 20%" has authorised
    that number - flagging their own instruction as invented would train everyone to
    ignore the one check that catches a model quietly changing a price.
    """
    flags: list[Flag] = list(_invented_in(text, rules, sources))
    haystack = text.casefold()
    for claim in rules.banned_claims:
        if claim.casefold() in haystack:
            flags.append(
                Flag(
                    code="banned_claim",
                    message=f"The copy contains {claim!r}, which this client never says.",
                    severity="blocker",
                )
            )
    for required in rules.must_include:
        if required not in text:
            flags.append(
                Flag(
                    code="missing_required_phrase",
                    message=f"{required!r} has to appear exactly and does not.",
                    severity="blocker",
                )
            )
    return tuple(flags)


def check_copy(copy: PostCopy, rules: ClientRules, brief: str) -> tuple[Flag, ...]:
    """Every rule this client has, checked against what the model actually wrote."""
    flags: list[Flag] = list(check_claims(copy.everything, rules, brief))

    # Discoverability. Instagram search reads the caption directly and weights the
    # opening most, so the terms people type have to be in the FIRST sentence, not
    # merely somewhere further down.
    opening = first_sentence(copy.caption).casefold()
    if rules.services and not any(s.casefold() in opening for s in rules.services):
        flags.append(
            Flag(
                code="no_service_in_first_sentence",
                message=(
                    "The caption's first sentence names none of this client's services, "
                    "so nobody searching for one will find this post."
                ),
            )
        )
    if rules.areas and not any(a.casefold() in opening for a in rules.areas):
        flags.append(
            Flag(
                code="no_area_in_first_sentence",
                message=(
                    "The caption's first sentence names no area this client serves. "
                    "Local searches are most of how these businesses get found."
                ),
            )
        )

    if len(copy.hashtags) > rules.max_hashtags:
        flags.append(
            Flag(
                code="too_many_hashtags",
                message=(
                    f"{len(copy.hashtags)} hashtags, and this client uses at most "
                    f"{rules.max_hashtags}. Extra tags do not add reach."
                ),
            )
        )
    if rules.allowed_hashtags:
        allowed = {tag.casefold() for tag in rules.allowed_hashtags}
        for tag in copy.hashtags:
            if tag.casefold() not in allowed:
                flags.append(
                    Flag(
                        code="hashtag_not_allowed",
                        message=f"{tag} is not one of this client's agreed hashtags.",
                    )
                )

    if not copy.alt_text.strip():
        flags.append(
            Flag(
                code="no_alt_text",
                message=(
                    "No alt text, because nobody described the photo. Add a one-line "
                    "description to the brief and this writes itself."
                ),
            )
        )
    elif len(copy.alt_text) > rules.alt_text_max_chars:
        flags.append(
            Flag(
                code="alt_text_too_long",
                message=(
                    f"Alt text is {len(copy.alt_text)} characters; screen readers do "
                    f"better under {rules.alt_text_max_chars}."
                ),
            )
        )

    return tuple(flags)


# --- does the poster say what the brief asked for? --------------------------------

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
DAY_MONTH = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}(?![a-z])", re.IGNORECASE)
MONTH_DAY = re.compile(rf"\b{_MONTH}\s+{_DAY}\b", re.IGNORECASE)


def _dates(text: str) -> dict[tuple[int, int], str]:
    """(day, month) -> the words as written, for "5th nov", "5 November", "Nov 5"."""
    found: dict[tuple[int, int], str] = {}
    taken: set[tuple[int, int]] = set()  # month words already read as "5th nov"
    for match in DAY_MONTH.finditer(text):
        day, month = int(match.group(1)), _MONTHS.index(match.group(2).casefold()) + 1
        found.setdefault((day, month), match.group().strip())
        taken.add(match.span(2))
    for match in MONTH_DAY.finditer(text):
        if match.span(1) in taken:  # "5th nov 1st feb" is not also "nov 1st"
            continue
        month, day = _MONTHS.index(match.group(1).casefold()) + 1, int(match.group(2))
        found.setdefault((day, month), match.group().strip())
    return {key: raw for key, raw in found.items() if 1 <= key[0] <= 31}


def _figures(text: str) -> dict[str, str]:
    """Money and percentages, normalised -> as written."""
    return {_normalise(m.group()): m.group().strip() for m in CLAIM.finditer(text)}


def _missing(brief: str, text: str) -> list[str]:
    dates = _dates(text)
    missing = [raw for key, raw in _dates(brief).items() if key not in dates]
    shown = _normalise(text)
    missing += [raw for key, raw in _figures(brief).items() if key not in shown]
    return missing


def _listed(items: list[str]) -> str:
    quoted = [f"'{item}'" for item in items]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def check_brief_on_poster(
    brief: str, poster: str, caption: str, *, has_offer_line: bool
) -> tuple[Flag, ...]:
    """The brief's offer and dates, when the poster itself does not show them.

    Found on the live site: a brief said "5th nov 2026 to 1st feb 2027 ... 50% off",
    the model wrote both into the offer line, and the design had no offer line - so
    the poster showed neither, and the draft said "Nothing to check". A figure the
    client asked for is exactly what their customers look for on the poster.
    """
    missing = _missing(brief, poster)
    if not missing:
        return ()
    them = "it" if len(missing) == 1 else "them"
    where = (
        f" The caption has {them}."
        if caption and not _missing(" ; ".join(missing), caption)
        else ""
    )
    fix = (
        f"To add {them}, type a change such as 'add {missing[0]} to the offer line'."
        if has_offer_line
        else f"This design has no line for {them}: to show {them}, make the post with "
        f"a design that has an offer line."
    )
    return (
        Flag(
            code="brief_not_on_poster",
            message=f"The brief says {_listed(missing)}, but the poster does not.{where} {fix}",
        ),
    )


__all__ = [
    "check_brief_on_poster",
    "check_claims",
    "check_copy",
    "check_layout",
    "first_sentence",
]


# --- the poster itself ------------------------------------------------------------

# Story and Reel furniture - profile row, caption, buttons - covers roughly this much
# of a 9:16 screen. Anything inside those bands is there but unreadable.
STORY_SAFE_TOP = 0.14
STORY_SAFE_BOTTOM = 0.80
ELLIPSIS = "…"


_covers = covers


def _backing(spec: PosterSpec, index: int) -> ShapeLayer | None:
    """The shape a text layer is sitting on, if any.

    Searched backwards from the text layer because list order is z-order: the LAST
    shape before the text is the one actually visible behind it.
    """
    text = spec.layers[index]
    for layer in reversed(spec.layers[:index]):
        if isinstance(layer, ShapeLayer) and layer.visible and _covers(layer.box, text.box):
            return layer
    return None


def _contrast_flags(
    spec: PosterSpec, index: int, layer: TextLayer, size_fraction: float, kit: BrandKit
) -> list[Flag]:
    """Is this text actually readable where it sits?

    Contrast is arithmetic, not taste, and it is the one design rule that is not
    subjective: below the WCAG floor a real person on a phone in daylight cannot read
    it. Sharma's Dhaba shipped 35 posters at 2.8:1 before this check existed.
    """
    colour = kit.color(layer.color)
    backing = _backing(spec, index)
    if backing is None:
        return [
            Flag(
                code="text_directly_on_photo",
                message=(
                    f"'{layer.id}' sits straight on the photo with nothing behind it, "
                    f"so whether it can be read depends on the picture."
                ),
            )
        ]
    behind = blend(kit.color(backing.fill), ASSUMED_BACKDROP, backing.opacity)
    ratio = contrast_ratio(colour, behind)
    needed = min_contrast_for(size_fraction)
    if ratio >= needed:
        return []
    return [
        Flag(
            code="low_contrast",
            message=(
                f"'{layer.id}' is {ratio:.1f}:1 against what is behind it, and needs "
                f"{needed:.1f}:1 to be readable. Change the colour, not the words."
            ),
            severity="blocker",
        )
    ]


def check_layout(
    spec: PosterSpec,
    kit: BrandKit,
    *,
    root: Path,
    logos: Mapping[str, LogoInk] | None = None,
) -> tuple[Flag, ...]:
    """What the rendered poster gets wrong, found without looking at the pixels.

    Every check here re-derives what the renderer will do rather than inspecting the
    PNG, so it says WHICH layer is wrong and why - something no pixel diff can do.
    `logos` maps a logo's SHA-256 to its colours; without it the logo is not checked.
    """
    flags: list[Flag] = []
    width, height = spec.canvas.size

    for layer_id, share in check_logos(spec, kit, logos or {}):
        flags.append(
            Flag(
                code="logo_hard_to_see",
                message=(
                    f"Only {share:.0%} of '{layer_id}' stands out from what is behind it, "
                    f"so the logo will be hard to see in {spec.canvas.aspect}. Try another "
                    f"design, or upload a version of the logo for dark or light backgrounds."
                ),
                severity="blocker",
            )
        )

    # A backing shape exists only to make something else readable. If the thing it
    # backs is hidden, the shape is a grey rectangle floating in a corner - which is
    # exactly what shipped the first time a logo was hidden by hand.
    hidden = {one.id for one in spec.layers if not one.visible}
    for layer in spec.layers:
        backed = layer.id.removesuffix("_chip")
        if layer.visible and layer.id.endswith("_chip") and backed in hidden:
            flags.append(
                Flag(
                    code="orphan_backing",
                    message=(
                        f"'{layer.id}' is still showing but '{backed}' is hidden, so "
                        f"there is a plain shape sitting on the poster for no reason."
                    ),
                    severity="blocker",
                )
            )

    for index, layer in enumerate(spec.layers):
        if not layer.visible:
            continue
        top, bottom = layer.box.y, layer.box.y + layer.box.h

        if spec.canvas.aspect == "9:16" and isinstance(layer, TextLayer | LogoLayer):
            if top < STORY_SAFE_TOP:
                flags.append(
                    Flag(
                        code="inside_story_safe_zone",
                        message=(
                            f"'{layer.id}' sits under the profile row at the top of a "
                            f"story, where nobody will read it."
                        ),
                    )
                )
            elif bottom > STORY_SAFE_BOTTOM:
                flags.append(
                    Flag(
                        code="inside_story_safe_zone",
                        message=(
                            f"'{layer.id}' sits behind the caption and buttons at the "
                            f"bottom of a story."
                        ),
                    )
                )

        if isinstance(layer, LogoLayer):
            margin = layer.safe_area
            if (
                layer.box.x < margin
                or top < margin
                or layer.box.x + layer.box.w > 1 - margin
                or bottom > 1 - margin
            ):
                flags.append(
                    Flag(
                        code="logo_too_close_to_edge",
                        message=(
                            f"The logo is inside its own {margin:.0%} safe margin. "
                            f"Printed or cropped, it will touch the edge."
                        ),
                    )
                )

        if isinstance(layer, TextLayer):
            face = kit.font(layer.font)
            content = layer.content.upper() if layer.uppercase else layer.content
            fitted = fit(
                content,
                load_metrics(str(root / face.file)),
                box_w_px=layer.box.w * width,
                box_h_px=layer.box.h * height,
                min_px=layer.min_size * height,
                max_px=layer.max_size * height,
                max_lines=layer.max_lines,
                line_height=face.line_height,
                balanced=layer.wrap == "balanced",
            )
            if fitted.lines and fitted.lines[-1].endswith(ELLIPSIS):
                flags.append(
                    Flag(
                        code="text_truncated",
                        message=(
                            f"'{layer.id}' is too long for its box even at the smallest "
                            f"size, so it has been cut short. Shorten the words."
                        ),
                        severity="blocker",
                    )
                )
            flags.extend(_contrast_flags(spec, index, layer, fitted.size_px / height, kit))

            if fitted.size_px <= layer.min_size * height + 0.5:
                flags.append(
                    Flag(
                        code="text_at_minimum_size",
                        message=(
                            f"'{layer.id}' had to shrink to its smallest size to fit. "
                            f"It will be hard to read on a phone."
                        ),
                    )
                )

    return tuple(flags)
