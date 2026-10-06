# M17a — Photos by what is in them: the plumbing for SigLIP

**Status:** written by Claude, 7 Oct 2026. The model itself is built and measured by
Rushil in the photo lab (`ml/photos`, his own PR, guided). This module is everything
around it, tested on a tiny stand-in with the same files and code path. **This build
ships without the model**: until the lab's files are stitched in (below), every
change here is switched off and Stratum behaves exactly as before.

**Review flags:**
- **New dependency:** `sentencepiece` (Apache-2.0), SigLIP's tokenizer. Small wheel,
  no system libraries.
- **New committed test fixture:** `apps/worker/tests/fixtures/siglip-tiny/`, about
  0.5 MB: a SigLIP with random weights built by `make.py` (ours, no rights reserved),
  four drawn 80 px pictures, and the tokens transformers gave for them. No real photo
  and no real model.
- No migration, no RLS, auth or token change, no spec change. The photo vectors live
  in the existing `assets.quality` jsonb (see Decisions).
- **Prompt edit.parse v8** (pinned). The chat eval's default baseline becomes v7
  (`ml/chat/edit_parse_v7.md`); `--baseline 6` still runs the M10i comparison.
- The control panel may now send `swap_asset`, checked against the client's own
  photos (a photo from another client of the agency is refused).

## Why

Live, 6 Oct, Sparkle Squad: "include image of person also like the cleaner along
with this photo" switched to a two-photo design and added another **room** photo, then
said it needed the cleaner's photo. Stratum knew where faces and subjects are, how
busy a photo is and how sharp, but not **what** a photo shows. So "the one with the
van" could not be found, and a draft could not prefer the photo a brief describes.

## What changed

### 1. The model in the worker (`photo/understand.py`)

SigLIP base (google/siglip-base-patch16-224, Apache-2.0), exported to ONNX and int8 in
the lab. Two towers: photo → vector, sentence → vector, both length 1; SigLIP's own
formula turns their cosine into a probability that the sentence describes the photo.

- **Files are checked before first use** against SHA-256 values committed with the
  code (`photo/model/siglip.lock.json`, added at stitching). A missing or changed file
  refuses to load and says which. A probe trained on another model's numbers is
  refused.
- **The tokenizer is transformers' SiglipTokenizer, step for step** (lowercase,
  punctuation removed, spaces collapsed, an end token, padded to 64 with it), on
  sentencepiece alone. One deliberate difference: a text over 64 tokens is cut, keeping
  the end token (transformers does not cut, and the model has 64 positions).
- **Preprocessing** from the lab's settings: phone rotation applied, resized to
  224 × 224 with the model's resampling, scaled to about −1..1.
- **The photo-type probe** from the lab (stage 6): a softmax over one linear layer,
  run in numpy; below its line it says nothing rather than guess.
- No lock in the repo → `installed()` is None → nothing below runs.

### 2. Each photo read once, its vector kept with it (`app/photo_search.py`)

