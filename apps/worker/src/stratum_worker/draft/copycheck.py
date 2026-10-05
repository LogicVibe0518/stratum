"""Does each line of a post earn its space? (post.copy v3, M7b)

Found on the live site, 5 Oct: a brief "first time booking is free" became the
headline "First time booking is free" over the offer line "Your first time booking is
free." Two lines, one message: the poster says it twice and nothing else. And a brief
"they do their job really well" came back as "We take pride in doing our job really
well": the brief's own words, polished.

These checks measure that, in code, on the actual output:
- `echo`: the share of the offer line's content words that are already in the
  headline. Above ECHO_MAX the offer only restates the headline;
- `copied_run`: the longest run of words copied from the brief in a row. A fact
  can be copied ("20% off"); a whole sentence should be rewritten;
- `cta_verb`: a call to action starts with what to do ("Book", "Call", "Get").

Words are compared after light stemming ("booking" = "book", "years" = "year"), with
the little words removed. Pure: no model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

ECHO_MAX = 0.6
COPIED_MAX = 5  # words in a row from the brief before a line counts as copied

STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "it",
        "its",
        "it's",
        "this",
        "that",
        "these",
        "those",
        "your",
        "you",
        "you're",
        "yours",
        "our",
        "we",
        "we're",
        "us",
        "my",
        "me",
        "i",
        "i'm",
        "he",
        "she",
        "they",
        "them",
        "their",
        "there",
        "here",
        "so",
        "if",
        "then",
        "than",
        "too",
        "very",
        "really",
        "just",
        "all",
        "any",
        "every",
        "each",
        "some",
        "no",
        "not",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "done",
        "get",
        "got",
        "will",
        "would",
        "can",
        "could",
        "should",
        "shall",
        "may",
        "might",
        "must",
        "now",
        "more",
        "most",
        "much",
        "many",
        "one",
        "ones",
        "also",
        "what",
        "when",
        "where",
        "who",
        "how",
        "why",
        "about",
        "into",
        "over",
        "under",
        "up",
        "down",
        "out",
        "off",
        "doing",
    ]
)

# Words a call to action may start with: what the reader does next.
VERBS = frozenset(
    [
        "book",
        "call",
        "get",
        "grab",
        "claim",
        "message",
        "text",
        "email",
        "visit",
        "order",
        "shop",
        "find",
        "see",
        "try",
        "start",
        "join",
        "reserve",
        "request",
        "ask",
        "enquire",
        "contact",
        "dm",
        "follow",
        "learn",
        "discover",
        "save",
        "sign",
        "apply",
        "register",
        "schedule",
        "secure",
        "check",
        "come",
        "pop",
        "drop",
        "treat",
        "give",
        "send",
    ]
)

_WORD = re.compile(r"[a-z0-9£$€%]+(?:'[a-z]+)?")


def stem(word: str) -> str:
    """Light stemming, enough to match "booking"/"booked"/"book" and "years"/"year"."""
    word = word.lower().removesuffix("'s")
    for suffix, keep in (("ing", 4), ("ied", 3), ("ies", 3), ("ed", 4), ("es", 4), ("s", 3)):
        if word.endswith(suffix) and len(word) - len(suffix) >= keep:
            word = word[: -len(suffix)]
            if suffix in ("ied", "ies"):
                word += "y"
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    """Every word as written, lower case, contractions cut ("we've" -> "we")."""
    folded = text.lower().replace("\u2019", "'")
    return [one.split("'")[0] for one in _WORD.findall(folded)]


def words(text: str) -> list[str]:
    """Every word, stemmed, in order."""
    return [stem(one) for one in _tokens(text)]


def content(text: str) -> set[str]:
    """The words that carry meaning, stemmed."""
    return {stem(one) for one in _tokens(text) if one not in STOP}


def echo(headline: str, offer: str) -> float:
    """The share of the offer's content words already in the headline: 1.0 when the
    offer only says the headline again, 0.0 when it says something new."""
    said = content(offer)
    if not said:
        return 0.0
    return len(said & content(headline)) / len(said)


def copied_run(brief: str, text: str) -> int:
    """The longest run of consecutive words `text` copies from `brief`."""
    left, right = words(brief), words(text)
    best = 0
    previous = [0] * (len(right) + 1)
    for one in left:
        current = [0] * (len(right) + 1)
        for j, two in enumerate(right, start=1):
            if one == two:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def cta_verb(cta: str) -> bool:
    """A call to action starts with what to do next."""
    first = _tokens(cta)
    return bool(first) and stem(first[0]) in {stem(one) for one in VERBS}


@dataclass(frozen=True)
class Problem:
    code: str  # "echo", "copied", "cta_verb"
    line: str  # "offer_detail", "headline", "cta"
    detail: str


def problems(brief: str, headline: str, offer: str, cta: str) -> list[Problem]:
    """What is wrong with the words on a poster, most serious first."""
    found: list[Problem] = []
    repeated = echo(headline, offer)
    if repeated > ECHO_MAX:
        found.append(
            Problem(
                "echo",
                "offer_detail",
                f"the offer line repeats the headline ({repeated:.0%} of its words)",
            )
        )
    for line, text in (("headline", headline), ("offer_detail", offer)):
        run = copied_run(brief, text)
        if run >= COPIED_MAX:
            found.append(Problem("copied", line, f"copies {run} words in a row from the brief"))
    if cta and not cta_verb(cta):
        found.append(Problem("cta_verb", "cta", "the call to action does not start with a verb"))
    return found


def feedback(found: Sequence[Problem], headline: str, offer: str) -> str:
    """What the writer is told when a draft is sent back: the echo only. Copying a
    fact from the brief is often right, so it is measured, not sent back."""
    if not any(one.code == "echo" for one in found):
        return ""
    return (
        f'Your offer_detail "{offer}" said the same as your headline "{headline}". '
        "Write an offer_detail that adds something the headline does not say: the "
        "detail, the proof, or a service and an area from the lists above."
    )


__all__ = [
    "COPIED_MAX",
    "ECHO_MAX",
    "Problem",
    "content",
    "copied_run",
    "cta_verb",
    "echo",
    "feedback",
    "problems",
    "stem",
    "words",
]
