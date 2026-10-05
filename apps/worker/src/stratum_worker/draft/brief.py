"""A smarter brief box: what a brief is missing, said while it is being typed (M7b).

A post is only as good as the facts it is given. The live briefs of 5 Oct were one
fact each ("first time booking is free", "we have been doing roofing for the past
8yrs"), so the words on the poster had nothing to say but that fact, twice. The
fastest fix is before the model: tell the person what one more detail would buy.

`gaps` reads a brief against the client's profile, in code, in microseconds, so the
page can ask as the person types. At most three questions, the most useful first:
- too short to say anything;
- nothing concrete: no price, number, date or named service;
- an offer with no end date (people act on deadlines);
- no service from the client's list;
- no area from the client's list, for an offer;
- a client with no services or areas at all: the model then has nothing true to
  add, and the caption's first sentence has nothing to be found by.

Pure: no model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from stratum_worker.draft.copycheck import content

MAX_GAPS = 3
SHORT = 4  # words

_NUMBER = re.compile(r"\d|[£$€]")
_OFFER = re.compile(
    r"\b(free|off|discount|deal|sale|offer|save|half price|bogo|voucher|promo)\b|%", re.I
)
_WHEN = re.compile(
    r"\b(today|tonight|tomorrow|week|weekend|month|year|until|till|til|ends?|by|before|"
    r"this|next|only|mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"january|february|march|april|june|july|august|september|october|november|"
    r"december|spring|summer|autumn|fall|winter|christmas|easter|halloween)\b",
    re.I,
)


@dataclass(frozen=True)
class Gap:
    code: str
    question: str
    example: str  # words the person could add, in their own voice

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "question": self.question, "example": self.example}


def _mentions(brief: str, names: Sequence[str]) -> bool:
    said = content(brief)
    return any(content(name) and content(name) <= said for name in names) or any(
        content(name) & said for name in names if len(content(name)) == 1
    )


def gaps(brief: str, *, services: Sequence[str] = (), areas: Sequence[str] = ()) -> list[Gap]:
    """What would make this brief a better post, most useful first (at most three)."""
    text = brief.strip()
    found: list[Gap] = []
    if len(text.split()) < SHORT:
        found.append(
            Gap(
                "too_short",
                "Say a little more: what is happening, and why should people care?",
                "Boiler service, 20% off for bookings this month",
            )
        )
    offer = bool(_OFFER.search(text))
    concrete = bool(_NUMBER.search(text)) or offer or _mentions(text, services)
    if text and not concrete:
        found.append(
            Gap(
                "no_detail",
                "What is the one detail people should remember? A price, a date, a number.",
                "8 years, 400 roofs, fully insured",
            )
        )
    if offer and not _WHEN.search(text):
        found.append(
            Gap("no_end", "Until when? An end date makes people act now.", "until 31 October")
        )
    if services and not _mentions(text, services):
        found.append(
            Gap(
                "no_service",
                f"Which service is it about? For example {', '.join(services[:3])}.",
                services[0],
            )
        )
    if areas and offer and not _mentions(text, areas):
        found.append(
            Gap("no_area", f"Where? For example {', '.join(areas[:3])}.", f"in {areas[0]}")
        )
    if not services and not areas:
        found.append(
            Gap(
                "no_profile",
                "This client has no services or areas saved. Add them to the client: the "
                "AI uses them for a second line and to help the post be found.",
                "",
            )
        )
    return found[:MAX_GAPS]


__all__ = ["MAX_GAPS", "Gap", "gaps"]
