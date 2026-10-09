"""What is in a photo (M17a): SigLIP's two towers, run with onnxruntime.

SigLIP (Zhai et al. 2023, Apache-2.0) was trained on image-caption pairs to put a
photo and a sentence describing it close together. Two halves:
- the image tower turns a photo into a vector (768 numbers for the base model);
- the text tower turns a sentence into a vector of the same length.
Both vectors have length 1, so their dot product is their cosine. SigLIP also learned
how to read a cosine as a probability that the sentence describes the photo:
sigmoid(cosine x scale + bias). Because every pair is scored on its own (a sigmoid,
not a softmax across candidates), a low probability means "nothing here fits", which
is what lets Stratum ask instead of guessing.

The model was chosen, exported to ONNX, shrunk to int8 and measured in the photo lab
(`ml/photos`, built by Rushil): the numbers behind every setting here are in its
NOTES.md. Like the face and subject models, every file is checked against SHA-256
values committed with this code (`model/siglip.lock.json`) before the first use.
Unlike them, the files are too big for git (about 200 MB), so they are downloaded at
build time into `model/siglip/` (or the folder in STRATUM_SIGLIP_DIR).

No lock file: this build has no photo model, `installed()` is None and everything
that would use it says so or carries on as before.

What the folder holds:
- image.onnx: pixel_values (n x 3 x H x W, float32) -> embedding (n x D), length 1;
- text.onnx: input_ids (n x 64, int64) -> embedding (n x D), length 1;
- spiece.model: SigLIP's own sentencepiece tokenizer;
- settings.json: the preprocessing, the template, scale, bias and the search line;
- types.json (optional): the photo-type probe trained in the lab (stage 6).

The tokenizer repeats transformers' SiglipTokenizer exactly (lowercase, punctuation
removed, spaces collapsed, an end-of-text token, padded to 64 with it); the parity
test replays the tokens the lab recorded. Unlike transformers it also cuts a text
longer than 64 tokens, keeping the end-of-text token: the model has no positions
beyond 64.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import string
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
import sentencepiece as spm
from PIL import Image, ImageOps

MODELS = Path(__file__).resolve().parent / "model"
LOCK = MODELS / "siglip.lock.json"
FOLDER_ENV = "STRATUM_SIGLIP_DIR"
REQUIRED = ("image.onnx", "text.onnx", "spiece.model", "settings.json")
PROBE = "types.json"
# Photos through the image tower at once: memory stays small on a shared worker.
BATCH = 8
# The sentencepiece marker for "a word starts here".
MARK = "▁"
_PUNCTUATION = str.maketrans("", "", string.punctuation)

Vector = npt.NDArray[np.float32]


class PhotoModelError(RuntimeError):
    """A model file is missing or is not the file the lock names."""


class PhotoModel(Protocol):
    """What the rest of Stratum uses. The ONNX model below, or a stand-in in tests."""

    @property
    def id(self) -> str: ...

    @property
    def line(self) -> float: ...

    def embed_photos(self, images: Sequence[Image.Image]) -> Vector: ...

    def embed_query(self, description: str) -> Vector: ...

    def probability(self, cosines: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float64]: ...

    def photo_types(self, vectors: Vector) -> list[tuple[str | None, float]]: ...


# --- text ------------------------------------------------------------------------------


def canonical(text: str) -> str:
    """SigLIP's text clean-up (big_vision's prompt engineering, as transformers has it)."""
    text = text.lower().translate(_PUNCTUATION)
    return re.sub(r"\s+", " ", text).strip()


class Tokenizer:
    """Words -> the 64 token numbers the text tower reads."""

    def __init__(self, path: Path, max_length: int) -> None:
        self._sp = spm.SentencePieceProcessor(model_file=str(path))
        self._unk_pieces = len(self._sp.encode("<unk>", out_type=str))
        self._specials = {"</s>", "<unk>"}
        self.eos = int(self._sp.piece_to_id("</s>"))
        self.max_length = max_length

    def ids(self, text: str) -> list[int]:
        # transformers adds the marker, cleans the text, then encodes "<unk>" + text
        # and drops the "<unk>" pieces, so the first word is never merged into a
        # dummy prefix. The same steps give the same numbers.
        prepared = canonical(MARK + text.replace(MARK, " "))
        pieces: list[str] = self._sp.encode("<unk>" + prepared, out_type=str)
        if len(pieces) >= self._unk_pieces:
            pieces = pieces[self._unk_pieces :]
        if len(pieces) > 1 and pieces[0] == MARK and pieces[1] in self._specials:
            pieces = pieces[1:]
        ids = [int(self._sp.piece_to_id(piece)) for piece in pieces]
        ids = [*ids[: self.max_length - 1], self.eos]
        return ids + [self.eos] * (self.max_length - len(ids))


# --- the model -------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    model: str
    logit_scale: float
    logit_bias: float
    size: tuple[int, int]  # height, width
    resample: int
    rescale: float
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    max_length: int
    template: str
    line: float  # a search below this probability asks instead of swapping

    @classmethod
    def read(cls, path: Path) -> Settings:
        raw = json.loads(path.read_text())
        image, text = raw["image"], raw["text"]
        height, width = image["size"]
        return cls(
            model=str(raw["model"]),
            logit_scale=float(raw["logit_scale"]),
            logit_bias=float(raw["logit_bias"]),
            size=(int(height), int(width)),
            resample=int(image["resample"]),
            rescale=float(image["rescale"]),
            mean=(float(image["mean"][0]), float(image["mean"][1]), float(image["mean"][2])),
            std=(float(image["std"][0]), float(image["std"][1]), float(image["std"][2])),
            max_length=int(text["max_length"]),
            template=str(text["template"]),
            line=float(raw.get("line", 0.5)),
        )


@dataclass(frozen=True)
class Probe:
    """The photo-type classifier from the lab: a softmax over a linear layer."""

    classes: tuple[str, ...]
    weights: npt.NDArray[np.float64]  # classes x D
    bias: npt.NDArray[np.float64]  # classes
    line: float  # below this probability the type is not said

    @classmethod
    def read(cls, path: Path, model: str) -> Probe:
        raw = json.loads(path.read_text())
        embedding = raw.get("embedding")
        made_for = embedding.get("model") if isinstance(embedding, dict) else embedding
        if made_for not in (None, model):
            raise PhotoModelError(
                f"{path.name} was trained on {made_for}'s numbers, not {model}'s. "
                "Train the probe again on this model (lab, stage 6)."
            )
        return cls(
            classes=tuple(str(one) for one in raw["classes"]),
            weights=np.asarray(raw["coef"], dtype=np.float64),
            bias=np.asarray(raw["intercept"], dtype=np.float64),
            line=float(raw.get("line", 0.5)),
        )

    def __call__(self, vectors: Vector) -> list[tuple[str | None, float]]:
        scores = vectors.astype(np.float64) @ self.weights.T + self.bias
        scores -= scores.max(axis=1, keepdims=True)
        exp = np.exp(scores)
        probs = exp / exp.sum(axis=1, keepdims=True)
        out: list[tuple[str | None, float]] = []
        for row in probs:
            best = int(row.argmax())
            sure = float(row[best]) >= self.line
            out.append((self.classes[best] if sure else None, round(float(row[best]), 4)))
        return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _session(path: Path) -> ort.InferenceSession:
    options = ort.SessionOptions()
    # Two threads per run: a tweak embeds one sentence, a first search a few photos;
    # the worker runs several jobs at once, as for the face and subject models.
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class OnnxPhotoModel:
    """SigLIP from a folder of checked files."""

    def __init__(self, folder: Path, lock: dict[str, Any]) -> None:
        files: dict[str, str] = lock["files"]
        for name in REQUIRED:
            if name not in files:
                raise PhotoModelError(f"The lock does not name {name}.")
        for name, expected in files.items():
            path = folder / name
            if not path.is_file():
                raise PhotoModelError(f"The photo model is missing {name} in {folder}.")
            digest = _sha256(path)
            if digest != expected:
                raise PhotoModelError(
                    f"{name} is not the file this code was built for (SHA-256 "
                    f"{digest[:12]}..., expected {expected[:12]}...). Download it again."
                )
        self.settings = Settings.read(folder / "settings.json")
        if self.settings.model != lock["model"]:
            raise PhotoModelError(
                f"settings.json is for {self.settings.model}, the lock for {lock['model']}."
            )
        self._image = _session(folder / "image.onnx")
        self._text = _session(folder / "text.onnx")
        self.tokenizer = Tokenizer(folder / "spiece.model", self.settings.max_length)
        self._probe = Probe.read(folder / PROBE, lock["model"]) if PROBE in files else None
        self._queries: dict[str, Vector] = {}
        self._kept = threading.Lock()

    @property
    def id(self) -> str:
        return self.settings.model

    @property
    def line(self) -> float:
        return self.settings.line

    def pixels(self, image: Image.Image) -> npt.NDArray[np.float32]:
        """One photo as the image tower reads it: 3 x H x W, about -1..1."""
        upright = ImageOps.exif_transpose(image).convert("RGB")
        height, width = self.settings.size
        resized = upright.resize((width, height), resample=Image.Resampling(self.settings.resample))
        mean = np.asarray(self.settings.mean, dtype=np.float32)
        std = np.asarray(self.settings.std, dtype=np.float32)
        x = (np.asarray(resized, dtype=np.float32) * self.settings.rescale - mean) / std
        return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32)

    def embed_photos(self, images: Sequence[Image.Image]) -> Vector:
        rows = []
        for start in range(0, len(images), BATCH):
            batch = np.stack([self.pixels(one) for one in images[start : start + BATCH]])
            rows.append(np.asarray(self._image.run(None, {"pixel_values": batch})[0]))
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return np.concatenate(rows).astype(np.float32)

    def embed_texts(self, texts: Sequence[str]) -> Vector:
        ids = np.asarray([self.tokenizer.ids(text) for text in texts], dtype=np.int64)
        return np.asarray(self._text.run(None, {"input_ids": ids})[0], dtype=np.float32)

    def embed_query(self, description: str) -> Vector:
        """A designer's description, worded as the model was trained to read it."""
        text = self.settings.template.format(description.strip())
        with self._kept:
            known = self._queries.get(text)
        if known is not None:
            return known
        vector: Vector = np.asarray(self.embed_texts([text])[0], dtype=np.float32)
        with self._kept:
            if len(self._queries) > 256:
                self._queries.clear()
            self._queries[text] = vector
        return vector

    def probability(self, cosines: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float64]:
        logits = np.asarray(cosines, dtype=np.float64) * self.settings.logit_scale
        return np.asarray(1.0 / (1.0 + np.exp(-(logits + self.settings.logit_bias))))

    def photo_types(self, vectors: Vector) -> list[tuple[str | None, float]]:
        if self._probe is None:
            return [(None, 0.0) for _ in range(len(vectors))]
        return self._probe(vectors)