The first draft or tweak that needs a client's photos reads the ones not read yet and
writes four keys into that photo's `quality`: `embedding` (float16, base64: 2 KB for
768 numbers), `seen_by` (the model's id), `photo_type`, `photo_type_p`. A vector made
by another model is made again: changing the model needs no migration and no clean-up.
The upload request never loads the model: uploads stay as fast as today, and the API
process stays small.

### 3. The chat: `use_photo` (`edit/actions.py`, `app/service.py`, prompt v8)

`{"op": "use_photo", "description": "a man in a red shirt", "layer": null, "add": false}`

The chat model says **what to look for**; the photo model finds it. A model never
names a file. Then:

| what the search finds | what happens |
|---|---|
| the best match is at or above the model's line | it is swapped into the photo named, or the main photo; the note says "Used the photo that best matches ..." |
| nothing at the line | **nothing changes**; the designer is shown the closest two (`photos` and `layer` in the answer) and can click one: a panel `swap_asset`, no model |
| `add: true` with a restyle | the restyle's extra photo space gets the photo asked for, not the next in line (the live failure) |
| `add: true` without room | said: "This design has room for 1 photo. Say 'two photos' ..." |
| the photo is already there | said, no new version |
| no model in this build | "Finding a photo by what is in it is not switched on yet ..." |

The chat model is also told the client's photos by kind: `<photos>` "12 photos: 4
people working, 3 place, ...".

### 4. Drafts choose with it (`service.pick_photos`)

- A photo the model is sure is a **graphic** (flyer, screenshot, logo) goes last: it is
  used only when nothing else is left.
- When the post's photo note (else its brief) clearly describes one photo, that photo
  comes first. "Clearly" is the same line as the chat's, so an unrelated brief changes
  nothing.

### 5. The asset list says each photo's kind (`GET /api/clients/{id}/assets`)

`photo_type` per row (null until read). The vector never leaves the server. The page
shows it in the Cursor brief that follows (C12).

## Decisions

- **Vectors in `assets.quality`, not a new table.** No migration, no new RLS: the
  vector has exactly the photo row's tenancy. A client has tens to hundreds of photos,
  so the search is one matrix product in memory; at hundreds of thousands, a
  `photo_vectors` table with pgvector would earn its keep (and would need the usual
  line-by-line RLS review).
- **float16.** Half precision moves a cosine by about 0.001, less than int8 already
  does, and halves the row size.
- **Lazy, in the worker.** Reading photos at upload would put a 100 MB model in the
  API. The first draft for a client pays about 0.1 s per photo, once.
- **The line comes from the lab** (leave-one-client-out, stage 4), shipped in
  `settings.json`. Below it Stratum asks; it never swaps on a guess.

## Stitching the lab's files in (when stage 8 is done)

1. From `ml/photos/onnx`: `image-int8.onnx` → `image.onnx`, `text-int8.onnx` →
   `text.onnx` (or the fp32 files if stage 5 said so), `spiece.model`; `types.json`
   from `ml/photos`.
2. `settings.json` from `parity.json`'s settings part, with `model` set to
   `siglip-base-patch16-224@<revision 7>-<int8|fp32>` and `line` from stage 4's honest
   threshold. `types.json` gets the same `model` in `embedding`.
3. A parity test on the real files against `parity.json`'s **queries** (tokens and
   text vectors; the photo vectors come from client photos and stay out of git).
4. `siglip.lock.json` with the five SHA-256 values; the files published at a pinned
   location (Rushil's public Hugging Face repo, with SigLIP's Apache-2.0 licence and
   a note of the changes) and downloaded at build time, checked against the lock.
5. CI downloads them too, or runs with the tiny fixture via `STRATUM_SIGLIP_DIR`.

## Not in this module

- The page: photo kinds on the Photos tab, the closest-two picker with "Use this
  photo" (Cursor brief C12).
- Reading photos in the background right after upload (a queue job), so the first
  draft does not pay for it.
- Search across more than a client's own photos; text inside photos (a graphic's
  words).

## Tests

- `test_photo_understanding.py` (26, 16 of them token cases): the lock check, tokens identical to
  transformers for queries and hard strings, photo and text vectors within int8
  tolerance of the PyTorch reference, phone rotation, the caption template, the
  probability formula, the kept vector's precision, the probe's classes and line, a
  probe for another model refused, and this build without a model.
- `test_photo_search_service.py` (9), with a stand-in model that sees only colour:
  found and swapped, vectors kept and reused, no clear match asks with the closest
  two and the panel swap works, another client's photo refused, "include it too"
  restyles with the photo asked for, "add" without room says so, the draft takes the
  photo its note describes, a graphic goes last, no model says so, the chat model is
  told the photo kinds.
- Chat eval: 10 new cases (category `photos`), including the live failure.
- The feature walk has a row for `swap_asset` ("another photo"), since the panel may
  now send it.

## Five things to be able to say

1. A designer can ask for a photo by what is in it. The chat model only writes the
   description; SigLIP, run on our own CPU, finds the photo; a model never names a
   file.
2. When nothing clearly matches, Stratum changes nothing and shows the two closest
   photos. The line between "swap" and "ask" was chosen in the lab on held-out
   clients, not guessed.
3. Each photo is read once per model and its vector kept with the photo row, as 2 KB
   of text. No migration, same tenancy as the photo, re-made if the model changes.
4. Every model file is checked against a committed SHA-256, and the tokenizer is
   tested token for token against the reference implementation.
5. Until the real files are stitched in, the build ships without the model and
   behaves exactly as before: the whole module is switched off by one missing file.
