"""The page a design is laid out on: size, margins and where text may go, per format.

Every family in the design space works in pixels on one of these and hands back
fractions, which is what the layer spec stores. Keeping the numbers here means a
margin or a safe zone is decided once, for every family and every size.
"""

from __future__ import annotations

from dataclasses import dataclass

from stratum_spec.models import Aspect, Box, Canvas
from stratum_spec.templates import base_width_for

# Instagram draws the profile row over the top of a story and the reply bar over the
# bottom. Text and the logo stay between these lines - the very numbers preflight
# checks against, so a generated design can never trip its own check.
from stratum_worker.draft.preflight import STORY_SAFE_BOTTOM, STORY_SAFE_TOP

# The space kept clear at every edge, as a share of the shorter side: 80 px on a
# 1080 px post, the margin most agency templates use.
MARGIN = 0.074


@dataclass(frozen=True)
class Rect:
    """A rectangle in canvas pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def inset(self, left: float, top: float, right: float, bottom: float) -> Rect:
        return Rect(self.x + left, self.y + top, self.w - left - right, self.h - top - bottom)


@dataclass(frozen=True)
class Frame:
    aspect: Aspect
    width: float
    height: float

    @classmethod
    def of(cls, aspect: Aspect, base_width: int = 1080) -> Frame:
        width, height = Canvas(aspect=aspect, base_width=base_width_for(base_width, aspect)).size
        return cls(aspect, float(width), float(height))

    @property
    def short(self) -> float:
        return min(self.width, self.height)

    @property
    def landscape(self) -> bool:
        return self.width > self.height

    @property
    def margin(self) -> float:
        return MARGIN * self.short

    @property
    def page(self) -> Rect:
        return Rect(0.0, 0.0, self.width, self.height)

    @property
    def safe(self) -> Rect:
        """Where text and the logo may sit: inside the margins, and inside a story's
        safe band."""
        top, bottom = self.margin, self.height - self.margin
        if self.aspect == "9:16":
            # A pixel inside the line, so rounding to the spec's fractions can never
            # land a box on the wrong side of it.
            top = max(top, STORY_SAFE_TOP * self.height + 1)
            bottom = min(bottom, STORY_SAFE_BOTTOM * self.height - 1)
        return Rect(self.margin, top, self.width - 2 * self.margin, bottom - top)

    def box(self, rect: Rect) -> Box:
        """Pixels to the spec's fractions of the canvas."""
        return Box(
            x=round(rect.x / self.width, 5),
            y=round(rect.y / self.height, 5),
            w=round(rect.w / self.width, 5),
            h=round(rect.h / self.height, 5),
        )

    def size(self, px: float) -> float:
        """A type size in pixels, as the spec stores it: a fraction of canvas height."""
        return round(px / self.height, 5)
