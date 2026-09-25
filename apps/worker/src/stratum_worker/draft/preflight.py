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

from stratum_spec.client import ClientRules

from stratum_worker.draft.models import Flag, PostCopy

# Money and percentages: "£49", "$1,299.00", "20%", "20 %".
CLAIM = re.compile(r"[£$€]\s?\d[\d,]*(?:\.\d{1,2})?|\d+(?:\.\d+)?\s?%")
# Runs of digits long enough to be a phone number, licence or registration number.
LONG_NUMBER = re.compile(r"\d[\d\s()+\-]{4,}\d")
SENTENCE_END = re.compile(r"(?<=[.!?])\s|\n")


def _normalise(text: str) -> str:
    return re.sub(r"[\s,]", "", text).casefold()


def first_sentence(caption: str) -> str:
    return SENTENCE_END.split(caption.strip(), maxsplit=1)[0]


def _invented(copy: PostCopy, rules: ClientRules, brief: str) -> list[Flag]:
    """Numbers in the copy that are not in the brief.

    This is the check that matters most. A model that turns "20% off" into "25% off",
    or invents a phone number, produces a poster that looks perfect and is a lie the
    client publishes under their own name. Everything numeric must be traceable to
    something a human wrote.
    """
    allowed = _normalise(brief + " " + " ".join(rules.must_include))
    flags: list[Flag] = []
    seen: set[str] = set()
    for pattern, kind in ((CLAIM, "figure"), (LONG_NUMBER, "number")):
        for match in pattern.finditer(copy.everything):
            raw = match.group().strip()
            key = _normalise(raw)
            if len(key) < 2 or key in seen or key in allowed:
                continue
            seen.add(key)
            flags.append(
                Flag(
                    code="invented_number",
                    message=(
                        f"The copy says {raw!r}, which is not in the brief. "
                        f"Check this {kind} before anything goes out."
                    ),
                    severity="blocker",
                )
            )
    return flags


def check_copy(copy: PostCopy, rules: ClientRules, brief: str) -> tuple[Flag, ...]:
    """Every rule this client has, checked against what the model actually wrote."""
    flags: list[Flag] = list(_invented(copy, rules, brief))
    haystack = copy.everything.casefold()

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
        if required not in copy.everything:
            flags.append(
                Flag(
                    code="missing_required_phrase",
                    message=f"{required!r} has to appear exactly and does not.",
                    severity="blocker",
                )
            )

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


__all__ = ["check_copy", "first_sentence"]
