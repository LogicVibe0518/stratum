"""Builds the tiny stand-in SigLIP the photo-understanding tests run on (M17a).

Same shapes and the same file layout as the real model folder, with random weights:
it knows nothing about photos, so tests check the plumbing (tokens, preprocessing,
ONNX, storage, search, the chat action), never quality. Quality is measured in the
lab (`ml/photos`, stage 4 onwards) on the real model.

Run from this folder with the photo lab's environment (PyTorch, transformers):

    uv run --python 3.12 --no-project --with torch==2.14.1 --with transformers==5.18.0 \
        --with sentencepiece==0.2.2 --with protobuf==7.34.1 --with onnx==1.23.2 \
        --with onnxscript==0.7.2 --with onnxruntime==1.25.0 --with scikit-learn==1.8.0 \
        --with pillow==12.2.0 python make.py

Everything is seeded, so a rebuild gives the same tokens; ONNX bytes may differ
between library versions, which is why lock.json is rebuilt with them.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

import numpy as np
import sentencepiece as spm
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from PIL import Image, ImageDraw
from sklearn.linear_model import LogisticRegression
from transformers import (
    SiglipConfig,
    SiglipImageProcessor,
    SiglipModel,
    SiglipProcessor,
    SiglipTokenizer,
)

HERE = Path(__file__).resolve().parent
CLASSES = ("people_posing", "people_working", "place", "object", "vehicle", "graphic")
TEMPLATE = "a photo of {}."
MAX_LENGTH = 64

# Queries the parity file records, and strings that are hard for a tokenizer:
# punctuation, capitals, runs of spaces, digits, accents, the sentencepiece marker.
QUERIES = [
    "a man in a red shirt",
    "two people, one in a red shirt and one in a grey tee",
    "the van",
    "a clean kitchen",
    "the team in front of the van!",
    "Before and after: the bathroom",
]
HARD = [
    "",
    "   ",
    "A Photo Of The VAN",
    "roof-tiles & gutters (2 storeys)",
    "café crème",
    "▁already marked",
    "tabs\tand\nnew lines",
    "one " * 80,
    "50% off this week",
    "it's the plumber's van",
]


def tokenizer() -> SiglipTokenizer:
    words = [
        "a",
        "photo",
        "of",
        "the",
        "man",
        "woman",
        "people",
        "team",
        "in",
        "front",
        "red",
        "grey",
        "blue",
        "shirt",
        "tee",
        "van",
        "truck",
        "car",
        "roof",
        "kitchen",
        "bathroom",
        "room",
        "clean",
        "before",
        "after",
        "finished",
        "ladder",
        "tools",
        "plumber",
        "cleaner",
        "garden",
        "house",
        "one",
        "two",
        "three",
        "and",
        "with",
        "on",
        "at",
        "off",
        "this",
        "week",
    ]
    rng = random.Random(0)
    corpus = HERE / "corpus.tmp"
    corpus.write_text("\n".join(" ".join(rng.choice(words) for _ in range(9)) for _ in range(600)))
    spm.SentencePieceTrainer.train(
        input=str(corpus),
        model_prefix=str(HERE / "spiece"),
        vocab_size=96,
        model_type="unigram",
        hard_vocab_limit=False,
        pad_id=-1,
        eos_id=1,
        unk_id=2,
        bos_id=-1,
    )
    corpus.unlink()
    (HERE / "spiece.vocab").unlink()
    return SiglipTokenizer(vocab_file=str(HERE / "spiece.model"))


def photos() -> list[Path]:
    """Four small photos the parity file records."""
    folder = HERE / "photos"
    folder.mkdir(exist_ok=True)
    out = []
    for name, ground, mark in (
        ("red-disc.png", (230, 230, 225), (200, 30, 30)),
        ("blue-square.png", (40, 40, 60), (60, 120, 220)),
        ("green-stripes.png", (30, 120, 40), (200, 220, 90)),
        ("portrait-grey.png", (128, 128, 128), (20, 20, 20)),
    ):
        size = (60, 80) if name.startswith("portrait") else (80, 60)
        image = Image.new("RGB", size, ground)
        draw = ImageDraw.Draw(image)
        if "disc" in name:
            draw.ellipse([20, 10, 60, 50], fill=mark)
        elif "square" in name:
            draw.rectangle([25, 15, 55, 45], fill=mark)
        elif "stripes" in name:
            for x in range(0, 80, 10):
                draw.rectangle([x, 0, x + 4, 60], fill=mark)
        else:
            draw.rectangle([20, 20, 40, 60], fill=mark)
        image.save(folder / name)
        out.append(folder / name)
    return out


class ImageTower(torch.nn.Module):
    def __init__(self, model: SiglipModel) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        out = self.model.get_image_features(pixel_values=pixel_values)
        return torch.nn.functional.normalize(getattr(out, "pooler_output", out), dim=-1)


class TextTower(torch.nn.Module):
    def __init__(self, model: SiglipModel) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        out = self.model.get_text_features(input_ids=input_ids)
        return torch.nn.functional.normalize(getattr(out, "pooler_output", out), dim=-1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    torch.manual_seed(0)
    torch.set_grad_enabled(False)
    tok = tokenizer()
    processor = SiglipProcessor(image_processor=SiglipImageProcessor(), tokenizer=tok)
    config = SiglipConfig(
        text_config={
            "hidden_size": 32,
            "intermediate_size": 64,
            "num_attention_heads": 2,
            "num_hidden_layers": 2,
            "vocab_size": 112,
            "max_position_embeddings": MAX_LENGTH,
            "pad_token_id": 1,
            "eos_token_id": 1,
            "bos_token_id": 1,
        },
        vision_config={
            "hidden_size": 32,
            "intermediate_size": 64,
            "num_attention_heads": 2,
            "num_hidden_layers": 2,
            "image_size": 224,
            "patch_size": 16,
        },
    )
    model = SiglipModel(config).eval()
    model.config._attn_implementation = "eager"
    model.logit_scale.fill_(math.log(10.0))
    model.logit_bias.fill_(-2.0)

    size = processor.image_processor.size
    pixels = torch.zeros(1, 3, size["height"], size["width"])
    ids = tok(
        ["a photo of a van"], padding="max_length", max_length=MAX_LENGTH, return_tensors="pt"
    )
    for name, tower, example, input_name in (
        ("image", ImageTower(model), pixels, "pixel_values"),
        ("text", TextTower(model), ids["input_ids"], "input_ids"),
    ):
        fp32 = HERE / f"{name}-fp32.onnx"
        torch.onnx.export(
            tower,
            (example,),
            fp32,
            input_names=[input_name],
            output_names=["embedding"],
            dynamic_axes={input_name: {0: "batch"}, "embedding": {0: "batch"}},
            opset_version=17,
            dynamo=False,
        )
        quantize_dynamic(fp32, HERE / f"{name}.onnx", weight_type=QuantType.QInt8)
        fp32.unlink()

    image_config = processor.image_processor
    settings = {
        "model": "siglip-tiny-test",
        "logit_scale": float(model.logit_scale.exp()),
        "logit_bias": float(model.logit_bias),
        "image": {
            "size": [size["height"], size["width"]],
            "resample": int(image_config.resample),
            "rescale": float(image_config.rescale_factor),
            "mean": [float(v) for v in image_config.image_mean],
            "std": [float(v) for v in image_config.image_std],
        },
        "text": {"max_length": MAX_LENGTH, "template": TEMPLATE},
    }
    (HERE / "settings.json").write_text(json.dumps(settings, indent=1) + "\n")

    texts = [TEMPLATE.format(q) for q in QUERIES]
    query_ids = tok(texts, padding="max_length", max_length=MAX_LENGTH, return_tensors="pt")[
        "input_ids"
    ]
    query_vectors = TextTower(model)(query_ids).numpy()
    files = photos()
    images = [Image.open(p).convert("RGB") for p in files]
    photo_vectors = ImageTower(model)(
        processor(images=images, return_tensors="pt")["pixel_values"]
    ).numpy()
    hard = []
    for text in HARD:
        row = tok([text], padding="max_length", max_length=MAX_LENGTH)["input_ids"][0]
        hard.append({"text": text, "input_ids": row})
    parity = {
        "queries": [
            {"text": t, "input_ids": i.tolist(), "embedding": v.round(6).tolist()}
            for t, i, v in zip(texts, query_ids, query_vectors, strict=True)
        ],
        "photos": [
            {"file": f"photos/{p.name}", "embedding": v.round(6).tolist()}
            for p, v in zip(files, photo_vectors, strict=True)
        ],
        "tokens": hard,
    }
    (HERE / "parity.json").write_text(json.dumps(parity) + "\n")

    # A probe over six colour families, so its answers are stable enough to test.
    rng = np.random.default_rng(0)
    bases = rng.integers(30, 225, size=(len(CLASSES), 3))
    train_images, labels = [], []
    for k, base in enumerate(bases):
        for _ in range(12):
            colour = tuple(int(c) for c in np.clip(base + rng.integers(-25, 25, 3), 0, 255))
            train_images.append(Image.new("RGB", (64, 64), colour))
            labels.append(k)
    vectors = ImageTower(model)(
        processor(images=train_images, return_tensors="pt")["pixel_values"]
    ).numpy()
    probe = LogisticRegression(C=10.0, max_iter=5000).fit(vectors, labels)
    types = {
        "classes": list(CLASSES),
        "coef": probe.coef_.round(7).tolist(),
        "intercept": probe.intercept_.round(7).tolist(),
        "line": 0.5,
        "embedding": settings["model"],
    }
    (HERE / "types.json").write_text(json.dumps(types) + "\n")

    lock = {
        "model": settings["model"],
        "revision": "test",
        "licence": "generated for tests, no rights reserved",
        "files": {
            name: sha256(HERE / name)
            for name in ("image.onnx", "text.onnx", "spiece.model", "settings.json", "types.json")
        },
    }
    (HERE / "lock.json").write_text(json.dumps(lock, indent=1) + "\n")
    print({name: (HERE / name).stat().st_size for name in lock["files"]})


if __name__ == "__main__":
    main()
