"""Stratum layer spec: a poster is data, not pixels.

Design decisions (see docs/adr/0001-poster-is-data.md):

1. Geometry is stored as FRACTIONS of the canvas, not pixels, so one spec renders
   at 1080x1080, 1080x1350 or 1080x1920 without re-layout.
2. Colours and fonts are BRAND TOKENS ("palette.primary"), never raw hex, so no
   model or tweak can introduce an off-brand colour.
3. Layers are a discriminated union; stacking order is list order (index 0 = back).
4. Assets are referenced by SHA-256 only; bytes live in object storage.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 2: LogoLayer.plate (M9b). Version-1 specs have no plate and upgrade unchanged.
# 3: PosterSpec.variants (M11a). A poster carries its own layout for every size.
# 4: ImageLayer.crop (M10b). Which part of a photo shows; none = the centre, as before.
SCHEMA_VERSION = 4

# A coordinate may sit slightly outside the canvas (full-bleed art, deliberate crops).
Coord = Annotated[float, Field(ge=-1.0, le=2.0)]
Size = Annotated[float, Field(gt=0.0, le=3.0)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
LayerId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ColorToken = Annotated[str, Field(pattern=r"^palette\.[a-z][a-z0-9_]{0,31}$")]
FontToken = Annotated[str, Field(pattern=r"^font\.[a-z][a-z0-9_]{0,31}$")]
AssetRef = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
# 4:5 is the Instagram feed default; 1:1 is a free re-render of it. 9:16 (story,
# reel) and 16:9 (LinkedIn, YouTube) need per-aspect box overrides to look right -
# see docs/modules/M7-draft-post.md.
Aspect = Literal["1:1", "4:5", "9:16", "16:9"]


class Frozen(BaseModel):
    """Immutable, strict base: unknown fields are an error, not a silent drop."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Box(Frozen):
    """Where a layer sits, as fractions of the canvas. Origin is top-left."""

    x: Coord
    y: Coord
    w: Size
    h: Size
    rotation: float = Field(default=0.0, ge=-180.0, le=180.0)


class BaseLayer(Frozen):
    id: LayerId
    box: Box
    opacity: Unit = 1.0
    visible: bool = True
    locked: bool = False


class Crop(Frozen):
    """Which part of a photo a "cover" image shows.

    A photo rarely has the shape of its box, so part of it is cut off. Before schema
    4 that part was always cut evenly from both sides, which cut heads off and put
    headlines over faces. Now the draft chooses, per size, where the window sits.

    x, y: the centre of the part shown, as fractions of the photo's width and height.
        The window is kept inside the photo, so x=1.0 means "as far right as it goes".
    zoom: 1 shows as much of the photo as fills the box; 1.25 shows 80% of that.
    """

    x: Unit = 0.5
    y: Unit = 0.5
    zoom: float = Field(default=1.0, ge=1.0, le=3.0)


class ImageLayer(BaseLayer):
    """A photo: the client's own, licensed stock, or a generated background."""

    type: Literal["image"] = "image"
    asset: AssetRef | None = None
    source: Literal["client", "stock", "generated"] = "client"
    fit: Literal["cover", "contain"] = "cover"
    blur: float = Field(default=0.0, ge=0.0, le=1.0)
    dim: float = Field(default=0.0, ge=0.0, le=1.0)
    # Only for fit="cover". None = the centre of the photo.
    crop: Crop | None = None


class CutoutLayer(BaseLayer):
    """The client's real product with its background removed. Pixels are sacred."""

    type: Literal["cutout"] = "cutout"
    asset: AssetRef | None = None
    shadow: bool = True


class LogoPlate(Frozen):
    """A panel drawn behind the logo so it can be seen.

    A navy logo on a navy band disappears. The fix is never to recolour the logo -
    it is the client's own artwork - but to change what it sits on. The plate hugs
    the logo as drawn, not its box, so a wide wordmark gets a wide, short panel, and
    logo and plate together stay inside the logo's box.
    """

    fill: ColorToken = "palette.surface"
    opacity: Unit = 0.96
    # Space around the logo on every side, as a fraction of the logo's shorter side.
    pad: Unit = 0.25
    # Corner radius, as a fraction of the plate's height.
    radius: Unit = 0.2


