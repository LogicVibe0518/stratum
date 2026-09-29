"""What is behind something on a poster, as a colour.

Both readability checks need it: text against the band it sits on, and the logo
against whatever it sits on. Layers are drawn in list order, so the answer is found
by walking down from the layer in question until something covers it completely.
"""

from __future__ import annotations

from stratum_spec.brand import BrandKit
from stratum_spec.colour import blend
from stratum_spec.models import Box, CutoutLayer, ImageLayer, PosterSpec, ShapeLayer

# What we assume is behind a semi-transparent band when we cannot know. Mid-grey is
# the honest choice: it is the worst case for BOTH light and dark text, so a poster
# that passes this check passes over any photograph.
ASSUMED_BACKDROP = "#808080"


def covers(outer: Box, inner: Box) -> bool:
    return (
        outer.x <= inner.x
        and outer.y <= inner.y
        and outer.x + outer.w >= inner.x + inner.w
        and outer.y + outer.h >= inner.y + inner.h
    )


def behind(spec: PosterSpec, index: int, box: Box, kit: BrandKit) -> str:
    """The colour under `box`, from the layers below `index`.

    A solid shape answers exactly. A see-through one is blended with whatever is
    under IT. A photo, or nothing at all, is assumed to be mid-grey: the worst case,
    so anything readable here is readable over any picture.
    """
    for below in range(index - 1, -1, -1):
        layer = spec.layers[below]
        if not layer.visible or not covers(layer.box, box):
            continue
        if isinstance(layer, ShapeLayer):
            colour = kit.color(layer.fill)
            if layer.opacity >= 0.999:
                return colour
            return blend(colour, behind(spec, below, box, kit), layer.opacity)
        if isinstance(layer, ImageLayer | CutoutLayer):
            return ASSUMED_BACKDROP
    return ASSUMED_BACKDROP


__all__ = ["ASSUMED_BACKDROP", "behind", "covers"]
