"""The feature walk (M10i, docs/editing-standard.md): every editing feature, once, on
fixed posters, in every size, on one contact sheet.

    make feature-walk        # writes feature-walk.png and prints any problem

Testing editing features one by one, by hand, on whatever post is open, finds them
broken one at a time. This applies each one to the chat eval's fixed posters (a
fictional client, stand-in photos), renders all four sizes, and puts one row per
feature on a sheet: one look shows what broke. A feature that raises, or changes
nothing, is listed as a problem, and the test suite runs the walk too.

Every op the control panel may send (service.EDITOR_OPS) must have a row here; a
test checks it, so a new editing feature cannot ship without one.
"""

from __future__ import annotations

import argparse
import io
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from stratum_spec import Box, BrandKit, PosterSpec, apply_ops, retarget
from stratum_spec.ops import (
    AdjustPhoto,
    Align,
    Dim,
    EditOp,
    Flip,
    Move,
    Pan,
    Place,
    Reorder,
    Scale,
    SetAlign,
    SetCase,
    SetColor,
    SetEffect,
    SetFade,
    SetFont,
    SetOpacity,
    SetText,
    SetVisible,
    SwapAsset,
    Zoom,
)

from stratum_worker.design.preview import bind, ink, stand_ins
from stratum_worker.design.space import SIZES, lay_out
from stratum_worker.edit import badge
from stratum_worker.edit.chateval import POSTERS, WORDS, poster
from stratum_worker.render import render_poster

REPO = Path(__file__).resolve().parents[5]
COLLAGE = "ds.collage.1.0000000"


@dataclass(frozen=True)
class Step:
    label: str
    poster: str  # a chat eval poster, or "collage"
    ops: tuple[EditOp, ...] = ()
    change: Callable[[PosterSpec, BrandKit], PosterSpec] | None = None  # a service action

    @property
    def op_names(self) -> set[str]:
        return {op.op for op in self.ops}


TEXTS = ("headline", "offer", "cta")
WALK: tuple[Step, ...] = (
    # Before M10i.
    Step("new words", "panel", (SetText(layer="headline", text="Boilers fixed fast"),)),
    Step("headline colour", "panel", (SetColor(layer="headline", color="palette.secondary"),)),
    Step("move the logo", "type", (Move(layer="logo", dx=0.1, dy=0.05),)),
    Step("bigger headline", "overlay", (Scale(layer="headline", factor=1.15),)),
    Step("hide the call to action", "panel", (SetVisible(layer="cta", visible=False),)),
    Step("pan the photo", "panel", (Zoom(layer="bg", factor=1.3), Pan(layer="bg", dx=0.1, dy=0))),
    Step("darker photo", "overlay", (Dim(layer="bg", amount=0.35),)),
    # The first editing set.
    Step("fade into the panel", "panel", (SetFade(layer="bg", edge="bottom", length=0.35),)),
    Step("flip the photo", "panel", (Flip(layer="bg"),)),
    Step("black and white", "panel", (AdjustPhoto(layer="bg", saturation=-1.0),)),
    Step("warmer, brighter", "overlay", (AdjustPhoto(layer="bg", warmth=0.3, brightness=0.15),)),
    Step("words centred", "type", tuple(SetAlign(layer=one, align="center") for one in TEXTS)),
    Step("headline in the body font", "panel", (SetFont(layer="headline", font="font.body"),)),
    Step("headline in normal case", "panel", (SetCase(layer="headline", upper=False),)),
    Step(
        "shadow on the words",
        "overlay",
        tuple(SetEffect(layer=one, kind="shadow") for one in TEXTS),
    ),
    Step("outline on the headline", "cover", (SetEffect(layer="headline", kind="outline"),)),
    Step("accent line to the back", "pair", (Reorder(layer="rule", to="back"),)),
    Step("see-through panel", "panel", (SetOpacity(layer="panel", opacity=0.75),)),
    Step("logo top centre", "type", (Align(layer="logo", x="center", y="top"),)),
    Step("offer badge", "panel", change=lambda spec, kit: badge.place(spec, kit, "20% off")),
    Step("collage design", "collage"),
    # Photos by what is in them (M17a): the closest photo, picked in the panel.
    Step("another photo", "panel", (SwapAsset(layer="bg", asset="b" * 64),)),
    # The canvas (editor v2): dragged in one size only; a corner sizes the words too.
    Step(
        "logo dragged in the story",
        "type",
        (Place(layer="logo", aspect="9:16", box=Box(x=0.676, y=0.7, w=0.25, h=0.046)),),
    ),
    Step(
        "headline corner-dragged",
        "panel",
        (
            Place(
                layer="headline",
                aspect="4:5",
                box=Box(x=0.074, y=0.6, w=0.852, h=0.16),
                text_size=0.07,
            ),
        ),
    ),
)

