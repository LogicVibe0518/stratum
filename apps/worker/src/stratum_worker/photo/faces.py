"""Faces in a photo, so a crop never cuts through a head or puts words over one.

The model is YuNet (OpenCV Zoo, MIT licence, 233 KB, `model/LICENSE-yunet.txt`),
run with onnxruntime. It is committed next to this file rather than downloaded at
start-up, so a draft never depends on a third-party server being up, and its
SHA-256 is checked before the first use, so a swapped file is refused rather than
trusted.

Why a model at all: the no-model subject finder in `read.py` finds what stands out
in colour and texture. On a portrait it picked the orange suit and the helmet, and
left the face out. A face is the one thing a crop must never cut, so it gets a
detector that is trained for exactly that.

YuNet sees a fixed 640 x 640 input and finds faces from about 10 to 300 pixels at
that scale. The photo is letterboxed into it twice, at full size and at half size,
so a close-up face that fills the frame is found too.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
from PIL import Image

MODEL = Path(__file__).resolve().parent / "model" / "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

SIDE = 640
STRIDES = (8, 16, 32)
# The photo's longer side, in model pixels, for each pass: whole, and half size.
PASSES = (640, 320)
# OpenCV's own demo uses 0.9. Lower finds more real faces in phone photos (side-on,
# partly lit); 0.7 still produced no false face on any test scene.
MIN_SCORE = 0.7
# Two boxes overlapping more than this are one face (non-maximum suppression).
SAME_FACE = 0.3
MOST_FACES = 12


class FaceModelError(RuntimeError):
    """The face model file is missing or is not the file this code was written for."""


@dataclass(frozen=True)
class Face:
    """One face: its box as fractions of the photo's width and height."""

    x: float
    y: float
    w: float
    h: float
    score: float

    def head(self) -> tuple[float, float, float, float]:
        """The face box grown to the whole head: hair above, a little either side.

        YuNet's box runs from the eyebrows to the chin. A crop that keeps only that
        box still cuts off the top of the head, which is exactly what looks wrong.
        """
        return self.x - 0.15 * self.w, self.y - 0.4 * self.h, 1.3 * self.w, 1.5 * self.h


@cache
def _session() -> ort.InferenceSession:
    if not MODEL.is_file():
        raise FaceModelError(f"The face model is missing: {MODEL.name} should be in {MODEL.parent}")
    digest = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise FaceModelError(
            f"{MODEL.name} is not the YuNet file this code was written for (SHA-256 "
            f"{digest[:12]}..., expected {MODEL_SHA256[:12]}...). Restore it from git."
        )
    options = ort.SessionOptions()
    # The worker runs several jobs at once; one thread each keeps them from fighting
    # over the same cores. A 640 x 640 pass takes about 20 ms on one core.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])


def _letterbox(image: Image.Image, longest: int) -> tuple[npt.NDArray[np.float32], float]:
    """The photo scaled so its longer side is `longest`, top-left on black, as BGR."""
    scale = longest / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    small = np.asarray(image.resize(size, Image.Resampling.BILINEAR), dtype=np.float32)
    canvas = np.zeros((SIDE, SIDE, 3), dtype=np.float32)
    canvas[: small.shape[0], : small.shape[1]] = small[..., ::-1]  # RGB -> BGR
    return canvas.transpose(2, 0, 1)[None], scale


def decode(
    outputs: Mapping[str, npt.NDArray[np.float32]],
    scale: float,
    size: tuple[int, int],
    *,
    min_score: float = MIN_SCORE,
) -> list[Face]:
    """YuNet's raw outputs -> faces, as fractions of a photo of `size` pixels.

    For every cell of each stride's grid the model gives a class score, an
    "objectness" score, and a box as offsets from the cell (centre) and log sizes
    (width, height), all in units of the stride. The same decoding as OpenCV's
    FaceDetectorYN.
    """
    width, height = size
    found: list[Face] = []
    for stride in STRIDES:
        cols = SIDE // stride
        cls = np.clip(outputs[f"cls_{stride}"][0, :, 0], 0.0, 1.0)
        obj = np.clip(outputs[f"obj_{stride}"][0, :, 0], 0.0, 1.0)
        score = np.sqrt(cls * obj)
        boxes = outputs[f"bbox_{stride}"][0]
        for index in np.nonzero(score >= min_score)[0]:
            row, col = divmod(int(index), cols)
            dx, dy, log_w, log_h = (float(v) for v in boxes[index])
            box_w, box_h = np.exp(log_w) * stride, np.exp(log_h) * stride
            centre_x, centre_y = (col + dx) * stride, (row + dy) * stride
            left = (centre_x - box_w / 2) / scale
            top = (centre_y - box_h / 2) / scale
            found.append(
                Face(
                    x=left / width,
                    y=top / height,
                    w=float(box_w) / scale / width,
                    h=float(box_h) / scale / height,
                    score=float(score[index]),
                )
            )
    return found


def _overlap(one: Face, two: Face) -> float:
    """Intersection over union of two face boxes."""
    w = min(one.x + one.w, two.x + two.w) - max(one.x, two.x)
    h = min(one.y + one.h, two.y + two.h) - max(one.y, two.y)
    inter = max(0.0, w) * max(0.0, h)
    union = one.w * one.h + two.w * two.h - inter
    return inter / union if union > 0 else 0.0


def distinct(faces: list[Face]) -> tuple[Face, ...]:
    """Keep the best-scoring box of each face (non-maximum suppression)."""
    kept: list[Face] = []
    for face in sorted(faces, key=lambda one: one.score, reverse=True):
        if all(_overlap(face, other) < SAME_FACE for other in kept):
            kept.append(face)
    return tuple(kept[:MOST_FACES])


def find_faces(image: Image.Image) -> tuple[Face, ...]:
    """Every face in the photo, best first. Deterministic: same photo, same faces."""
    rgb = image.convert("RGB")
    session = _session()
    names = [output.name for output in session.get_outputs()]
    found: list[Face] = []
    for longest in PASSES:
        tensor, scale = _letterbox(rgb, longest)
        raw = session.run(None, {"input": tensor})  # thread-safe: jobs may share it
        outputs = dict(zip(names, raw, strict=True))
        found.extend(decode(outputs, scale, rgb.size))
    return distinct(found)


__all__ = ["MODEL", "MODEL_SHA256", "Face", "FaceModelError", "decode", "distinct", "find_faces"]
