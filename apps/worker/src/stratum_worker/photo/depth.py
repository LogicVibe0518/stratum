"""Words behind the subject (M10g): the magazine-cover look, where the photo asks for it.

Sometimes the best crop still leaves the headline over the top of the subject: a
portrait whose head rises into the words, a square photo that cannot move in its
box. Words printed over a head look like a mistake. The same words with the head in
front of them look intended - it is how magazine covers are made. So in that case
the headline goes behind the subject (`TextLayer.behind`, schema 6): the renderer
draws the photo's subject again over the headline, and over any scrim, band or
card between them, so the person stands in front of the words.

For the headline over a photo with one clear subject (`subject.py`), measured in
every size the poster is made in:
- in at least one size, a head (a face found by YuNet, grown to the whole head)
  reaches the letters, and the subject covers at least DEPTH_MIN of them: enough to
  read as deliberate. Depth is for a person in front of the words; a product or a
  horse's back breaking a band reads as a mistake, so those keep the words in front;
- in no size does the subject cover more than DEPTH_MAX of the headline's letters,
  or more than LINE_MAX of any one line: the words must still be read;
- in no size does it cover other words that drawing it again would change: words
  drawn before the headline (their letters would be hidden), or words on a panel
  drawn before the headline (the subject would replace the panel they were made to
  be read on - dark letters for a light card, now on a dark jacket).
Otherwise the words stay in front, exactly as before.

Depth never steers the crop: crops are chosen as if every word is in front, then
depth is decided. So a reframed photo can never slide its subject further under the
words because depth would hide the collision.

Pure: no database, no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import numpy as np
from stratum_spec import BrandKit, ImageLayer, PosterSpec, ShapeLayer, TextLayer, retarget
from stratum_spec.models import Aspect

from stratum_worker.photo.read import PhotoRead
from stratum_worker.photo.subject import INSIDE
from stratum_worker.render.layout import box_px, crop_window, place_text
from stratum_worker.render.text import load_metrics

HEADLINE = "headline"
DEPTH_MIN = 0.03
DEPTH_MAX = 0.25
LINE_MAX = 0.4
# Any other words (the offer, the call to action) the subject may cover.
OTHER_MAX = 0.01
# A face this tall (share of the photo's height) or taller counts as a head.
MIN_FACE = 0.05
# The letters of a line: from the cap height above the baseline to a little below it.
CAP = 0.72
DESCENT = 0.05
# Points sampled across each line of letters.
ROWS, COLS = 6, 48

Rect = tuple[float, float, float, float]


def letters(layer: TextLayer, spec: PosterSpec, kit: BrandKit, root: Path) -> list[Rect]:
    """Where each line's letters are, in canvas pixels."""
    placed = place_text(layer, spec, kit, root)
    metrics = load_metrics(str(root / kit.font(layer.font).file))
    box_x, _, box_w, _ = placed.box
    out: list[Rect] = []
    for index, line in enumerate(placed.lines):
        width = metrics.width(line, placed.size_px)
        left = {
            "left": box_x,
            "center": box_x + (box_w - width) / 2,
            "right": box_x + box_w - width,
        }[placed.align]
        top = placed.baseline(index) - CAP * placed.size_px
        out.append((left, top, width, (CAP + DESCENT) * placed.size_px))
    return out


def hidden(spec: PosterSpec, photo: ImageLayer, read: PhotoRead, lines: list[Rect]) -> list[float]:
    """For each line, the share of its letters the photo's subject would cover."""
    if read.subject is None:
        return [0.0 for _ in lines]
    mask = read.subject.mask
    rows, cols = mask.shape
    width, height = read.size
    px, py, pw, ph = box_px(spec, photo)
    win_x, win_y, win_w, win_h = crop_window(read.size, (pw, ph), photo.crop)
    out: list[float] = []
    for x, y, w, h in lines:
        if w <= 0 or h <= 0:
            out.append(0.0)
            continue
        gx = x + (np.arange(COLS) + 0.5) / COLS * w
        gy = y + (np.arange(ROWS) + 0.5) / ROWS * h
        xx, yy = np.meshgrid(gx, gy)
        on = (xx >= px) & (xx < px + pw) & (yy >= py) & (yy < py + ph)
        fx = (win_x + (xx - px) / pw * win_w) / width
        fy = (win_y + (yy - py) / ph * win_h) / height
        col = np.clip((fx * cols).astype(int), 0, cols - 1)
        row = np.clip((fy * rows).astype(int), 0, rows - 1)
        covered = on & (mask[row, col] >= INSIDE)
        out.append(float(covered.mean()))
    return out


def _share(lines: list[Rect], shares: list[float]) -> float:
    areas = [w * h for _, _, w, h in lines]
    return sum(a * s for a, s in zip(areas, shares, strict=True)) / max(sum(areas), 1e-9)


