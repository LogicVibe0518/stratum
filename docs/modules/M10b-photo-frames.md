# M10b — The photo decides the crop

**Status:** written by Claude, 30 Sep 2026. **Spec schema 3 → 4**, with its upgrader
and tests. **New dependency: onnxruntime (MIT)** and **a committed model file: YuNet
face detector (MIT, 233 KB)**, both approved by Rushil on 30 Sep. No migration, no
auth or token code.

## Why

A photo rarely has the shape of its box, so part of it is cut. Until now that part
was always cut evenly from both sides, whatever was there:

- A 3:2 phone photo in a 4:5 post lost a sixth from each side. In a story it lost
  more than half.
- A tall photo in a 16:9 post lost its top and bottom. On a portrait, that is the top
  of the head.
- The headline landed wherever the design put it: over a face as often as over sky.

A designer's first move with a client's photo is to reframe it. Stratum now does
that for every photo, in every size, when it drafts the post.

## What it does

For each photo layer, in each size, it tries windows across the photo, plus a little
zoom when the photo has pixels to spare. It keeps the window with the lowest cost:

| cost | weight | why |
|---|---|---|
| any head outside the window | 30 | a cut head is the most visible mistake there is |
| any face under words, the logo or a panel | 12 | people look at faces first |
| the subject outside the window | 6 × strength | the photo was taken of something |
| the subject under something drawn | 2.5 × strength | half-hidden is better than gone |
| the subject jammed against the frame's edge | 3 × strength | looks cut even when it is not |
| busy photo under the words | 1.5 | words over brickwork are hard to read |
| moving the window from the centre | 0.3 | with nothing to gain, nothing changes |
| zooming in | 1.2 per unit | show as much of the photo as possible |

*Strength* (0 to 1) is how clearly one thing stands out. A flat or evenly busy photo
has strength near 0, so it is placed for its words alone and otherwise stays centred.

The chosen crop is stored in the poster: `ImageLayer.crop` for the poster's own size,
and `LayoutOverride.crop` for each other size. So the crop the client approved is
the crop that renders later, in the PNG, the SVG and the PowerPoint file for Canva.

**When no crop is good enough, a person is told.** Every version (draft, tweak,
quick change) is checked for two flags:

- `face_cut`: a quarter of a head outside the frame.
- `face_covered`: a third of a face under the words, the logo or a panel.

A square photo in a 4:5 post cannot move up or down, so a face under a headline at
the top stays there. The flag says to try a design with the words elsewhere. The
top-three work (M12b) can use the same costs to prefer those designs.

## How the photo is read (`photo/read.py`)

Once per photo file, cached:

- **Faces** come from YuNet (`photo/faces.py`), run with onnxruntime:
  - the image is letterboxed into the model's 640 × 640 input twice, at full and at
    half size, so a close-up face is found too;
  - hits are decoded as OpenCV's FaceDetectorYN does, and overlapping boxes are
    merged;
  - the face box is grown to the whole head (hair above), because the box stops at
    the eyebrows.
- **Salience** needs no model:
  - colour that differs from the photo's edges and from its average;
  - a little of the spectral residual (Hou and Zhang, 2007);
  - a soft weighting towards the centre.

  It is squared before use, so the one thing that stands out most outweighs a
  scatter of lesser ones.
- **Busyness** is fine texture, measured with the photo at 512 pixels and averaged
  into the grid. Measured on the small grid itself, brickwork blurs into flat grey
  and reads as calm.

**Why a face model at all.** The no-model salience was tried first on public-domain
test photos. On a portrait it picked the orange suit and the helmet, and left the
face out. A face is the one thing a crop must never cut, so it gets a detector
trained for exactly that.

## Where it runs

- `service.draft`: after the logo panel is fitted, before rendering.
- `service.tweak` and `service.quick`: only photos the edit **swapped** are framed
  again (`reframe`). A tweak to the words never moves the photo.
- `SwapAsset` (spec ops) clears the old photo's crop in every size, so nothing ever
  frames a new photo by where someone else stood.
- The CLI pipeline (`draft/pipeline.py`) frames each size it renders.
- `render_versions` adds `face_flags` to the per-size layout checks, merged per size
  like every other flag.
