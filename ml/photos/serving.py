"""Photo -> 768 numbers, the way the server will do it: Pillow, numpy and onnxruntime.

No PyTorch and no transformers on the image side, so whatever is trained on these
numbers (stage 6) sees exactly what the server will see.
"""

from __future__ import annotations

import json

import numpy as np
import onnxruntime as ort
import photolab as lab
from PIL import Image, ImageOps

ONNX = lab.HERE / "onnx"
SETTINGS = json.loads((ONNX / "parity.json").read_text())
IMAGE_KIND = "fp32"  # stage 5: int8 photos dropped Recall@1 to 0.941
TEXT_KIND = "int8pc"  # stage 5: per-channel int8 queries, worst cosine 0.995


def preprocess(path) -> np.ndarray:
    """One photo -> a 3 x 224 x 224 array, exactly as the model was trained to see."""
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")  # phone photos carry a rotation tag
    height, width = SETTINGS["image"]["size"]
    image = image.resize((width, height), resample=SETTINGS["image"]["resample"])
    x = np.asarray(image, dtype=np.float32) * SETTINGS["image"]["rescale"]
    x = (x - np.array(SETTINGS["image"]["mean"], dtype=np.float32)) / np.array(
        SETTINGS["image"]["std"], dtype=np.float32
    )
    return x.transpose(2, 0, 1)


class ImageEncoder:
    def __init__(self, kind: str = IMAGE_KIND) -> None:
        self.session = ort.InferenceSession(
            str(ONNX / f"image-{kind}.onnx"), providers=["CPUExecutionProvider"]
        )

    def __call__(self, paths, batch: int = 16) -> np.ndarray:
        rows = []
        for start in range(0, len(paths), batch):
            pixels = np.stack([preprocess(p) for p in paths[start : start + batch]])
            rows.append(self.session.run(None, {"pixel_values": pixels})[0])
        return np.concatenate(rows)


class TextEncoder:
    """Lab only: the tokenizer comes from transformers here; the server has its own."""

    def __init__(self, kind: str = TEXT_KIND) -> None:
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(lab.MODEL_DIR)
        self.session = ort.InferenceSession(
            str(ONNX / f"text-{kind}.onnx"), providers=["CPUExecutionProvider"]
        )

    def __call__(self, texts) -> np.ndarray:
        ids = self.tokenizer(list(texts), padding="max_length", max_length=64, return_tensors="np")[
            "input_ids"
        ]
        return self.session.run(None, {"input_ids": ids.astype(np.int64)})[0]