def _head_meets(spec: PosterSpec, photo: ImageLayer, read: PhotoRead, lines: list[Rect]) -> bool:
    """A head (a face grown to the whole head) reaches the headline's letters."""
    if not lines:
        return False
    width, height = read.size
    px, py, pw, ph = box_px(spec, photo)
    win_x, win_y, win_w, win_h = crop_window(read.size, (pw, ph), photo.crop)
    left = min(x for x, _, _, _ in lines)
    top = min(y for _, y, _, _ in lines)
    right = max(x + w for x, _, w, _ in lines)
    bottom = max(y + h for _, y, _, h in lines)
    for face in read.faces:
        if face.h < MIN_FACE:
            continue
        hx, hy, hw, hh = face.head()
        x0 = px + (hx * width - win_x) / win_w * pw
        y0 = py + (hy * height - win_y) / win_h * ph
        x1 = px + ((hx + hw) * width - win_x) / win_w * pw
        y1 = py + ((hy + hh) * height - win_y) / win_h * ph
        if x0 < right and left < x1 and y0 < bottom and top < y1:
            return True
    return False


def _overlaps(one: Rect, two: Rect) -> bool:
    ax, ay, aw, ah = one
    bx, by, bw, bh = two
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _touched(spec: PosterSpec, photo: int, headline: int, text: int, lines: list[Rect]) -> bool:
    """Would drawing the subject again, just above the headline, change these words?

    Yes when they are drawn before the headline (the subject covers their letters), or
    when they sit on a panel drawn before the headline (the subject covers the panel,
    so dark letters made for a light card would be on the subject instead).
    """
    if text < headline:
        return True
    return any(
        isinstance(layer, ShapeLayer)
        and layer.visible
        and any(_overlaps(line, box_px(spec, layer)) for line in lines)
        for layer in spec.layers[photo + 1 : headline]
    )


def _verdict(
    spec: PosterSpec, reads: Mapping[str, PhotoRead], kit: BrandKit, root: Path
) -> dict[str, str]:
    """In one size, for each photo with a clear subject: "fits", "refused" or absent."""
    out: dict[str, str] = {}
    texts = [
        (index, layer)
        for index, layer in enumerate(spec.layers)
        if isinstance(layer, TextLayer) and layer.visible
    ]
    headline = next((index for index, layer in texts if layer.id == HEADLINE), None)
    if headline is None:
        return out
    for photo_index, photo in enumerate(spec.layers[:headline]):
        if not (
            isinstance(photo, ImageLayer)
            and photo.visible
            and photo.fit == "cover"
            and photo.asset in reads
        ):
            continue
        read = reads[photo.asset]  # type: ignore[index]
        if read.subject is None or not read.subject.clear:
            continue
        verdict: str | None = None
        for text_index, text in texts:
            if text_index < photo_index:
                continue
            lines = letters(text, spec, kit, root)
            if text.id != HEADLINE:
                if not _touched(spec, photo_index, headline, text_index, lines):
                    continue
                if _share(lines, hidden(spec, photo, read, lines)) > OTHER_MAX:
                    verdict = "refused"
                continue
            shares = hidden(spec, photo, read, lines)
            covered = _share(lines, shares)
            if covered > DEPTH_MAX or max(shares, default=0.0) > LINE_MAX:
                verdict = "refused"
            elif covered >= DEPTH_MIN and _head_meets(spec, photo, read, lines):
                verdict = verdict or "fits"
        if verdict is not None:
            out[photo.id] = verdict
    return out


def decide_depth(
    spec: PosterSpec,
    reads: Mapping[str, PhotoRead],
    kit: BrandKit,
    root: Path,
    aspects: Iterable[Aspect],
) -> PosterSpec:
    """The poster with the headline behind a photo's subject where that fits in every
    size (see the module doc), and in front everywhere else."""
    # Decided from scratch: an older choice is not evidence for this framing.
    plain = _all_in_front(spec)
    if not any(read.subject is not None and read.subject.clear for read in reads.values()):
        return plain  # most photos: nothing to measure
    base = spec.canvas.aspect
    sizes: list[Aspect] = [base] if not spec.variants else list(dict.fromkeys([base, *aspects]))
    fits: set[str] = set()
    refused: set[str] = set()
    for aspect in sizes:
        one = retarget(plain, aspect) if aspect != base else plain
        for photo_id, verdict in _verdict(one, reads, kit, root).items():
            if verdict == "fits":
                fits.add(photo_id)
            elif verdict == "refused":
                refused.add(photo_id)
    chosen = sorted(fits - refused)
    if not chosen:
        return plain
    order = [layer.id for layer in plain.layers]
    photo_id = min(chosen, key=order.index)  # one subject in front of the headline
    layers = [
        layer.model_copy(update={"behind": photo_id})
        if isinstance(layer, TextLayer) and layer.id == HEADLINE and order.index(photo_id) < i
        else layer
        for i, layer in enumerate(plain.layers)
    ]
    return plain.model_copy(update={"layers": layers})


def _all_in_front(spec: PosterSpec) -> PosterSpec:
    if not any(isinstance(layer, TextLayer) and layer.behind for layer in spec.layers):
        return spec
    layers = [
        layer.model_copy(update={"behind": None}) if isinstance(layer, TextLayer) else layer
        for layer in spec.layers
    ]
    return spec.model_copy(update={"layers": layers})


__all__ = ["DEPTH_MAX", "DEPTH_MIN", "decide_depth", "hidden", "letters"]
