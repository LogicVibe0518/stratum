"""Restyle (M10c): "words on the left", "cream panel", "make it words only".

Which neighbouring design a style change means. Every asked-for setting is kept
exactly; every other setting stays as it was where the new design has it.
"""

from __future__ import annotations

import pytest
from stratum_worker.design import decode
from stratum_worker.edit.restyle import RestyleError, described, targets

PANEL = "ds.panel.1.0010110"  # side end, share 0.42, surface, left, none, panel, bold


def test_one_setting_changes_and_the_rest_stay() -> None:
    first = decode(targets(PANEL, {"fill": "dark"})[0])
    before = decode(PANEL)
    assert first.family.name == "panel"
    assert first.choice == {**before.choice, "fill": "dark"}


def test_every_candidate_keeps_what_was_asked() -> None:
    for one in targets(PANEL, {"side": "start", "scale": "calm"}):
        choice = decode(one).choice
        assert choice["side"] == "start" and choice["scale"] == "calm"


def test_the_candidates_keep_as_much_as_they_can_first() -> None:
    before = decode(PANEL).choice
    kept = [
        sum(decode(one).choice[name] == value for name, value in before.items() if name != "fill")
        for one in targets(PANEL, {"fill": "primary"})
    ]
    assert kept == sorted(kept, reverse=True)


def test_switching_family_carries_the_settings_both_have() -> None:
    first = decode(targets(PANEL, {"family": "type"})[0])
    assert first.family.name == "type"
    assert first.choice["fill"] == "surface"
    assert first.choice["align"] == "left"
    assert first.choice["scale"] == "bold"


def test_switching_family_with_a_setting_of_that_family() -> None:
    first = decode(targets(PANEL, {"family": "overlay", "treatment": "card"})[0])
    assert first.family.name == "overlay" and first.choice["treatment"] == "card"


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"colour": "gold"}, "has no colour setting"),
        ({"fill": "gold"}, "fill can be primary, surface, dark; not gold"),
        ({"family": "collage"}, "There is no collage design"),
        ({"fill": "surface"}, "already has that style"),
        ({}, "Say which style setting"),
    ],
)
def test_impossible_restyles_are_a_sentence(changes: dict[str, str], said: str) -> None:
    with pytest.raises(RestyleError, match=said):
        targets(PANEL, changes)


def test_a_fixed_template_has_no_styles_to_switch() -> None:
    with pytest.raises(RestyleError, match="fixed template"):
        targets("full_bleed", {"fill": "surface"})


def test_a_preset_name_restyles_like_its_design() -> None:
    assert decode(targets("two_photos", {"fill": "dark"})[0]).family.name == "pair"


def test_a_design_is_described_for_its_version_note() -> None:
    assert described(PANEL).startswith("Photo and panel (side end, share 0.42, fill surface")
