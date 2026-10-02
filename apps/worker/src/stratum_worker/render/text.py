"""Text measurement, wrapping and auto-fit.

The renderer draws every character itself, so it must answer one question exactly:
"at what size does this headline fill its box without overflowing?" We measure with
the real font metrics (fontTools), wrap, then binary-search the size.

Two ways to wrap, chosen per text layer (TextLayer.wrap, spec schema 5):
- greedy: as many words as fit on each line. What every poster before schema 5 used,
  kept for them so an approved poster never re-renders differently.
- balanced: the same number of lines as greedy (so every height and every layout is
  unchanged), but the words shared out evenly and the breaks put where the meaning
  breaks: "A LITTLE PEACE / FOR YOUR MIND", never "A LITTLE PEACE FOR YOUR / MIND";
  "50% off" and "5 Nov" kept together; no line ending on "the", "for" or "your".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

FIT_ITERATIONS = 18  # binary search steps: ~0.004px precision over a 1000px range


@dataclass(frozen=True)
class Metrics:
    """Advance widths in font units, plus the em size they are relative to."""

    units_per_em: int
    advances: dict[int, int]
    fallback_advance: int

    def width(self, text: str, size_px: float) -> float:
        total = sum(self.advances.get(ord(ch), self.fallback_advance) for ch in text)
        return total * size_px / self.units_per_em


@lru_cache(maxsize=32)
def load_metrics(font_path: str) -> Metrics:
    """Read advance widths once per font file; cached because fonts are reused."""
    font = TTFont(Path(font_path), lazy=True)
    upem = int(font["head"].unitsPerEm)
    hmtx = font["hmtx"]
    cmap = font.getBestCmap()
    advances = {code: int(hmtx[name][0]) for code, name in cmap.items() if name in hmtx.metrics}
    space = advances.get(ord(" "), upem // 4)
    font.close()
    return Metrics(units_per_em=upem, advances=advances, fallback_advance=space)


def wrap(text: str, metrics: Metrics, size_px: float, max_width_px: float) -> list[str]:
    """Greedy word wrap. A single word wider than the box gets its own line."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and metrics.width(candidate, size_px) > max_width_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