TILE_H = 220
LABEL_W = 230
GAP = 8


def _poster(name: str, root: Path) -> tuple[PosterSpec, BrandKit]:
    if name != "collage":
        shown = poster(name, root)
        return shown.spec, shown.kit
    base = poster("pair", root)
    folder = root / "clients" / POSTERS["pair"][0]
    with Image.open(folder / "logo.png") as logo:
        aspect = logo.width / logo.height
    laid = lay_out(COLLAGE, base.kit, root, copy=WORDS, logo_aspect=aspect)
    if laid is None:
        raise ValueError(f"{COLLAGE} does not lay out around the walk's words")
    return bind(laid, base.kit, WORDS, logo=ink(folder / "logo.png")), base.kit


def apply(step: Step, root: Path) -> tuple[PosterSpec, PosterSpec, BrandKit]:
    """(before, after, kit) for one step. Raises what the edit raises."""
    before, kit = _poster(step.poster, root)
    after = step.change(before, kit) if step.change else before
    return before, apply_ops(after, list(step.ops)), kit


def _tile(
    spec: PosterSpec, kit: BrandKit, root: Path, files: dict[str, str], aspect: str
) -> Image.Image:
    one = retarget(spec, aspect)
    width = round(TILE_H * one.canvas.size[0] / one.canvas.size[1])
    one = one.model_copy(
        update={"canvas": one.canvas.model_copy(update={"base_width": max(width, 64)})}
    )
    image = Image.open(io.BytesIO(render_poster(one, kit, assets=files, root=root))).convert("RGB")
    return image.resize((round(image.width * TILE_H / image.height), TILE_H))


def run(root: Path = REPO, steps: tuple[Step, ...] = WALK) -> tuple[Image.Image, list[str]]:
    """The contact sheet, and the problems: a step that raised or changed nothing."""
    problems: list[str] = []
    rows: list[tuple[str, list[Image.Image]]] = []
    font_file = root / "assets" / "fonts" / "Barlow-Bold.ttf"
    font = (
        ImageFont.truetype(str(font_file), 20) if font_file.is_file() else ImageFont.load_default()
    )
    with tempfile.TemporaryDirectory() as tmp:
        folder = root / "clients" / POSTERS["panel"][0]
        files = stand_ins(Path(tmp), folder / "logo.png")
        for step in steps:
            try:
                before, after, kit = apply(step, root)
            except Exception as exc:
                problems.append(f"{step.label}: {type(exc).__name__}: {exc}")
                continue
            if after == before and step.poster != "collage":
                problems.append(f"{step.label}: changed nothing")
            try:
                rows.append((step.label, [_tile(after, kit, root, files, a) for a in SIZES]))
            except Exception as exc:
                problems.append(f"{step.label}: does not render: {type(exc).__name__}: {exc}")
    width = LABEL_W + max((sum(t.width + GAP for t in tiles) for _, tiles in rows), default=0)
    sheet = Image.new("RGB", (width + GAP, len(rows) * (TILE_H + GAP) + GAP), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for index, (label, tiles) in enumerate(rows):
        y = GAP + index * (TILE_H + GAP)
        draw.text((GAP, y + TILE_H // 2 - 10), label, fill=(235, 235, 235), font=font)
        x = LABEL_W
        for tile in tiles:
            sheet.paste(tile, (x, y))
            x += tile.width + GAP
    return sheet, problems


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("feature-walk.png"))
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    sheet, problems = run(args.root)
    sheet.save(args.out)
    print(f"{len(WALK)} features -> {args.out}")
    for problem in problems:
        print("PROBLEM", problem)
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
