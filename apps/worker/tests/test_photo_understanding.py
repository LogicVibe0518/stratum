"""M17a: SigLIP in the worker - files checked, tokens and numbers as the lab made them.

Run on a tiny stand-in with random weights (fixtures/siglip-tiny, built by make.py):
the same file layout and code path as the real model, so these test the plumbing,
never photo quality. Quality is measured in the lab on the real model.

What a reviewer should be able to repeat back:
- every model file is checked against the lock before the first use, and a model
  missing a file or with a changed file refuses to load, saying which;
- the tokenizer gives exactly the token numbers transformers gave in the lab, for
  plain queries and for hard strings (capitals, punctuation, accents, tabs, a text
  longer than the model reads - cut, keeping the end-of-text token);
- photo and text vectors match the lab's PyTorch reference to the int8 tolerance;
- a vector kept with its photo as text (float16) comes back within 0.001;
- the photo-type probe answers in its own classes and says nothing below its line,
  and a probe trained on another model's numbers is refused;
- this build ships without a lock, so `installed()` is None and nothing else changes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from stratum_worker.photo import understand
from stratum_worker.photo.understand import PhotoModelError, Probe, load, pack, unpack

TINY = Path(__file__).resolve().parent / "fixtures" / "siglip-tiny"
PARITY = json.loads((TINY / "parity.json").read_text())


@pytest.fixture(scope="module")
def model() -> understand.OnnxPhotoModel:
    return load(TINY, TINY / "lock.json")


def test_this_build_ships_without_a_photo_model() -> None:
    assert not understand.LOCK.exists()
    assert understand.installed() is None


def test_a_changed_file_is_refused_by_name(tmp_path: Path) -> None:
    copy = tmp_path / "siglip"
    shutil.copytree(TINY, copy)
    with (copy / "text.onnx").open("ab") as handle:
        handle.write(b"\0")
    with pytest.raises(PhotoModelError, match=r"text\.onnx is not the file"):
        load(copy, TINY / "lock.json")
    (copy / "text.onnx").unlink()
    with pytest.raises(PhotoModelError, match=r"missing text\.onnx"):
        load(copy, TINY / "lock.json")


@pytest.mark.parametrize(
    "row", PARITY["tokens"] + PARITY["queries"], ids=lambda r: repr(r["text"][:20])
)
def test_tokens_match_the_lab_exactly(model: understand.OnnxPhotoModel, row: dict) -> None:
    ours = model.tokenizer.ids(row["text"])
    theirs = row["input_ids"]
    assert len(ours) == 64
    if len(theirs) > 64:  # transformers does not cut; the model reads 64 positions
        assert ours == [*theirs[:63], model.tokenizer.eos]
    else:
        assert ours == theirs


def test_text_vectors_match_the_reference(model: understand.OnnxPhotoModel) -> None:
    texts = [row["text"] for row in PARITY["queries"]]
    reference = np.asarray([row["embedding"] for row in PARITY["queries"]], dtype=np.float32)
    ours = model.embed_texts(texts)
    assert np.allclose(np.linalg.norm(ours, axis=1), 1.0, atol=1e-3)
    assert float((ours * reference).sum(axis=1).min()) >= 0.99


def test_photo_vectors_match_the_reference(model: understand.OnnxPhotoModel) -> None:
    images = [Image.open(TINY / row["file"]) for row in PARITY["photos"]]
    reference = np.asarray([row["embedding"] for row in PARITY["photos"]], dtype=np.float32)
    ours = model.embed_photos(images)
    assert ours.shape == reference.shape
    assert float((ours * reference).sum(axis=1).min()) >= 0.98


def test_a_phone_photo_is_turned_upright_first(
    model: understand.OnnxPhotoModel, tmp_path: Path
) -> None:
    upright = Image.new("RGB", (60, 80), "white")
    upright.paste((0, 0, 0), (0, 0, 20, 20))  # a mark in one corner only
    sideways = upright.rotate(90, expand=True)
    exif = sideways.getexif()
    exif[0x0112] = 6  # "rotate 90 clockwise to view"
    sideways.save(tmp_path / "sideways.png", exif=exif)
    turned = model.pixels(Image.open(tmp_path / "sideways.png"))
    assert np.allclose(turned, model.pixels(upright), atol=1e-6)
    untagged = upright.rotate(90, expand=True)  # the same pixels, no rotation tag
    assert not np.allclose(model.pixels(untagged), model.pixels(upright), atol=0.2)


def test_a_query_is_worded_like_a_caption(model: understand.OnnxPhotoModel) -> None:
    said = model.embed_query("the van")
    assert np.allclose(said, model.embed_texts(["a photo of the van."])[0], atol=1e-6)
    assert model.embed_query("the van") is said  # asked twice, worked out once


def test_probability_is_sigmoid_of_scaled_cosine(model: understand.OnnxPhotoModel) -> None:
    scale, bias = model.settings.logit_scale, model.settings.logit_bias
    cos = np.array([-0.2, 0.0, 0.3])
    assert np.allclose(model.probability(cos), 1 / (1 + np.exp(-(cos * scale + bias))))


def test_a_kept_vector_comes_back_within_a_thousandth(model: understand.OnnxPhotoModel) -> None:
    vector = model.embed_texts(["a photo of a clean kitchen."])[0]
    text = pack(vector)
    assert isinstance(text, str) and len(text) < 3 * len(vector)
    assert float(unpack(text) @ vector) >= 0.999


def test_photo_types_speak_the_probe_classes(model: understand.OnnxPhotoModel) -> None:
    vectors = model.embed_photos([Image.new("RGB", (64, 64), (200, 30, 30))] * 2)
    answers = model.photo_types(vectors)
    assert len(answers) == 2
    for label, probability in answers:
        assert (
            label is None
            or label in understand.Probe.read(TINY / "types.json", "siglip-tiny-test").classes
        )
        assert 0.0 <= probability <= 1.0


def test_below_its_line_the_probe_says_nothing() -> None:
    probe = Probe.read(TINY / "types.json", "siglip-tiny-test")
    unsure = Probe(probe.classes, probe.weights * 0.0, probe.bias * 0.0, line=0.5)
    vector = np.ones((1, probe.weights.shape[1]), dtype=np.float32)
    assert unsure(vector) == [(None, round(1 / len(probe.classes), 4))]


def test_a_probe_for_another_model_is_refused() -> None:
    with pytest.raises(PhotoModelError, match="Train the probe again"):
        Probe.read(TINY / "types.json", "siglip-base-patch16-224@other")
