"""build_spec: put the model's words into the template's boxes.

This is the only path from text a model produced into something renderable, and it
is the sentence the whole product rests on: a model never designs a poster, it only
returns words, and this function decides where words go.

Nothing here touches the network, the AI gateway, the filesystem or the clock. Given
the same four arguments it returns the same spec every time, which is what makes the
rest of the pipeline testable.

Two rules it lives by:

*Steps degrade, validators refuse.* This is a validator. It has no fallbacks - a
missing brand colour must never quietly become black, or off-brand posters ship to a
client who never finds out why. Graceful degradation belongs in the pipeline steps
above: no usable photo becomes a generated background, copy too long for its box gets
shrunk then truncated then flagged. By the time anything reaches here, those choices
are already made.

*Every problem at once.* A designer who mistyped two layer ids should see both, in
their language, in one run - not one, then the next, then a third.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import get_type_hints

from pydantic import BaseModel, ValidationError
from stratum_spec.brand import BrandKit, UnknownToken
from stratum_spec.models import (
    Aspect,
    AssetRef,
    ColorToken,
    CutoutLayer,
    FontToken,
    ImageLayer,
    Layer,
    LogoLayer,
    PosterSpec,
    TextLayer,
)

# Layers that carry the client's actual pixels. Every one must end up with a file -
# including hidden ones: a tweak can unhide a layer, and a spec that breaks the
# moment someone unhides something is worse than one that refuses to be built.
ASSET_LAYERS = (ImageLayer, CutoutLayer, LogoLayer)


class Kind(StrEnum):
    # S105 suppressed: a brand token (palette.primary) is not a secret.
    UNKNOWN_TOKEN = "unknown_token"  # noqa: S105
    UNEXPECTED_LAYER = "unexpected_layer"
    MISSING_COPY = "missing_copy"
    MISSING_ASSET = "missing_asset"
    INVALID_TEXT = "invalid_text"
    INVALID_ASSET = "invalid_asset"


@dataclass(frozen=True)
class Problem:
    """One thing wrong, described the way a designer would say it.

    `message` never names a Python field or an exception type. It is what the review
    portal shows and what the voice co-pilot reads out, so it has to make sense to
    someone who has never opened this repo.
    """

    kind: Kind
    message: str
    layer_id: str | None = None


class ComposeError(ValueError):
    """Composing a spec failed. `problems` holds every reason, not just the first."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems: tuple[Problem, ...] = tuple(problems)
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return " ".join(problem.message for problem in self.problems)

    # Defined here so it wins over KeyError.__str__ in MissingToken's MRO, which
    # would otherwise wrap the whole message in quotes.
    def __str__(self) -> str:
        return self.message


class UnexpectedLayer(ComposeError):
    """`texts` or `assets` named a layer the template does not have, or has of a
    different kind. Loud on purpose: a silently ignored key ships a placeholder."""


class MissingCopy(ComposeError):
    """A text layer in the template was given no text (or an empty string)."""


class MissingAsset(ComposeError):
    """An image, cutout or logo layer ended up with no file bound."""


class InvalidText(ComposeError):
    """Text that cannot go in that layer - almost always too long."""


class InvalidAsset(ComposeError):
    """Something that is not an asset id was passed as one."""


class MissingToken(ComposeError, UnknownToken):
    """The brand kit has no such colour or font.

    Both a compose failure and a failed token lookup, so `except ComposeError` and
    `except UnknownToken` each catch it.
    """


# Highest first. When a build fails several ways at once, the raised class is the
# most specific problem present; the message always lists all of them.
_PRECEDENCE: tuple[Kind, ...] = (
    Kind.UNKNOWN_TOKEN,
    Kind.UNEXPECTED_LAYER,
    Kind.MISSING_COPY,
    Kind.INVALID_TEXT,
    Kind.MISSING_ASSET,
    Kind.INVALID_ASSET,
)

_CLASSES: dict[Kind, type[ComposeError]] = {
    Kind.UNKNOWN_TOKEN: MissingToken,
    Kind.UNEXPECTED_LAYER: UnexpectedLayer,
    Kind.MISSING_COPY: MissingCopy,
    Kind.INVALID_TEXT: InvalidText,
    Kind.MISSING_ASSET: MissingAsset,
    Kind.INVALID_ASSET: InvalidAsset,
}


def _fail(problems: Sequence[Problem]) -> None:
    kinds = {problem.kind for problem in problems}
    for kind in _PRECEDENCE:
        if kind in kinds:
            raise _CLASSES[kind](problems)
    raise ComposeError(problems)  # pragma: no cover - _PRECEDENCE covers every Kind


@cache
def _token_fields(cls: type[BaseModel]) -> tuple[tuple[str, str], ...]:
    """Which fields on this layer type are brand tokens, and what kind.

    Found by comparing each field's annotation against the ColorToken / FontToken
    aliases themselves, so a new layer type with a colour is covered the day it is
    written, with no list here to forget to update. Comparing annotations rather
    than scanning strings also means a TextLayer whose *content* happens to read
    "palette.primary" is not mistaken for a token.
    """
    hints = get_type_hints(cls, include_extras=True)
    found: list[tuple[str, str]] = []
    for name in cls.model_fields:
        hint = hints.get(name)
        if hint == ColorToken:
            found.append((name, "colour"))
        elif hint == FontToken:
            found.append((name, "font"))
    return tuple(found)


