"""M10i through the product: typed tweaks and the control panel making the new edits.

What a reviewer should be able to repeat back:
- a typed request the model reads as a fade, a flip or a badge makes the next version,
  and the badge's words are checked like every other word on the poster;
- the control panel can send every new op and a badge, with no model, and History
  says what changed in plain words;
- a font the brand does not have is refused with the brand's own fonts named, in a
  tweak and in the panel.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from stratum_spec import ImageLayer, TextLayer
from stratum_worker.app import service
from stratum_worker.draft.pipeline import DraftError
from test_smarter_tweaks import studio


async def test_a_typed_fade_flip_and_badge_make_the_next_version(tmp_path: Path) -> None:
    answer = {
        "ops": [
            {"op": "fade", "layer": "bg", "edge": "bottom", "length": 0.3},
            {"op": "flip", "layer": "bg"},
            {"op": "badge", "text": "20% off", "corner": "auto"},
        ]
    }
    one = await studio(tmp_path, answer)
    post = await one.post()
    out = await one.tweak(post, "fade the photo, flip it, and add a 20% off badge")
    assert out["outcome"] == "applied", out
    spec = await one.latest_spec(post)
    photo = spec.layer("bg")
    assert isinstance(photo, ImageLayer) and photo.flip and photo.fades[0].edge == "bottom"
    words = spec.layer("badge_text")
    assert isinstance(words, TextLayer) and words.content == "20% OFF"


async def test_an_invented_badge_is_flagged_like_any_other_claim(tmp_path: Path) -> None:
    answer = {"ops": [{"op": "badge", "text": "70% OFF"}]}
    one = await studio(tmp_path, answer)
    post = await one.post()
    out = await one.tweak(post, "add a badge")
    assert out["outcome"] == "applied"
    [latest] = (await one.versions(post))[-1:]
    flags = await one.ctx.supa.select("flags", post_version_id=f"eq.{latest['id']}")
    assert any("70%" in row["message"] for row in flags), flags


async def test_the_panel_sends_the_new_ops_and_a_badge_with_no_model(tmp_path: Path) -> None:
    one = await studio(tmp_path)
    post = await one.post()
    calls = len(one.provider.calls)
    out = await service.edit(
        one.ctx,
        await one.fresh(post),
        await one.client(),
        version=1,
        raw_ops=[
            {"op": "adjust", "layer": "bg", "saturation": -1},
            {"op": "set_case", "layer": "headline", "upper": False},
            {"op": "align", "layer": "logo", "x": "center"},
        ],
        restyle=None,
        badge={"text": "New", "corner": "top_left"},
    )
    assert out["outcome"] == "applied", out
    assert len(one.provider.calls) == calls
    [edit] = await one.ctx.supa.select("edits", post_id=f"eq.{post['id']}")
    for words in ("photo light and colour", "normal case", "logo to the center", "badge NEW"):
        assert words in edit["message"], edit["message"]
    spec = await one.latest_spec(post)
    assert spec.layer("badge").box.x < 0.2


async def test_a_font_the_brand_does_not_have_is_refused(tmp_path: Path) -> None:
    comic = {"op": "set_font", "layer": "headline", "font": "font.comic_sans"}
    one = await studio(tmp_path, {"ops": [comic]})
    post = await one.post()
    out = await one.tweak(post, "headline in comic sans")
    assert out["outcome"] == "needs_human" and "display" in out["why"]
    with pytest.raises(DraftError, match="not one of the brand's"):
        await service.edit(
            one.ctx,
            await one.fresh(post),
            await one.client(),
            version=1,
            raw_ops=[comic],
            restyle=None,
        )
    assert len(await one.versions(post)) == 1
