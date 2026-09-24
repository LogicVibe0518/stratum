"""Text measurement, wrapping and auto-fit.

The renderer draws every character itself, so it must answer one question exactly:
"at what size does this headline fill its box without overflowing?" We measure with
the real font metrics (fontTools), wrap greedily, then binary-search the size.
"""

from __future__ import annotations

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
) -> FittedText:
    """Largest size that fits the box within max_lines. Never overflows: at the
    minimum size we truncate with an ellipsis rather than spill over the design."""

    def layout(size: float) -> tuple[list[str], bool]:
        lines = wrap(text, metrics, size, box_w_px)
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