# A line should not end on these: they lean on the word after them.
LEANS_FORWARD = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "for",
        "to",
        "with",
        "and",
        "or",
        "in",
        "on",
        "at",
        "by",
        "from",
        "into",
        "onto",
        "your",
        "our",
        "my",
        "their",
        "his",
        "her",
        "its",
        "this",
        "that",
        "these",
        "those",
        "every",
        "each",
        "no",
        "&",
        "+",
    ]
)
# A line may well start with these: each opens a phrase.
OPENS = frozenset(
    [
        "for",
        "with",
        "from",
        "and",
        "or",
        "to",
        "in",
        "on",
        "at",
        "by",
        "when",
        "while",
        "because",
        "so",
        "but",
        "until",
        "till",
        "then",
        "plus",
        "only",
        "every",
        "all",
    ]
)
# Words that belong to the number before them: "20% off", "5 Nov", "3 days".
UNITS = frozenset(
    [
        "off",
        "%",
        "am",
        "pm",
        "years",
        "year",
        "yrs",
        "mins",
        "minutes",
        "hours",
        "hrs",
        "days",
        "day",
        "nights",
        "weeks",
        "months",
        "miles",
        "km",
        "people",
        "per",
        "x",
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
)
NUMBER = re.compile(r"^[£$€#]?\d")
CLOSES = (",", ";", ":", ".", "!", "?", "\u2014", "\u2013", "-", "/")  # \u2014/\u2013: dashes
QUOTES = "\"'\u2018\u2019\u201c\u201d()"
WEIGHT = 0.25  # one unit of break penalty is worth this much of a line's squared slack
WIDOW = 0.5  # a last line of one word, when there is more than one line


def break_cost(words: list[str], index: int) -> float:
    """What breaking the line after words[index] costs: negative where the meaning
    breaks, positive where it would split a phrase."""
    word, following = words[index], words[index + 1]
    plain = word.lower().strip(QUOTES)
    nxt = following.lower().strip(QUOTES + ",.;:!?")
    cost = 0.0
    if word.endswith(CLOSES):
        cost -= 0.6
    if plain.rstrip(",.;:!?") in LEANS_FORWARD:
        cost += 1.0
    if nxt in OPENS:
        cost -= 0.3
    if NUMBER.match(plain) and (nxt in UNITS or nxt.startswith("%") or NUMBER.match(nxt)):
        cost += 1.0  # "20% off", "5 Nov", and a phone number in one piece
    if NUMBER.match(nxt) and plain in {"under", "over", "from", "to", "until", "by", "at", "on"}:
        cost += 0.5
    return cost


def wrap_balanced(text: str, metrics: Metrics, size_px: float, max_width_px: float) -> list[str]:
    """The greedy number of lines, with the words shared out evenly and broken where
    the meaning breaks. Every line fits whenever greedy's do."""
    greedy = wrap(text, metrics, size_px, max_width_px)
    words = text.split()
    count = len(greedy)
    if count < 2 or len(words) <= count:
        return greedy
    space = metrics.width(" ", size_px)
    widths = [metrics.width(word, size_px) for word in words]
    total = len(words)

    def width(start: int, end: int) -> float:
        return sum(widths[start:end]) + space * (end - start - 1)

    # best[k][j]: the cheapest way to set words[:j] on k lines.
    inf = float("inf")
    best = [[inf] * (total + 1) for _ in range(count + 1)]
    back = [[0] * (total + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for lines in range(1, count + 1):
        for end in range(lines, total + 1):
            for start in range(lines - 1, end):
                if best[lines - 1][start] == inf:
                    continue
                used = width(start, end)
                if used > max_width_px and end - start > 1:
                    continue
                slack = max(0.0, (max_width_px - used) / max_width_px)
                cost = best[lines - 1][start] + slack * slack
                if end < total:
                    cost += WEIGHT * break_cost(words, end - 1)
                if lines == count and end == total and end - start == 1:
                    cost += WIDOW
                if cost < best[lines][end]:
                    best[lines][end], back[lines][end] = cost, start
    if best[count][total] == inf:
        return greedy
    out: list[str] = []
    end = total
    for lines in range(count, 0, -1):
        start = back[lines][end]
        out.append(" ".join(words[start:end]))
        end = start
    return out[::-1]


@dataclass(frozen=True)
class FittedText:
    size_px: float
    lines: list[str]
    line_height_px: float

    @property
    def height_px(self) -> float:
        return self.line_height_px * len(self.lines)


def fit(
    text: str,
    metrics: Metrics,
    *,
    box_w_px: float,
    box_h_px: float,
    min_px: float,
    max_px: float,
    max_lines: int,
    line_height: float,
    balanced: bool = False,
) -> FittedText:
    """Largest size that fits the box within max_lines. Never overflows: at the
    minimum size we truncate with an ellipsis rather than spill over the design.
    `balanced` changes which words share a line, never how many lines there are."""
    wrapper = wrap_balanced if balanced else wrap

    def layout(size: float) -> tuple[list[str], bool]:
        lines = wrapper(text, metrics, size, box_w_px)
        fits = (
            len(lines) <= max_lines
            and size * line_height * len(lines) <= box_h_px
            and all(metrics.width(line, size) <= box_w_px for line in lines)
        )
        return lines, fits

    low, high = min_px, max(min_px, max_px)
    best_lines, ok = layout(low)
    if not ok:
        best_lines = _truncate(best_lines, metrics, low, box_w_px, max_lines)
        return FittedText(low, best_lines, low * line_height)

    best = low
    for _ in range(FIT_ITERATIONS):
        mid = (low + high) / 2
        lines, fits = layout(mid)
        if fits:
            best, best_lines, low = mid, lines, mid
        else:
            high = mid
    return FittedText(best, best_lines, best * line_height)


def _truncate(
    lines: list[str], metrics: Metrics, size: float, box_w_px: float, max_lines: int
) -> list[str]:
    kept = lines[:max_lines]
    if not kept:
        return [""]
    last = kept[-1]
    while last and metrics.width(f"{last}…", size) > box_w_px:
        last = last[:-1].rstrip()
    kept[-1] = f"{last}…" if last else "…"
    return kept
