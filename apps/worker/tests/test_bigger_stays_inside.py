"""I-37: "bigger" keeps words and logos on the poster, in every size of every design.

The feature walk showed a 16:9 headline cut at the left after "bigger": scaling about
the centre pushed a full-width text box past both edges. What a reviewer should be
able to repeat back:
- on every chat-eval poster, a headline, offer, call to action or logo made 30%
  bigger stays inside the poster in all four sizes;
- words still get more room (a taller box); a logo keeps its shape.
"""

from __future__ import annotations

import pytest
from stratum_spec import LogoLayer, apply_ops, retarget
from stratum_spec.ops import Scale
from stratum_worker.design.space import SIZES
from stratum_worker.edit.chateval import POSTERS, poster


@pytest.mark.parametrize("name", sorted(POSTERS))
@pytest.mark.parametrize("part", ["headline", "offer", "cta", "logo"])
def test_bigger_stays_on_the_poster_in_every_size(name: str, part: str) -> None:
    before = poster(name).spec
    after = apply_ops(before, [Scale(layer=part, factor=1.3)])
    for aspect in SIZES:
        old, new = retarget(before, aspect).layer(part).box, retarget(after, aspect).layer(part).box
        assert new.x >= -1e-9 and new.y >= -1e-9, (aspect, new)
        assert new.x + new.w <= 1 + 1e-9 and new.y + new.h <= 1 + 1e-9, (aspect, new)
        assert new.h > old.h, (aspect, "no more room")
        if isinstance(before.layer(part), LogoLayer):
            assert new.w / new.h == pytest.approx(old.w / old.h), aspect
