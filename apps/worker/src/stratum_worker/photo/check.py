"""The photo check at upload, and the photo picker's ranking (M10f).

At upload, beside sharpness, brightness and size (`assets/quality.py`), each photo
gets four numbers, stored with the asset:
- subject_cover, subject_separation: the subject `subject.py` found;
- subject_top: how much of the photo's top edge the subject touches;
- calm_room: how much of the photo is calm and off the subject - where words can sit
  on the photo itself.

And one flag, advice for a person and never a refusal:
- subject_cut_off: a clear subject runs off the top of the photo (a head, a roof, the
  top of a cake). No crop can give it room above, and words cannot go over it.

The picker ranks the client's photos for a post by `fitness`:
    0.45 x sharp enough  +  0.30 x a clear subject  +  0.25 x room for words
    - 0.15 if the subject is cut off at the top
Sharpness counts in full from twice the blur threshold up, so among sharp photos the
one with a clear subject and room for words comes first. A photo uploaded before
this check scores the middle for what was not measured.

Pure: no database, no network. The model runs once per photo (`subject.py` keeps
the answer).
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from PIL import Image

from stratum_worker.assets.models import Flag
from stratum_worker.assets.quality import MIN_SHARPNESS
from stratum_worker.photo.read import texture
from stratum_worker.photo.subject import find_subject, is_clear

# The subject touches more than this share of the top edge: cut off.
TOP_CUT = 0.15
# Texture under this (0 flat .. 1 brickwork) is calm enough for words.
CALM = 0.3
# The grid calm room is measured on, cells on the longer side.
ROOM_GRID = 48
SHARP_ENOUGH = 2 * MIN_SHARPNESS
# Weights of the ranking (see the module doc).
W_SHARP, W_SUBJECT, W_ROOM, W_CUT = 0.45, 0.30, 0.25, 0.15
# A photo with no clear subject (a wide shot of a finished kitchen) is still a good
# photo; it scores this much for its subject.
NO_SUBJECT = 0.4
NOT_MEASURED = 0.5

CUT_OFF: Flag = "subject_cut_off"


def upload_check(image: Image.Image) -> tuple[dict[str, float], list[Flag]]:
    """The subject numbers and flags for one uploaded photo."""
    rgb = image.convert("RGB")
    subject = find_subject(rgb)
    width, height = rgb.size
    scale = ROOM_GRID / max(width, height)
    shape = (max(1, round(height * scale)), max(1, round(width * scale)))
    calm = texture(rgb, shape) < CALM
    off_subject = subject.on(shape) < 0.5
    numbers = {
        "subject_cover": round(subject.cover, 4),
        "subject_separation": round(subject.separation, 4),
        "subject_top": round(subject.top, 4),
        "calm_room": round(float((calm & off_subject).mean()), 4),
    }
    flags: list[Flag] = [CUT_OFF] if subject.clear and subject.top > TOP_CUT else []
    return numbers, flags


def fitness(quality: Mapping[str, float]) -> float:
    """How good a photo is for a post, 0..1 (see the module doc)."""
    sharp = min(1.0, float(quality.get("sharpness", 0.0)) / SHARP_ENOUGH)
    if "subject_cover" not in quality:
        subject, room, cut = NOT_MEASURED, NOT_MEASURED, 0.0
    else:
        cover = float(quality["subject_cover"])
        separation = float(quality.get("subject_separation", 0.0))
        clear = is_clear(cover, separation)
        subject = float(np.clip(separation, 0.0, 1.0)) if clear else NO_SUBJECT
        room = float(quality.get("calm_room", 0.0))
        cut = 1.0 if clear and float(quality.get("subject_top", 0.0)) > TOP_CUT else 0.0
    return W_SHARP * sharp + W_SUBJECT * subject + W_ROOM * room - W_CUT * cut


__all__ = ["CUT_OFF", "TOP_CUT", "fitness", "upload_check"]