class LogoLayer(BaseLayer):
    """Always the client's original file. Never generated, never recoloured."""

    type: Literal["logo"] = "logo"
    asset: AssetRef | None = None
    variant: Literal["full", "white", "mono"] = "full"
    safe_area: Unit = 0.02
    plate: LogoPlate | None = None


class ShapeLayer(BaseLayer):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "rounded", "ellipse"] = "rect"
    fill: ColorToken = "palette.primary"
    radius: Unit = 0.0


class TextLayer(BaseLayer):
    """Text is drawn by our renderer, so it stays crisp and always editable."""

    type: Literal["text"] = "text"
    content: str = Field(min_length=1, max_length=500)
    font: FontToken = "font.body"
    color: ColorToken = "palette.text"
    align: Literal["left", "center", "right"] = "left"
    max_lines: int = Field(default=3, ge=1, le=12)
    # Auto-fit searches this range, in fractions of canvas height.
    min_size: Unit = 0.02
    max_size: Unit = 0.12
    uppercase: bool = False

    @model_validator(mode="after")
    def _size_range_ordered(self) -> Self:
        if self.min_size > self.max_size:
            raise ValueError("min_size must be <= max_size")
        return self


Layer = Annotated[
    ImageLayer | CutoutLayer | LogoLayer | ShapeLayer | TextLayer,
    Field(discriminator="type"),
]


class LayoutOverride(Frozen):
    """How one layer is laid out in one size, where that differs from the base size.

    Layout only. Content, colour, font and asset are deliberately absent, so no size
    of a poster can say or show something the client did not approve in another.
    A field left as None keeps the base layer's value.
    """

    box: Box | None = None
    align: Literal["left", "center", "right"] | None = None
    max_lines: int | None = Field(default=None, ge=1, le=12)
    min_size: Unit | None = None
    max_size: Unit | None = None
    visible: bool | None = None
    opacity: Unit | None = None
    fit: Literal["cover", "contain"] | None = None
    dim: Unit | None = None
    blur: Unit | None = None
    radius: Unit | None = None
    safe_area: Unit | None = None
    uppercase: bool | None = None
    crop: Crop | None = None

    def changes(self) -> dict[str, object]:
        """The fields this override sets, ready to lay over a layer's own."""
        return self.model_dump(exclude_none=True)


class Canvas(Frozen):
    aspect: Aspect = "4:5"
    base_width: int = Field(default=1080, ge=256, le=4096)

    @property
    def size(self) -> tuple[int, int]:
        w_ratio, h_ratio = (int(part) for part in self.aspect.split(":"))
        return self.base_width, round(self.base_width * h_ratio / w_ratio)


class PosterSpec(Frozen):
    """One poster version. Immutable: an edit produces a new spec."""

    schema_version: int = SCHEMA_VERSION
    template_id: str = Field(min_length=1, max_length=64)
    template_version: int = Field(default=1, ge=1)
    brand_kit_version: int = Field(default=1, ge=1)
    canvas: Canvas = Canvas()
    layers: list[Layer] = Field(min_length=1, max_length=64)
    # Per-size layout, carried by the poster itself (schema 3). Before this, the
    # other sizes were read from the template file at render time: editing a
    # template quietly moved every old poster's story, and an edit such as "make
    # the logo bigger" reached the feed post but not the story.
    variants: dict[Aspect, dict[LayerId, LayoutOverride]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        ids = [layer.id for layer in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("layer ids must be unique")
        known = set(ids)
        for aspect, overrides in self.variants.items():
            unknown = set(overrides) - known
            if unknown:
                raise ValueError(
                    f"the {aspect} layout names layers the poster does not have: "
                    f"{', '.join(sorted(unknown))}"
                )
        return self

    def layer(self, layer_id: str) -> Layer:
        for candidate in self.layers:
            if candidate.id == layer_id:
                return candidate
        raise KeyError(layer_id)
