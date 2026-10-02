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
from dataclasses import dataclass, field

from stratum_spec import BrandKit, PosterSpec
from stratum_spec.colour import MIN_CONTRAST_LARGE, MIN_CONTRAST_NORMAL, blend, contrast_ratio
from stratum_spec.models import ImageLayer, LogoLayer, ShapeLayer, TextLayer
from stratum_spec.ops import Dim, EditOp, Scale, SetColor, SetVisible, apply_ops

from stratum_worker.brand.names import colour_name
from stratum_worker.design import FAMILIES, decode, design_id, everything, is_design
from stratum_worker.design.families import ACCENT, FILL
from stratum_worker.draft.backdrop import ASSUMED_BACKDROP, covers

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
    """The design's name and settings, for logs and decisions (not for people)."""
    design = decode(template_id)
    settings = ", ".join(f"{name} {value}" for name, value in design.choice.items())
    return f"{design.title} ({settings})"


# --- what changed, said to a person ---------------------------------------------------
#
# Live, 1 Oct: the note read "Switched to Two photos (share 0.58, gutter none, fill
# surface, align left, accent rule, logo text, scale bold)". A designer reads words.


def _colour(kit: BrandKit, token: str) -> str:
    value = kit.palette.get(token.removeprefix("palette."))
    return colour_name(value) if value else "brand"


def _setting(family: str, name: str, value: str, before: str | None, kit: BrandKit) -> str:
    if name == "side":
        return "the words below the photo" if value == "end" else "the words above the photo"
    if name == "share":
        if before is None:
            what = "photos" if family == "pair" else "a panel"
            return f"{what} taking {round(float(value) * 100)}% of the poster"
        size = "bigger" if float(value) > float(before) else "smaller"
        return f"{size} photos" if family == "pair" else f"a {size} panel"
    if name == "fill":
        return f"a {_colour(kit, FILL[value][0])} background behind the words"
    if name == "align":
        return "left-aligned words" if value == "left" else "centred words"
    if name == "accent":
        return f"a {_colour(kit, ACCENT)} accent line" if value == "rule" else "no accent line"
    if name == "logo":
        return {
            "photo": "the logo on the photo",
            "panel": "the logo with the words",
            "text": "the logo with the words",
            "start": "the logo on the left",
            "end": "the logo on the right",
            "top": "the logo at the top",
            "bottom": "the logo at the bottom",
        }.get(value, "the logo moved")
    if name == "scale":
        return "a bigger, bolder headline" if value == "bold" else "a smaller, calmer headline"
    if name == "zone":
        return {"top": "the words at the top", "bottom": "the words at the bottom"}.get(
            value, "the words in the middle"
        )
    if name == "treatment":
        return {
            "scrim": "the words on a dark fade",
            "band": f"the words on a {_colour(kit, 'palette.primary')} band",
            "card": f"the words on a {_colour(kit, 'palette.surface')} card",
        }[value]
    if name == "gutter":
        return "a thin line between the photos" if value == "thin" else "no line between the photos"
    return f"{name} {value}"


# Short labels for a control panel: one dropdown per setting (C7).
SETTING_LABEL: dict[str, str] = {
    "side": "Words",
    "share": "Panel size",
    "fill": "Background",
    "align": "Alignment",
    "accent": "Accent line",
    "logo": "Logo",
    "scale": "Headline",
    "zone": "Words position",
    "treatment": "Words sit on",
    "gutter": "Between photos",
}


def setting_label(family: str, name: str) -> str:
    if name == "share" and family == "pair":
        return "Photo height"
    return SETTING_LABEL.get(name, name.capitalize())


def option_label(family: str, name: str, value: str, kit: BrandKit) -> str:
    """One choice of one setting, as a dropdown shows it: "Cream", "Below the photo"."""
    if name == "side":
        return "Below the photo" if value == "end" else "Above the photo"
    if name == "share":
        return f"{round(float(value) * 100)}%"
    if name == "fill":
        return _colour(kit, FILL[value][0]).capitalize()
    if name == "align":
        return "Left" if value == "left" else "Centred"
    if name == "accent":
        return f"{_colour(kit, ACCENT).capitalize()} line" if value == "rule" else "None"
    if name == "logo":
        return {
            "photo": "On the photo",
            "panel": "With the words",
            "text": "With the words",
            "start": "Left",
            "end": "Right",
            "top": "Top",
            "bottom": "Bottom",
        }.get(value, value.capitalize())
    if name == "scale":
        return "Bold" if value == "bold" else "Calm"
    if name == "zone":
        return {"top": "Top", "bottom": "Bottom"}.get(value, "Middle")
    if name == "treatment":
        return {
            "scrim": "Dark fade",
            "band": f"{_colour(kit, 'palette.primary').capitalize()} band",
            "card": f"{_colour(kit, 'palette.surface').capitalize()} card",
        }[value]
    if name == "gutter":
        return "Thin line" if value == "thin" else "None"
    return value


