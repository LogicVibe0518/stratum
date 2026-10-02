"""What the model reading a tweak is told about the poster, in a designer's words.

Before (edit.parse v3) it saw layer ids, a character count and token names:
"headline: text, fits 40 characters", "palette.secondary". It could not know that
the headline is white and at the bottom, that palette.secondary is gold, that the
brand has no green, or that this design can put its words on the left. So it
guessed, or refused in jargon.

Now it sees, for each layer, where it is and what colour it is in words; each brand
colour by name and what uses it; which brand colours are nearest to any colour the
request names; this design's style settings with what each one does; and the last
few changes, so "bigger still" and "undo that" mean something.

Pure: no database, no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stratum_spec import BrandKit, PosterSpec
from stratum_spec.models import Box, ImageLayer, Layer, LogoLayer, ShapeLayer, TextLayer

from stratum_worker.brand.names import colour_name, nearest, words_in
from stratum_worker.design import decode, is_design
from stratum_worker.design.families import FAMILIES, FILL
from stratum_worker.draft.bindings import binding_for

# What a designer calls each layer the design space makes. A hand-made template's
# own layer ids are shown with their underscores as spaces.
FRIENDLY: dict[str, str] = {
    "headline": "headline",
    "offer": "offer line",
    "cta": "call to action",
    "logo": "logo",
    "logo_chip": "panel behind the logo",
    "panel": "colour panel",
    "backing": "band behind the words",
    "band": "band behind the headline",
    "base": "background colour",
    "rule": "accent line",
    "bg": "photo",
    "photo_a": "first photo",
    "photo_b": "second photo",
}


def friendly(layer_id: str) -> str:
    return FRIENDLY.get(layer_id, layer_id.replace("_", " "))


def where(box: Box) -> str:
    """Where a box sits on the poster, in words: "bottom left", "the top 42%"."""
    left, top = max(box.x, 0.0), max(box.y, 0.0)
    right, bottom = min(box.x + box.w, 1.0), min(box.y + box.h, 1.0)
    width, height = right - left, bottom - top
    if width >= 0.95 and height >= 0.95:
        return "the whole poster"
    if width >= 0.95:
        share = round(height * 100)
        if top <= 0.02:
            return f"the top {share}%"
        if bottom >= 0.98:
            return f"the bottom {share}%"
        return f"a full-width band, {share}% high, across the middle"
    if height >= 0.95:
        share = round(width * 100)
        if left <= 0.02:
            return f"the left {share}%"
        if right >= 0.98:
            return f"the right {share}%"
        return f"a full-height strip, {share}% wide, down the middle"
    centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
    row = "top" if centre_y < 0.36 else "bottom" if centre_y > 0.64 else "middle"
    column = "left" if centre_x < 0.4 else "right" if centre_x > 0.6 else "centre"
    if row == "middle" and column == "centre":
        return "the centre"
    if row == "middle":
        return f"middle {column}"
    return f"{row} {column}"


def _token_name(token: str, kit: BrandKit) -> str:
    name = token.removeprefix("palette.")
    value = kit.palette.get(name)
    return f"{colour_name(value)} ({token})" if value else token


def _photo_room(layer: ImageLayer, canvas: tuple[int, int], size: tuple[int, int] | None) -> str:
    """Which way a photo can be moved inside its frame, if any."""
    if layer.fit != "cover" or size is None:
        return ""
    crop = layer.crop
    zoom = crop.zoom if crop else 1.0
    box_ratio = (layer.box.w * canvas[0]) / max(layer.box.h * canvas[1], 1e-6)
    photo_ratio = size[0] / max(size[1], 1)
    if zoom > 1.01:
        room = f"zoomed {zoom:.2f}x, can move any way"
    elif photo_ratio > box_ratio * 1.02:
        room = "can move left or right; zoom in first to move it up or down"
    elif photo_ratio < box_ratio / 1.02:
        room = "can move up or down; zoom in first to move it left or right"
    else:
        room = "fits exactly; zoom in first to move it"
    return f", {room}"


def layer_line(
    layer: Layer,
    kit: BrandKit,
    canvas: tuple[int, int],
    *,
    fits: int | None = None,
    photo_size: tuple[int, int] | None = None,
) -> str:
    """One layer for the model: what it is, where, what colour, what it says."""
    parts = [friendly(layer.id), where(layer.box)]
    if isinstance(layer, TextLayer):
        parts[0] = f"text ({parts[0]})"
        parts.append(_token_name(layer.color, kit))
        if fits is not None:
            parts.append(f"fits {fits} characters")
    elif isinstance(layer, ShapeLayer):
        parts[0] = f"shape ({parts[0]})"
        parts.append(_token_name(layer.fill, kit))
    elif isinstance(layer, ImageLayer):
        parts[0] = "photo" if parts[0] == "photo" else f"photo ({parts[0]})"
        if layer.dim > 0:
            parts.append(f"darkened {layer.dim:.2f}")
    elif isinstance(layer, LogoLayer):
        parts[0] = "the client's logo"
    if not layer.visible:
        parts.append("hidden")
    line = ", ".join(parts)
    if isinstance(layer, ImageLayer):
        line += _photo_room(layer, canvas, photo_size)
    content = getattr(layer, "content", "")
    return f"{line}: {content}" if content else line


def describe_layers(
    spec: PosterSpec,
    kit: BrandKit,
    fits: Mapping[str, int],
    photo_sizes: Mapping[str, tuple[int, int]],
) -> dict[str, str]:
    canvas = spec.canvas.size
    out = {}
    for layer in spec.layers:
        asset = getattr(layer, "asset", None)
        out[layer.id] = layer_line(
            layer,
            kit,
            canvas,
            fits=fits.get(layer.id),
            photo_size=photo_sizes.get(asset) if isinstance(asset, str) else None,
        )[:240]
    return out


def colour_lines(spec: PosterSpec, kit: BrandKit) -> dict[str, str]:
    """Each brand colour by name, and which shown layers are that colour now."""
    users: dict[str, list[str]] = {}
    for layer in spec.layers:
        if not layer.visible:
            continue
        token = (
            layer.color
            if isinstance(layer, TextLayer)
            else layer.fill
            if isinstance(layer, ShapeLayer)
            else None
        )
        if token:
            users.setdefault(token, []).append(friendly(layer.id))
    out = {}
    for name, value in kit.palette.items():
        token = f"palette.{name}"
        used = users.get(token)
        tail = f"; used by the {', '.join(used)}" if used else ""
        out[token] = f"{colour_name(value)} {value}{tail}"
    return out


def asked_colours(message: str, kit: BrandKit) -> list[str]:
    """For each colour word in the request: is it a brand colour, and what is nearest."""
    lines = []
    for word in words_in(message)[:6]:
        found = nearest(word, kit.palette)
        closest = ", ".join(
            f"palette.{token} ({colour_name(kit.palette[token])})" for token in found.nearest
        )
        if found.in_brand is not None:
            lines.append(f"{word}: the brand's {word} is palette.{found.in_brand}")
        else:
            lines.append(f"{word}: NOT a brand colour; the nearest brand colours are {closest}")
    return lines


# What each style setting does, said once for every family that has it. {primary},
# {surface}, {dark} and {accent} become this brand's own colour names.
SETTING_HELP: dict[str, str] = {
    "side": "where the words panel is: end = below the photo (right of it in wide "
    "sizes), start = above it (left in wide sizes)",
    "share": "how much of the poster the panel or the photos take",
    "fill": "the colour behind the words: primary = {primary}, surface = {surface}, dark = {dark}",
    "align": "words left-aligned or centred",
    "accent": "a short {accent} line by the headline (rule) or none",
    "logo": "where the logo sits",
    "scale": "bold = a big headline, calm = a smaller headline with more space",
    "zone": "where the words sit on the poster",
    "treatment": "what the words sit on over the photo: scrim = a dark fade "
    "({dark_name}), band = a solid {primary} band, card = a {surface} card",
    "gutter": "a thin line between the two photos, or none",
}


def _help(name: str, kit: BrandKit) -> str:
    names = {
        "primary": colour_name(kit.palette.get("primary", "#000000")),
        "surface": colour_name(kit.palette.get("surface", "#ffffff")),
        "dark": colour_name(kit.palette.get(FILL["dark"][0].removeprefix("palette."), "#000000")),
        "dark_name": colour_name(kit.palette.get("text", "#000000")),
        "accent": colour_name(kit.palette.get("secondary", "#000000")),
    }
    return SETTING_HELP.get(name, name).format(**names)


def style_menu(template_id: str, kit: BrandKit, photos: int) -> str:
    """This design's style settings and the other designs, for a restyle op.

    `photos` is how many of the client's own photos could be used: a design with two
    photo spaces is only offered when there are two.
    """
    if not is_design(template_id):
        return (
            "This poster was made from a fixed template, so it has no style settings "
            "and restyle is not available."
        )
    design = decode(template_id)
    lines = [f'This poster is the "{design.title}" design. Its style settings:']
    for name, options in design.family.params:
        current = design.choice[name]
        others = " | ".join(option for option in options if option != current)
        lines.append(f"- {name}: now {current}; can be {others}. {_help(name, kit)}")
    lines.append("Other designs (restyle with family):")
    for family in FAMILIES.values():
        if family.name == design.family.name:
            continue
        need = list(binding_for(f"ds.{family.name}.1.{'0' * len(family.params)}").photos.values())
        wanted = need.count("photo")
        if wanted > photos:
            lines.append(
                f"- {family.name}: {family.title}. NOT available: needs {wanted} of the "
                f"client's photos and there are {photos}."
            )
        else:
            lines.append(f"- {family.name}: {family.title}")
    return "\n".join(lines)


def recent_lines(edits: Sequence[Mapping[str, object]]) -> list[str]:
    """The last few changes, newest last: "make the logo bigger -> applied"."""
    return [f"{row.get('message', '')!s:.160} -> {row.get('outcome', '')}" for row in edits][-5:]


__all__ = [
    "FRIENDLY",
    "asked_colours",
    "colour_lines",
    "describe_layers",
    "friendly",
    "layer_line",
    "recent_lines",
    "style_menu",
    "where",
]