def load(folder: Path, lock_path: Path) -> OnnxPhotoModel:
    return OnnxPhotoModel(folder, json.loads(lock_path.read_text()))


@cache
def installed() -> PhotoModel | None:
    """The photo model this build ships with, checked; None when it ships without one.

    A lock with a missing or wrong file is an error, not a quiet None: a deploy that
    lost its model should fail where someone sees it.
    """
    if not LOCK.is_file():
        return None
    folder = Path(os.environ.get(FOLDER_ENV) or MODELS / "siglip")
    return load(folder, LOCK)


# --- keeping a vector with its photo ---------------------------------------------------


def pack(vector: Vector) -> str:
    """A vector as text for the photo's row: float16, base64. 768 numbers -> 2 KB.

    Half precision changes a cosine by about 0.001, less than int8 already does."""
    return base64.b64encode(np.asarray(vector, dtype="<f2").tobytes()).decode("ascii")


def unpack(text: str) -> Vector:
    raw = np.frombuffer(base64.b64decode(text), dtype="<f2").astype(np.float32)
    norm = float(np.linalg.norm(raw))
    return raw / norm if norm > 0 else raw


__all__ = [
    "LOCK",
    "OnnxPhotoModel",
    "PhotoModel",
    "PhotoModelError",
    "Probe",
    "Settings",
    "Tokenizer",
    "Vector",
    "canonical",
    "installed",
    "load",
    "pack",
    "unpack",
]
