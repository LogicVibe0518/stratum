"""The design space: every candidate design, by id, and how to lay one out.

A design id names a family, the generation of its code, and one choice per
parameter: "ds.pair.1.0100100" is the Two photos family, generation 1, with each
digit an index into that parameter's options. The id is all a post needs to store;
the words decide the rest, and the laid-out result (all four sizes) is kept in the
poster itself (spec schema 3), so a later change to this code never moves a poster
that already exists.

Friendly names ("two_photos") are presets: a fixed id the design picker can offer
before the critic picks designs by itself.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from stratum_spec.brand import BrandKit
from stratum_spec.models import Aspect, LayoutOverride, PosterSpec

from stratum_worker.design.families import FAMILIES, Choice, Family, Layers
from stratum_worker.design.frame import Frame
from stratum_worker.design.typeset import PLACEHOLDER, Copy

GENERATION = 1
BASE: Aspect = "4:5"
SIZES: tuple[Aspect, ...] = ("4:5", "1:1", "9:16", "16:9")
_ID = re.compile(r"^ds\.([a-z]+)\.(\d+)\.(\d+)$")

PRESETS: dict[str, str] = {
    # Two of the client's own photos side by side, then headline, offer and contact:
    # the design the Krishna test asked for (both photos AND the offer on one poster).
    # The logo sits on the first photo, which leaves the words the whole panel.
    "two_photos": "ds.pair.1.0000010",
}


class UnknownDesign(KeyError):
    """Not a design id this code can lay out."""


@dataclass(frozen=True)
class Design:
    family: Family
    choice: Choice
    id: str

    @property
    def title(self) -> str:
        return self.family.title


def design_id(family: Family, choice: Choice) -> str:
    digits = "".join(str(options.index(choice[name])) for name, options in family.params)
    return f"ds.{family.name}.{GENERATION}.{digits}"


def is_design(template_id: str) -> bool:
    return template_id in PRESETS or bool(_ID.match(template_id))


def decode(template_id: str) -> Design:
    resolved = PRESETS.get(template_id, template_id)
    match = _ID.match(resolved)
    if match is None or match.group(1) not in FAMILIES:
        raise UnknownDesign(template_id)
    family = FAMILIES[match.group(1)]
    if int(match.group(2)) != GENERATION:
        raise UnknownDesign(f"{template_id}: generation {match.group(2)} is not {GENERATION}")
    digits = match.group(3)
    if len(digits) != len(family.params):
        raise UnknownDesign(f"{template_id}: {len(family.params)} choices expected")
    try:
        choice = {
            name: options[int(d)] for (name, options), d in zip(family.params, digits, strict=True)
        }
    except IndexError as exc:
        raise UnknownDesign(template_id) from exc
    return Design(family, choice, template_id)


def everything(families: tuple[str, ...] | None = None) -> Iterator[Design]:
    """Every combination of every family's choices, in a stable order."""
    for name in families or tuple(FAMILIES):
        family = FAMILIES[name]
        names = [param for param, _ in family.params]
        for values in itertools.product(*(options for _, options in family.params)):
            choice = dict(zip(names, values, strict=True))
            yield Design(family, choice, design_id(family, choice))


def _build(
    design: Design, aspect: Aspect, copy: Copy, kit: BrandKit, root: Path, logo_aspect: float
) -> Layers | None:
    return design.family.build(Frame.of(aspect), design.choice, copy, kit, root, logo_aspect)


def lay_out(
    template_id: str,
    kit: BrandKit,
    root: Path,
    *,
    copy: Copy = PLACEHOLDER,
    logo_aspect: float = 0.0,
    sizes: tuple[Aspect, ...] = SIZES,
) -> PosterSpec | None:
    """A template-like spec for this design and these words, carrying every size.

    None when the words do not fit this design in one of the sizes: the candidate
    does not exist for this copy, which is better than a poster with cut-off words.
    The text layers hold placeholders; build_spec binds the real words and assets.
    """
    design = decode(template_id)
    base = _build(design, BASE, copy, kit, root, logo_aspect)
    if base is None:
        return None
    variants: dict[str, dict[str, dict[str, object]]] = {}
    for aspect in sizes:
        if aspect == BASE:
            continue
        other = _build(design, aspect, copy, kit, root, logo_aspect)
        if other is None:
            return None
        variants[aspect] = _overrides(base, other)
    return PosterSpec.model_validate(
        {
            "template_id": template_id,
            "template_version": GENERATION,
            "canvas": {"aspect": BASE},
            "layers": base,
            "variants": variants,
        }
    )


def _overrides(base: Layers, other: Layers) -> dict[str, dict[str, object]]:
    """What one size lays out differently. Anything else differing is a bug in a
    family: a size may never change what a layer says, shows or is coloured."""
    if [layer["id"] for layer in base] != [layer["id"] for layer in other]:
        raise AssertionError("a design must have the same layers in every size")
    changes: dict[str, dict[str, object]] = {}
    for one, two in zip(base, other, strict=True):
        diff = {key: two[key] for key in two if one.get(key) != two[key]}
        wrong = set(diff) - set(LayoutOverride.model_fields)
        if wrong:
            raise AssertionError(f"{one['id']}: sizes differ in {sorted(wrong)}")
        if diff:
            changes[str(one["id"])] = diff
    return changes


def binding_parts(template_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """(texts, photos) for build_spec: which copy goes in which layer, which photos."""
    design = decode(template_id)
    texts = {"headline": "headline", "offer": "offer_detail", "cta": "cta"}
    return texts, dict(design.family.photos)


__all__ = [
    "BASE",
    "GENERATION",
    "PRESETS",
    "SIZES",
    "Design",
    "UnknownDesign",
    "binding_parts",
    "decode",
    "design_id",
    "everything",
    "is_design",
    "lay_out",
]