def _text_problem(layer_id: str, content: str, exc: ValidationError) -> Problem:
    """Turn a Pydantic complaint into a sentence a designer can act on."""
    for error in exc.errors():
        limit = (error.get("ctx") or {}).get("max_length")
        if isinstance(limit, int):
            return Problem(
                Kind.INVALID_TEXT,
                f"The text for '{layer_id}' is too long: {len(content)} characters, "
                f"and this design fits at most {limit}.",
                layer_id,
            )
    return Problem(
        Kind.INVALID_TEXT, f"The text for '{layer_id}' cannot be used as it is.", layer_id
    )


def build_spec(
    template: PosterSpec,
    brand_kit: BrandKit,
    texts: Mapping[str, str],
    assets: Mapping[str, AssetRef],
    *,
    aspect: Aspect | None = None,
) -> PosterSpec:
    """Fill a template into a renderable spec.

    Args:
        template:   a PosterSpec with placeholder text and no assets bound.
        brand_kit:  the client's colours and fonts.
        texts:      layer id -> the exact string for that text layer.
        assets:     layer id -> a sha256 asset id for that image/cutout/logo layer.
        aspect:     render at this aspect instead of the template's own. Boxes are
                    fractions of the canvas, so nothing else has to change.

    Returns:
        A new PosterSpec. The template is never modified.

    Raises:
        ComposeError: one of the subclasses above, carrying every problem found.
    """
    problems: list[Problem] = []
    by_id = {layer.id: layer for layer in template.layers}
    text_ids = {layer.id for layer in template.layers if isinstance(layer, TextLayer)}
    asset_ids = {layer.id for layer in template.layers if isinstance(layer, ASSET_LAYERS)}

    # 1. Keys that name nothing, or name the wrong kind of thing.
    for given, allowed, what in ((texts, text_ids, "text"), (assets, asset_ids, "image")):
        for layer_id in given:
            if layer_id in allowed:
                continue
            if layer_id in by_id:
                problems.append(
                    Problem(
                        Kind.UNEXPECTED_LAYER,
                        f"The {template.template_id} design has a layer called "
                        f"'{layer_id}', but it does not hold {what}.",
                        layer_id,
                    )
                )
            else:
                problems.append(
                    Problem(
                        Kind.UNEXPECTED_LAYER,
                        f"The {template.template_id} design has no {what} layer "
                        f"called '{layer_id}'.",
                        layer_id,
                    )
                )

    # 2. Text layers with nothing to say. An empty string counts as nothing: the
    #    alternative is a poster with a blank where the headline should be.
    provided = {layer_id: text for layer_id, text in texts.items() if layer_id in text_ids and text}
    for layer_id in sorted(text_ids - provided.keys()):
        problems.append(
            Problem(Kind.MISSING_COPY, f"No text was given for '{layer_id}'.", layer_id)
        )

    # 3. Every colour and font the design asks for must exist in this client's brand
    #    kit. Resolve them to prove they exist, then throw the hex away - the spec
    #    stays token-only, which is what stops any tweak introducing an off-brand
    #    colour.
    for layer in template.layers:
        for field, what in _token_fields(type(layer)):
            token = getattr(layer, field)
            try:
                brand_kit.color(token) if what == "colour" else brand_kit.font(token)
            except UnknownToken:
                problems.append(
                    Problem(
                        Kind.UNKNOWN_TOKEN,
                        f"{brand_kit.client_id} has no {what} called '{token}', "
                        f"which the '{layer.id}' layer needs.",
                        layer.id,
                    )
                )

    # 4. Build the new layers, in template order - order is what sits on top of what.
    new_layers: list[Layer] = []
    for layer in template.layers:
        if isinstance(layer, TextLayer):
            content = provided.get(layer.id)
            if content is None:
                new_layers.append(layer)  # already reported; keep checking the rest
                continue
            try:
                # model_validate, not model_copy: model_copy does not re-validate, so
                # over-long copy would slip through to the renderer unnoticed.
                new_layers.append(
                    type(layer).model_validate({**layer.model_dump(), "content": content})
                )
            except ValidationError as exc:
                problems.append(_text_problem(layer.id, content, exc))
                new_layers.append(layer)
        elif isinstance(layer, ASSET_LAYERS):
            asset = assets.get(layer.id, layer.asset)
            if asset is None:
                problems.append(
                    Problem(
                        Kind.MISSING_ASSET,
                        f"The '{layer.id}' layer has no picture. Add one to "
                        f"{brand_kit.client_id}'s folder, or pick a different design.",
                        layer.id,
                    )
                )
                new_layers.append(layer)
                continue
            try:
                new_layers.append(
                    type(layer).model_validate({**layer.model_dump(), "asset": asset})
                )
            except ValidationError:
                problems.append(
                    Problem(
                        Kind.INVALID_ASSET,
                        f"The picture given for '{layer.id}' is not a stored file.",
                        layer.id,
                    )
                )
                new_layers.append(layer)
        else:
            new_layers.append(layer)

    if problems:
        _fail(problems)

    canvas = (
        template.canvas if aspect is None else template.canvas.model_copy(update={"aspect": aspect})
    )
    # Validated as a whole: whatever comes out of here is a spec the renderer can draw.
    return PosterSpec.model_validate(
        {
            **template.model_dump(),
            "canvas": canvas.model_dump(),
            "layers": [layer.model_dump() for layer in new_layers],
            "brand_kit_version": brand_kit.version,
        }
    )


__all__ = [
    "ComposeError",
    "InvalidAsset",
    "InvalidText",
    "Kind",
    "MissingAsset",
    "MissingCopy",
    "MissingToken",
    "Problem",
    "UnexpectedLayer",
    "build_spec",
]
