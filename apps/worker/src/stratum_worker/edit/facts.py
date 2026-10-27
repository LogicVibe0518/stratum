"""An edit keeps the poster's facts (trust fix, 26 Oct 2026).

Live, 26 Oct: a designer wrote "2 times you have written signup and book and nowhere
its mentioning about the black friday sale". The model set the headline to "Black
Friday Sale", and "30% off home cleaning" was gone - the offer itself. The same
message on another version had kept it. A model will do this now and then; the
engine around it is what must not.

The rule: new words from the model may move a price, a percentage, a date or a phone
number, or say it differently ("31 October" for "31 Oct"), but may not leave it out,
unless the message is about that kind of fact ("make it 40% off", "drop the date").
When they would, the model is asked once more with the facts named; if it still
drops them, the poster is left as it was and the designer is told which, in a
sentence. Hiding a line is not a rewrite: "hide the offer" hides it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from stratum_spec.models import PosterSpec, TextLayer
from stratum_spec.ops import EditOp, OpError, SetText, apply_ops

from stratum_worker.draft.preflight import CLAIM, DAY_MONTH, MONTH_DAY, facts_missing

# What a message says when it is about that kind of fact on purpose.
FIGURE_WORDS = re.compile(
    r"%|[£$€]|\b(?:percent(?:age)?s?|prices?|pricing|discounts?|costs?|cheaper|free)\b",
    re.IGNORECASE,
)
DATE_WORDS = re.compile(
    r"\b(?:dates?|deadlines?|expir(?:y|es)|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?"
    r"|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
NUMBER_WORDS = re.compile(r"\b(?:phone|telephone|mobile|tel|numbers?)\b", re.IGNORECASE)


def shown_words(spec: PosterSpec) -> str:
    """Every word the poster shows, one line per text layer."""
    return "\n".join(
        layer.content for layer in spec.layers if isinstance(layer, TextLayer) and layer.visible
    )


def _kind(fact: str) -> str:
    if CLAIM.search(fact):
        return "figure"
    if DAY_MONTH.search(fact) or MONTH_DAY.search(fact):
        return "date"
    return "number"


def asked_about(message: str, fact: str) -> bool:
    """Is the message changing this fact, or this kind of fact, on purpose?"""
    if re.sub(r"\s", "", fact).casefold() in re.sub(r"\s", "", message).casefold():
        return True
    kind = _kind(fact)
    if kind == "figure":
        return bool(FIGURE_WORDS.search(message) or CLAIM.search(message))
    if kind == "date":
        return bool(DATE_WORDS.search(message))
    return bool(NUMBER_WORDS.search(message))


def lost(spec: PosterSpec, ops: Sequence[EditOp], message: str) -> list[str]:
    """The facts on the poster that these ops' new words leave out, unasked."""
    rewrites: list[EditOp] = [op for op in ops if isinstance(op, SetText)]
    if not rewrites:
        return []
    try:
        after = apply_ops(spec, rewrites)
    except OpError:
        return []  # the change is refused later, with its own sentence
    missing = facts_missing(shown_words(spec), shown_words(after))
    return [fact for fact in missing if not asked_about(message, fact)]


def listed(facts: Sequence[str]) -> str:
    quoted = [f"'{fact}'" for fact in facts]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def keep_line(facts: Sequence[str]) -> str:
    """Said to the model when it is asked again."""
    return f"Whatever else changes, the poster must still show {listed(facts)}."


def refusal(facts: Sequence[str]) -> str:
    """Said to the designer when the model would still drop them."""
    them = "it" if len(facts) == 1 else "them"
    return (
        f"That change would take {listed(facts)} off the poster, so I left it as it was. "
        f"Say it again with where {them} should go - for example 'keep {facts[0]} in "
        f"the headline' - or say 'remove {facts[0]}' if you meant it."
    )


__all__ = ["asked_about", "keep_line", "listed", "lost", "refusal", "shown_words"]