def said(before_id: str, after_id: str, changes: Mapping[str, str], kit: BrandKit) -> str:
    """What a restyle changed, in a designer's words.

    The same design with other settings names what changed ("Changed the style: a
    smaller, calmer headline."). A new design is named, with the settings asked for.
    """
    before, after = decode(before_id), decode(after_id)
    family = after.family.name
    if before.family.name != family:
        asked = [
            _setting(family, name, after.choice[name], None, kit)
            for name in changes
            if name != FAMILY and name in after.choice
        ]
        tail = f", with {', '.join(asked)}" if asked else ""
        return f"Switched to the {after.family.title} design{tail}."
    changed = [
        _setting(family, name, value, before.choice.get(name), kit)
        for name, value in after.choice.items()
        if before.choice.get(name) != value
    ]
    return f"Changed the style: {', '.join(changed)}." if changed else "Changed the style."


# --- the person's earlier changes survive a restyle --------------------------------------
#
# Live, 1 Oct: "darken both photos", then "undo the calmer change" (a restyle back to
# bold) brought the photos back to full brightness and the warm grey text back to the
# design's own colour. A restyle lays the poster out again; what the person had
# changed on top of the design is carried onto the new layout.


@dataclass(frozen=True)
class Kept:
    spec: PosterSpec
    kept: list[str] = field(default_factory=list)  # what was carried, in words
    dropped: list[str] = field(default_factory=list)  # sentences: what was not, and why


def behind(spec: PosterSpec, layer_id: str) -> ShapeLayer | None:
    """The visible shape a layer sits on: the last one before it that covers it."""
    index = next(i for i, one in enumerate(spec.layers) if one.id == layer_id)
    target = spec.layers[index]
    for layer in reversed(spec.layers[:index]):
        if isinstance(layer, ShapeLayer) and layer.visible and covers(layer.box, target.box):
            return layer
    return None


def _readable(spec: PosterSpec, layer_id: str, token: str, kit: BrandKit) -> bool:
    backing = behind(spec, layer_id)
    if backing is None:
        return False  # straight on a photo: the design's own colour is the safe one
    shown = blend(kit.color(backing.fill), ASSUMED_BACKDROP, backing.opacity)
    floor = MIN_CONTRAST_LARGE if layer_id == "headline" else MIN_CONTRAST_NORMAL
    return contrast_ratio(kit.color(token), shown) >= floor


def _name(layer_id: str) -> str:
    return {"offer": "offer line", "cta": "call to action"}.get(layer_id, layer_id)


def carry_over(
    before: PosterSpec,
    pristine: PosterSpec | None,
    after: PosterSpec,
    kit: BrandKit,
    changes: Mapping[str, str],
) -> Kept:
    """`after` with the person's own changes to `before` made again.

    `pristine` is the old design laid out fresh around the same words and photos:
    whatever differs between it and `before` is what the person changed. Carried:
    text colours that are still readable on the new background, shape colours unless
    the restyle itself changes the background, how dark each photo is, what was
    hidden, and the logo's size.
    """
    if pristine is None:
        return Kept(after)
    fresh = {layer.id: layer for layer in pristine.layers}
    new = {layer.id: layer for layer in after.layers}
    restyles_colour = bool({FAMILY, "fill", "treatment"} & set(changes))
    ops: list[EditOp] = []
    kept: list[str] = []
    dropped: list[str] = []
    darker = {
        layer.asset: layer.dim
        for layer in before.layers
        if isinstance(layer, ImageLayer)
        and isinstance(fresh.get(layer.id), ImageLayer)
        and layer.dim != getattr(fresh[layer.id], "dim", 0.0)
    }
    for layer in after.layers:
        if isinstance(layer, ImageLayer) and layer.asset in darker:
            ops.append(Dim(layer=layer.id, amount=darker[layer.asset]))
    if darker:
        kept.append("the darker photo" if len(darker) == 1 else "the darker photos")
    for layer in before.layers:
        was, now = fresh.get(layer.id), new.get(layer.id)
        if was is None or now is None or type(was) is not type(layer):
            continue
        if isinstance(layer, TextLayer) and isinstance(was, TextLayer):
            if layer.color != was.color and isinstance(now, TextLayer):
                if _readable(after, layer.id, layer.color, kit):
                    ops.append(SetColor(layer=layer.id, color=layer.color))
                    kept.append(f"the {_colour(kit, layer.color)} {_name(layer.id)}")
                else:
                    dropped.append(
                        f"Your {_colour(kit, layer.color)} {_name(layer.id)} would not be "
                        f"readable on the new background, so it has the design's colour."
                    )
        elif (
            isinstance(layer, ShapeLayer)
            and isinstance(was, ShapeLayer)
            and isinstance(now, ShapeLayer)
            and layer.fill != was.fill
            and not restyles_colour
        ):
            ops.append(SetColor(layer=layer.id, color=layer.fill))
            kept.append(f"the {_colour(kit, layer.fill)} {_name(layer.id)}")
        if not layer.visible and was.visible and now.visible:
            ops.append(SetVisible(layer=layer.id, visible=False))
            kept.append(f"the hidden {_name(layer.id)}")
        if isinstance(layer, LogoLayer) and isinstance(now, LogoLayer) and was.box.w > 0:
            ratio = layer.box.w / was.box.w
            if abs(ratio - 1) > 0.02:
                ops.append(Scale(layer=layer.id, factor=ratio))
                kept.append("the bigger logo" if ratio > 1 else "the smaller logo")
    return Kept(apply_ops(after, ops) if ops else after, kept, dropped)


__all__ = [
    "FAMILY",
    "SETTING_LABEL",
    "Kept",
    "RestyleError",
    "behind",
    "carry_over",
    "described",
    "option_label",
    "said",
    "setting_label",
    "targets",
]