- The critic's busyness feature follows the crop window. Posters without a crop
  measure exactly as before, so critic v1 and its training data are unchanged.
  `edge_map` moved to `photo/read.py` and is re-exported unchanged.

## Old posters

- A schema-3 poster upgrades to 4 unchanged, with no crop anywhere, and renders byte
  for byte as before. A test checks the SVG still uses `xMidYMid slice`.
- A poster laid out from its template file (made before schema 3) gets a crop only
  in its own size when a photo is swapped. Its other sizes inherit it.

## Tests

`test_photo_frames.py` (31) and `packages/spec/tests/test_crop.py` (11), plus one new
migration test.

- **Geometry:**
  - no crop is the even cut;
  - the window stays inside the photo with the box's shape (Hypothesis);
  - the renderer shows the named half of a two-colour photo;
  - blur and crop together;
  - the PowerPoint file trims the same window.
- **The read:**
  - a drawn subject is found where it is;
  - flat and noisy photos have no subject;
  - brickwork is busy and sky is not.
- **The model:**
  - the committed file matches its SHA-256 and a changed copy is refused;
  - decoding is checked on a hand-built output;
  - duplicates are merged;
  - drawn scenes contain no face.
- **The choice:**
  - nothing to gain means centred;
  - the subject moves out from under the words, on either side;
  - the words go over the calm half;
  - a head is not cut when a crop can keep it;
  - a face moves out from under the words;
  - a small photo is never zoomed.
- **The poster:**
  - every size gets a crop and keeps its other settings;
  - `only` limits the work;
  - no read means no change;
  - template-file posters are framed in their own size;
  - the words cover their letters, not their box, and are read through a scrim.
- **Flags:** a face no crop can save is flagged, and a face the crop saved is not.
- **The product:** a draft stores crops in both sizes. "Use the next photo" frames
  the new photo and leaves every other layer as it was.

**By eye:** `make frame-sheet PHOTO=...` renders before and after pairs for six
designs in 4:5, 9:16 and 16:9. On public-domain test photos (not in the repository):

- a head cut at the top of 16:9 is whole;
- a face under a top headline in 16:9 moves below it;
- nothing else moves.

## Risks

- **The salience is a heuristic.** On a busy scene it can prefer a cluster of lights
  to the main object. The cost of moving is small, so a wrong subject moves the frame
  a little, not wildly. Faces do not depend on it.
- **YuNet misses** faces in profile, small faces, and faces over about 300 pixels at
  its input. Two passes cover close-ups. A missed face is framed like any subject.
- **Model licence:** YuNet is MIT. Its authors trained it on WIDER FACE, whose own
  terms are for research use. Using weights under their own licence is common
  practice, but it is a known grey area. It is recorded here and belongs in the ADR
  (Rushil's decision).
- **Schema 4:** an old worker reading a schema-4 poster refuses it as "newer". Deploy
  the API and the worker together (Railway does), and stop any laptop worker first.
- **Tweaks do not re-frame** unless the photo changes. A tweak that moves the headline
  over a face keeps the crop and gets a `face_covered` flag instead.

## Five things to be able to say

1. Every photo is framed per size by a cost function: faces first, then the subject,
   then words over calm areas, with small costs for moving or zooming. With nothing
   to gain it stays centred.
2. Faces come from a 233 KB MIT model (YuNet) run with onnxruntime. It is committed
   with its SHA-256 checked, because numpy salience missed a face on a real portrait.
3. The crop is data: spec schema 4, one per size, the same window in PNG, SVG and
   PowerPoint. Old posters upgrade unchanged and render byte for byte as before.
4. Only a swapped photo is re-framed, and swapping clears the old crop. A tweak to
   the words never moves the photo.
5. When no crop can save a face, the poster says so (`face_cut`, `face_covered`), on
   every version.

## For the ADR (Rushil writes it)

- **Crops are chosen at draft time and stored,** not computed at render time.
  Predictable, editable later, approved means approved.
- **First pretrained model in the product.** Committed rather than downloaded: works
  offline, reproducible, checked by hash. Note the WIDER FACE training data.
- **onnxruntime as the model runtime,** not PyTorch. About 20 MB, not 800 MB, and CPU
  only.
- **No-model salience for subjects for now.** A subject-mask model (BiRefNet, MIT)
  comes with the depth design and portrait blur.
