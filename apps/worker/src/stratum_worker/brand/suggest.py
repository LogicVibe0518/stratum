"""From the colours in a logo to a whole brand palette, with every choice explained.

A logo gives one to three colours. A poster needs seven: the main colour, an accent,
a page colour, dark text, and three colours of text that must stay readable on the
others. The logo decides the first two when it can. The rest are derived from them,
and each derived colour is chosen to clear a WCAG contrast floor, so a suggested
palette cannot produce a poster whose words cannot be read.

Every token comes back with a sentence saying where it came from, because a designer
who is told "orange - the logo's second colour, 9% of it" can disagree in one click,
and one who is shown a bare hex code cannot.

Fonts are not suggested. Reading a typeface from a logo needs a trained model, and a
wrong font is worse than a plain one. The kit keeps whatever fonts it already had.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from stratum_spec import BrandKit
from stratum_spec.brand import FontFace
from stratum_spec.colour import MIN_CONTRAST_NORMAL, contrast_ratio

from stratum_worker.brand.logo import COLOURFUL, LogoReading, Swatch
from stratum_worker.brand.oklab import distance, from_lch, lch_of

# The tokens every template in templates/ uses. A kit without one of them fails at
# the first draft, so saving one is refused instead.
REQUIRED = (
    "primary",
    "secondary",
    "surface",
    "text",
    "text_on_primary",
    "text_on_dark",
    "muted",
)
# Text on a page should be comfortably past the floor, not just over it.
TEXT_ON_PAGE = 7.0
# WCAG's floor for graphics that must be seen, such as a logo.
MIN_CONTRAST_GRAPHIC = 3.0
# An accent must look clearly different from the main colour to be worth having.
DISTINCT = 0.12
WHITE = "#ffffff"
BLACK = "#000000"

# What a new client gets before anyone has read their logo: plain charcoal and grey.
# Deliberately unbranded, so nobody mistakes it for the client's colours.
STARTER_PALETTE: dict[str, str] = {
    "primary": "#2f343b",
    "secondary": "#8a94a3",
    "surface": "#f5f6f7",
    "text": "#1f2328",
    "text_on_primary": "#ffffff",
    "text_on_dark": "#ffffff",
    "muted": "#5f6670",
}
STARTER_FONTS: dict[str, FontFace] = {
    "display": FontFace(family="Anton", file="assets/fonts/Anton-Regular.ttf", line_height=1.05),
    "body": FontFace(family="Barlow", file="assets/fonts/Barlow-Regular.ttf", line_height=1.25),
    "body_bold": FontFace(
        family="Barlow", file="assets/fonts/Barlow-Bold.ttf", weight=700, line_height=1.25
    ),
}

Source = Literal["logo", "derived"]


def starter_kit(client_id: str) -> BrandKit:
    return BrandKit(
        client_id=client_id, version=1, palette=dict(STARTER_PALETTE), fonts=dict(STARTER_FONTS)
    )


@dataclass(frozen=True)
class Choice:
    hex: str
    source: Source
    why: str


@dataclass(frozen=True)
class Check:
    what: str
    ratio: float
    needs: float

    @property
    def ok(self) -> bool:
        return self.ratio >= self.needs

    def sentence(self) -> str:
        verdict = "fine" if self.ok else f"needs {self.needs:g}:1"
        return f"{self.what}: {self.ratio:.1f}:1, {verdict}."


@dataclass(frozen=True)
class Suggestion:
    kit: BrandKit
    tokens: dict[str, Choice]
    checks: tuple[Check, ...]
    notes: tuple[str, ...]
    reading: LogoReading
    logo: str = ""  # the SHA-256 of the logo file it was read from


def is_starter(kit: BrandKit) -> bool:
    """True while a client still has the plain starter colours, not their own."""
    return all(kit.palette.get(token) == value for token, value in STARTER_PALETTE.items())


def missing_tokens(palette: Mapping[str, str]) -> list[str]:
    return [token for token in REQUIRED if token not in palette]


def check_palette(palette: Mapping[str, str]) -> tuple[Check, ...]:
    """The three pairings every template relies on, measured."""
    return (
        Check(
            "Words on the main colour",
            round(contrast_ratio(palette["text_on_primary"], palette["primary"]), 2),
            MIN_CONTRAST_NORMAL,
        ),
        Check(
            "Words on the page colour",
            round(contrast_ratio(palette["text"], palette["surface"]), 2),
            MIN_CONTRAST_NORMAL,
        ),
        Check(
            "Words on dark bands and photo shading",
            round(contrast_ratio(palette["text_on_dark"], palette["text"]), 2),
            MIN_CONTRAST_NORMAL,
        ),
    )


def _logo_checks(logo: Swatch, palette: Mapping[str, str]) -> tuple[Check, ...]:
    return (
        Check(
            "The logo on the main colour",
            round(contrast_ratio(logo.hex, palette["primary"]), 2),
            MIN_CONTRAST_GRAPHIC,
        ),
        Check(
            "The logo on the page colour",
            round(contrast_ratio(logo.hex, palette["surface"]), 2),
            MIN_CONTRAST_GRAPHIC,
        ),
    )


def _readable(preferred: str, fallback: str, behind: str, floor: float) -> str:
    """The brand-tinted colour if it clears the floor, the plain one if not."""
    return preferred if contrast_ratio(preferred, behind) >= floor else fallback


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def suggest(reading: LogoReading, base: BrandKit) -> Suggestion:
    """A full palette from a logo reading. Fonts and any extra tokens are kept."""
    notes = list(reading.notes)
    tokens: dict[str, Choice] = {}
    candidates = [s for s in reading.colourful if s.lightness <= 0.92 and s.share >= 0.05]
    main_light = next((s for s in reading.swatches if s.lightness >= 0.85), None)

    # --- main colour --------------------------------------------------------------
    if candidates:
        first = candidates[0]
        tokens["primary"] = Choice(
            first.hex, "logo", f"The logo's main colour ({_pct(first.share)} of it)."
        )
    elif reading.tone == "dark":
        darkest = min(reading.swatches, key=lambda s: s.lightness)
        tokens["primary"] = Choice(
            darkest.hex, "logo", "The logo is one dark colour, so that colour leads."
        )
        notes.append("The logo has no colour of its own. Pick an accent if the brand has one.")
    else:
        light = reading.swatches[0]
        _, chroma, hue = lch_of(light.hex)
        tokens["primary"] = Choice(
            from_lch(0.3, min(chroma, 0.04), hue),
            "derived",
            "A dark shade of the logo's colour. The logo is light, made to sit on something dark.",
        )
        notes.append(
            "The logo is a single light colour, so it can't show the brand's main colour. "
            "If the brand has one, set it here."
        )
    primary = tokens["primary"].hex
    p_light, p_chroma, p_hue = lch_of(primary)
    colourful = p_chroma >= COLOURFUL

    # --- accent -------------------------------------------------------------------
    others = [
        s
        for s in reading.colourful
        if s.hex != primary and s.share >= 0.03 and distance(s.hex, primary) >= DISTINCT
    ]
    if others:
        # Big AND different: in a gradient every step is the same size, so the far end
        # wins; in a two-colour logo there is only one choice anyway. Distance is capped
        # so a speck of a very different colour cannot beat a real second colour.
        second = max(others, key=lambda s: (s.share * min(distance(s.hex, primary), 0.3), s.hex))
        tokens["secondary"] = Choice(
            second.hex, "logo", f"The logo's second colour ({_pct(second.share)} of it)."
        )
    elif colourful:
        lighter = p_light < 0.55
        tokens["secondary"] = Choice(
            from_lch(0.72 if lighter else 0.38, max(p_chroma, 0.1), p_hue),
            "derived",
            f"A {'lighter' if lighter else 'darker'} shade of the main colour, because the "
            f"logo has only one colour.",
        )
    else:
        tokens["secondary"] = Choice(
            from_lch(0.62, 0.0, 0.0), "derived", "A mid grey until the brand has an accent."
        )

    # --- page and text ------------------------------------------------------------
    surface = from_lch(0.975, min(0.015, 0.3 * p_chroma), p_hue)
    tokens["surface"] = Choice(surface, "derived", "A near-white with a trace of the main colour.")

    if p_light <= 0.45 and contrast_ratio(primary, surface) >= TEXT_ON_PAGE:
        tokens["text"] = Choice(primary, "logo", "The main colour, dark enough to read as text.")
    else:
        lightness = 0.24
        dark = from_lch(lightness, min(0.03, 0.5 * p_chroma), p_hue)
        while contrast_ratio(dark, surface) < TEXT_ON_PAGE and lightness > 0.0:
            lightness -= 0.02
            dark = from_lch(lightness, min(0.03, 0.5 * p_chroma), p_hue)
        tokens["text"] = Choice(dark, "derived", "A near-black with a hint of the main colour.")
    text = tokens["text"].hex

    # Light words use the logo's own colour when the logo is a light one: that is what
    # the brand already prints on dark backgrounds.
    light_words = main_light.hex if (main_light and reading.tone == "light") else WHITE
    light_why = "The logo's own colour." if light_words != WHITE else "White."

    on_light = contrast_ratio(light_words, primary)
    on_dark = contrast_ratio(text, primary)
    if on_light >= on_dark:
        pick = _readable(light_words, WHITE, primary, MIN_CONTRAST_NORMAL)
        why = light_why if pick == light_words else "White."
    else:
        pick = _readable(text, BLACK, primary, MIN_CONTRAST_NORMAL)
        why = "The dark text colour." if pick == text else "Black."
    if contrast_ratio(pick, primary) < MIN_CONTRAST_NORMAL:
        # Only reachable for mid-tones where one side barely loses: take the plain
        # colour that wins outright. White or black always clears 4.5:1.
        pick = WHITE if contrast_ratio(WHITE, primary) >= contrast_ratio(BLACK, primary) else BLACK
        why = "White." if pick == WHITE else "Black."
    tokens["text_on_primary"] = Choice(
        pick,
        "logo" if pick == light_words != WHITE else "derived",
        f"{why} Chosen to read clearly on the main colour.",
    )

    on_text = _readable(light_words, WHITE, text, MIN_CONTRAST_NORMAL)
    tokens["text_on_dark"] = Choice(
        on_text,
        "logo" if on_text == light_words != WHITE else "derived",
        f"{light_why if on_text == light_words else 'White.'} For words on dark bands and "
        f"shaded photos.",
    )

    lightness = 0.58
    muted = from_lch(lightness, min(0.05, 0.6 * p_chroma), p_hue)
    while contrast_ratio(muted, surface) < MIN_CONTRAST_NORMAL and lightness > 0.0:
        lightness -= 0.01
        muted = from_lch(lightness, min(0.05, 0.6 * p_chroma), p_hue)
    tokens["muted"] = Choice(muted, "derived", "A soft shade of the main colour for small print.")

    palette = {**base.palette, **{token: choice.hex for token, choice in tokens.items()}}
    kit = BrandKit(
        client_id=base.client_id, version=base.version, palette=palette, fonts=base.fonts
    )
    checks = check_palette(palette)
    if reading.swatches:
        checks = checks + _logo_checks(reading.swatches[0], palette)
    for check in checks:
        if not check.ok and check.what.startswith("The logo"):
            notes.append(f"{check.sentence()} On designs that put the logo there, it won't show.")
    return Suggestion(
        kit=kit, tokens=tokens, checks=checks, notes=tuple(dict.fromkeys(notes)), reading=reading
    )


__all__ = [
    "REQUIRED",
    "STARTER_PALETTE",
    "Check",
    "Choice",
    "Suggestion",
    "check_palette",
    "is_starter",
    "missing_tokens",
    "starter_kit",
    "suggest",
]
