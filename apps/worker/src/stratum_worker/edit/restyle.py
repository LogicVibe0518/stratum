"""Restyle: "put the words on the left", "cream panel", "make it words only".

A design-space poster is one point in a small grid of honest choices (M11a): which
side the words go, which brand colour carries them, bold or calm. Asking for a
different choice is not a pile of move and colour ops - which drift, overlap, and
break the colour pairs - it is the neighbouring design, laid out again around the
same words and photos. This module works out WHICH design; the service lays it out.

The asked-for settings are kept exactly. Everything else stays as it is where the
new design has the same setting, so "cream panel" changes the panel and nothing
else. If the words do not fit the exact design, the next candidates keep every
asked-for setting and as many of the others as they can.

Pure: no database, no network.
"""

from __future__ import annotations

from collections.abc import Mapping

from stratum_worker.design import FAMILIES, decode, design_id, everything, is_design

FAMILY = "family"


class RestyleError(ValueError):
    """A restyle that cannot be done, said in a sentence for the designer."""


def _check(changes: Mapping[str, str], family_name: str) -> None:
    family = FAMILIES[family_name]
    params = dict(family.params)
    for name, value in changes.items():
        if name == FAMILY:
            continue
        if name not in params:
            have = ", ".join(params)
            raise RestyleError(
                f'The "{family.title}" design has no {name} setting. It has: {have}.'
            )
        if value not in params[name]:
            options = ", ".join(params[name])
            raise RestyleError(f"{name} can be {options}; not {value}.")


def targets(template_id: str, changes: Mapping[str, str], limit: int = 24) -> list[str]:
    """Design ids to try, best first: every asked-for setting, then the most kept.

    Raises RestyleError if the poster is not a design-space design, or a setting or
    value does not exist.
    """
    if not is_design(template_id):
        raise RestyleError(
            "This poster was made from a fixed template, so its style cannot be switched. "
            "Make it again with Best three to choose between styles."
        )
    if not changes:
        raise RestyleError("Say which style setting to change.")
    current = decode(template_id)
    family_name = changes.get(FAMILY, current.family.name)
    if family_name not in FAMILIES:
        names = ", ".join(FAMILIES)
        raise RestyleError(f"There is no {family_name} design. The designs are: {names}.")
    _check(changes, family_name)
    asked = {name: value for name, value in changes.items() if name != FAMILY}
    keep = {
        name: value
        for name, value in current.choice.items()
        if name not in asked and name in dict(FAMILIES[family_name].params)
    }
    ranked = []
    for design in everything((family_name,)):
        if any(design.choice[name] != value for name, value in asked.items()):
            continue
        kept = sum(design.choice[name] == value for name, value in keep.items())
        ranked.append((-kept, design.id))
    ranked.sort()
    here = design_id(current.family, current.choice)
    out = [one for _, one in ranked if one != here]
    if len(out) < len(ranked) and ranked[0][1] == here:
        raise RestyleError("The poster already has that style.")
    return out[:limit]


def described(template_id: str) -> str:
    """The design's name and settings, for a version note."""
    design = decode(template_id)
    settings = ", ".join(f"{name} {value}" for name, value in design.choice.items())
    return f"{design.title} ({settings})"


__all__ = ["FAMILY", "RestyleError", "described", "targets"]
