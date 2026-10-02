"""The subject of a photo, as a mask: which pixels belong to the thing the photo is of.

The model is U^2-Net small ("u2netp", Qin et al. 2020, Apache-2.0,
`model/LICENSE-u2net.txt`): 4.6 MB, the ONNX export rembg distributes. Like the face
model it is committed next to this file, so a draft never waits on a third-party
server, and its SHA-256 is checked before the first use.

Why a model: the no-model salience in `read.py` finds what differs from the photo's
edges in colour and texture. It finds a red disc on a wall, but on a plumber in
front of a van it picks the van's orange stripe. U^2-Net was trained to outline the
salient object itself (DUTS-TR, 10,553 photos with hand-drawn masks), so it marks
the plumber, the cake or the car, edge to edge.

The photo is squashed to 320 x 320, the shape the model was trained on, and
normalised with the ImageNet mean and deviation. The model's answer is a
probability per pixel (its last step is a sigmoid). rembg stretches every answer to
fill 0..1; this does not, because on a photo with no subject the stretched map
invents one: on a photo of grass the model's highest probability is 0.27, which
stretched becomes a confident "subject".

A subject is clear when both hold:
- it covers between 2% and 75% of the photo. Smaller is a speck; larger is the whole
  photo, with nothing beside it to place words on;
- the mask is decisive: the mean probability inside it is at least 0.5 above the
  mean outside it.
Otherwise the photo has no clear subject and `read.py` uses its own salience alone.

About 0.45 s per photo on one core. Answers are kept per photo (by the pixels the
model sees), so the upload check and every later draft share one run.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
from PIL import Image

MODEL = Path(__file__).resolve().parent / "model" / "u2netp.onnx"
MODEL_SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"

SIDE = 320
MEAN = np.array([0.485, 0.456, 0.406])
STD = np.array([0.229, 0.224, 0.225])
# A pixel with at least this probability is part of the subject.
INSIDE = 0.5
COVER_MIN = 0.02
COVER_MAX = 0.75
DECISIVE = 0.5
# The mask is kept with the photo's own shape, this many cells on its longer side.
MASK_SIDE = 160
# Answers kept in memory: 64 photos is about 6.5 MB.
KEPT = 64

Mask = npt.NDArray[np.float32]


class SubjectModelError(RuntimeError):
    """The subject model file is missing or is not the file this code was written for."""


@dataclass(frozen=True)
class Subject:
    """The subject of one photo. Positions are fractions of the photo."""

    mask: Mask = field(repr=False)  # probability, rows x cols with the photo's shape
    cover: float  # share of the photo inside the subject
    separation: float  # mean probability inside minus mean outside
    box: tuple[float, float, float, float]  # x, y, w, h around the subject; zeros if none
    top: float  # share of the photo's top edge the subject touches

    @property
    def clear(self) -> bool:
        return is_clear(self.cover, self.separation)

    def on(self, shape: tuple[int, int]) -> npt.NDArray[np.float64]:
        """The mask averaged into a grid of `shape` (rows, cols), 0..1."""
        rows, cols = shape
        small = Image.fromarray(self.mask, mode="F").resize((cols, rows), Image.Resampling.BOX)
        out: npt.NDArray[np.float64] = np.clip(np.asarray(small, dtype=np.float64), 0.0, 1.0)
        return out


def is_clear(cover: float, separation: float) -> bool:
    """One clear subject: not a speck, not the whole photo, and a decisive mask."""
    return COVER_MIN <= cover <= COVER_MAX and separation >= DECISIVE


@cache
def _session() -> ort.InferenceSession:
    if not MODEL.is_file():
        raise SubjectModelError(
            f"The subject model is missing: {MODEL.name} should be in {MODEL.parent}"
        )
    digest = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise SubjectModelError(
            f"{MODEL.name} is not the U^2-Net file this code was written for (SHA-256 "
            f"{digest[:12]}..., expected {MODEL_SHA256[:12]}...). Restore it from git."
        )
    options = ort.SessionOptions()
    # One thread per run, as for the face model: the worker runs several jobs at once.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])


_kept: OrderedDict[str, npt.NDArray[np.uint8]] = OrderedDict()
_lock = threading.Lock()


def _squashed(image: Image.Image) -> npt.NDArray[np.uint8]:
    """The photo as the model sees it: RGB, 320 x 320, whatever its shape."""
    rgb = image.convert("RGB")
    small = rgb.resize((SIDE, SIDE), Image.Resampling.LANCZOS, reducing_gap=3.0)
    out: npt.NDArray[np.uint8] = np.asarray(small, dtype=np.uint8)
    return out


def _run(pixels: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """The model's probability for each of the 320 x 320 pixels, as 0..255."""
    values = pixels.astype(np.float64)
    values = values / max(float(values.max()), 1e-6)  # as the model was trained
    tensor = ((values - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    session = _session()
    first = session.get_outputs()[0].name  # the fused map; the others are side outputs
    [out] = session.run([first], {session.get_inputs()[0].name: tensor})
    probability = np.clip(np.asarray(out, dtype=np.float64)[0, 0], 0.0, 1.0)
    answer: npt.NDArray[np.uint8] = np.round(probability * 255).astype(np.uint8)
    return answer


def probability(image: Image.Image) -> Mask:
    """The model's answer for the photo: 320 x 320 probabilities, 0..1.

    Kept per photo: the same pixels never run the model twice in one process.
    """
    pixels = _squashed(image)
    key = hashlib.sha256(pixels.tobytes()).hexdigest()
    with _lock:
        known = _kept.get(key)
        if known is not None:
            _kept.move_to_end(key)
    if known is None:
        known = _run(pixels)
        with _lock:
            _kept[key] = known
            while len(_kept) > KEPT:
                _kept.popitem(last=False)
    out: Mask = known.astype(np.float32) / 255.0
    return out


def subject_of(mask: Mask) -> Subject:
    """Measure a probability mask that already has the photo's shape."""
    inside = mask >= INSIDE
    cover = float(inside.mean())
    if inside.any() and not inside.all():
        separation = float(mask[inside].mean() - mask[~inside].mean())
    else:
        separation = 0.0
    if inside.any():
        rows, cols = np.nonzero(inside)
        height, width = mask.shape
        box = (
            float(cols.min()) / width,
            float(rows.min()) / height,
            float(cols.max() + 1 - cols.min()) / width,
            float(rows.max() + 1 - rows.min()) / height,
        )
    else:
        box = (0.0, 0.0, 0.0, 0.0)
    top = float(inside[:2].any(axis=0).mean())
    return Subject(mask=mask, cover=cover, separation=separation, box=box, top=top)


def find_subject(image: Image.Image) -> Subject:
    """The subject of the photo. Deterministic: the same photo, the same subject."""
    width, height = image.size
    scale = MASK_SIDE / max(width, height)
    shape = (max(1, round(width * scale)), max(1, round(height * scale)))
    square = Image.fromarray(probability(image), mode="F")
    mask = np.asarray(square.resize(shape, Image.Resampling.BILINEAR), dtype=np.float32)
    return subject_of(np.clip(mask, 0.0, 1.0))


__all__ = [
    "MODEL",
    "MODEL_SHA256",
    "Subject",
    "SubjectModelError",
    "find_subject",
    "is_clear",
    "probability",
    "subject_of",
]
