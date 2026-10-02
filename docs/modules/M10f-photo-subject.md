# M10f — The photo decides the design: the subject's outline, a photo check, a photo picker

**Status:** written by Claude, 2 Oct 2026.

**Review flags:**
- **A new model file, committed:** `photo/model/u2netp.onnx`, 4.6 MB, Apache-2.0
  (`LICENSE-u2net.txt`). The large-file hook (800 KB) gets an exclude for that one
  path. The SHA-256 is checked in code before first use.
- No migration. No spec change. No RLS, auth or token change. No new dependency
  (onnxruntime is already used for faces).
- Uploading a photo now runs the model inside the upload request (about 0.5 s, once
  per photo). See Risks.

## Why

Every crop, and the ranking of designs that uses the crop's cost, depended on a
no-model salience: what differs from the photo's edges in colour and detail. It
works for a red disc on a wall. In front of a busy street it fails. On a drawn test
scene (a person in an orange jacket in front of 120 coloured signs) only **3%** of
the salience was on the person. So the crop went for the signs: with the words on
the left, the person ended up under the words, or outside the frame altogether.

A designer looks at a photo and sees its subject first. This module gives Stratum
the same first look, and uses it in three places: the crop, the upload check, and
which photo a post gets.

## What changed

### 1. The subject's outline (`photo/subject.py`)

**U²-Net small** ("u2netp", Qin et al. 2020) is a salient-object model: trained to
outline the thing a photo is of, on DUTS-TR (10,553 photos with hand-drawn masks).
- 4.6 MB, the ONNX export rembg distributes, unchanged. Apache-2.0.
- About 0.45 s per photo on one core. One thread per run, as for the face model.
- The photo is squashed to 320 × 320 (the shape it was trained on) and normalised
  with the ImageNet mean and deviation.
- **The answer is not stretched.** rembg stretches every mask to fill 0 to 1. On a
  photo of grass the model's highest probability is 0.27; stretched, that becomes a
  confident "subject". So the raw probability is used.
- **Answers are kept per photo**, keyed by the pixels the model sees (64 photos,
  about 6.5 MB). The upload check and every later draft of that photo share one run.

A subject is **clear** when both hold:

| rule | why |
|---|---|
| it covers 2% to 75% of the photo | smaller is a speck; larger is the whole photo, with nothing beside it for words |
| mean probability inside minus outside ≥ 0.5 | the model is decisive, not guessing |

Measured on public-domain and CC0 photos (scikit-image's samples), not committed:

| photo | cover | clear |
|---|---|---|
| astronaut portrait | 55% | yes |
| coffee cup | 48% | yes |
| cat close-up | 68% | yes (and its head runs off the top) |
| rocket on the launch pad | 2.2% | yes |
| horse silhouette | 34% | yes |
| brickwork | 0.7% | no |
| grass | 0% | no |

### 2. The read uses it (`photo/read.py`)

When the photo has a clear subject:
- **Salience** becomes 75% the subject's outline and 25% the colour salience. So a
  bright sign beside the person still counts a little.
- **Strength** (how sure the read is that there is one subject to protect) becomes at
  least the outline's separation, about 0.9 to 1.0.

With no clear subject, nothing changes: the read is exactly what it was.

**The effect, on the busy street:**
- the person's share of the salience: 3% → 74%;
- strength: 0.38 → 0.98;
- with words over the left of a 4:5 post, the crop puts the person in the right half
  of the poster, in frame. Before, they landed under the words (person at 0.3) or
  outside the frame (person at 0.7).

**Why this is "the photo decides the design":** the top three ranks every design by
its critic score minus how badly the photo sits in it (`framing_cost`, M12b). That
cost now knows where the subject is. A design whose words must cover the person
loses to one that leaves them clear. Tested through the real ranking: every design
shown keeps the person clear (framing cost below 3); with colour alone, one design
shown covered them (above 5).

### 3. The photo check at upload (`photo/check.py`, `service.store_image`)

Every uploaded photo gets four more numbers in its `quality`:
- `subject_cover` and `subject_separation`: the subject as found;
- `subject_top`: how much of the top edge the subject touches;
- `calm_room`: how much of the photo is calm and off the subject. That is where words
  can sit on the photo itself.

One new flag, advice only: **`subject_cut_off`**. A clear subject touches more than
15% of the top edge: a head, a roof, the top of a cake. No crop can give it room
above. The client page shows it as a badge, like "too blurry". Logos are not checked.

### 4. The photo picker (`service.pick_photos`)

Photos were used sharpest first. Now best first, by `fitness`:

```
0.45 × sharp enough  +  0.30 × a clear subject  +  0.25 × room for words
− 0.15 if the subject is cut off at the top
```

- **Sharpness counts in full from 200** (twice the blur threshold). So among sharp
  photos, the one with a clear subject and room for words comes first.
- **A photo with no clear subject** (a wide shot of a finished kitchen) scores 0.4 for
  its subject. It is still a good photo.
- **A photo uploaded before this check** scores the middle (0.5) for what was not
  measured.
- **Ties** go to the sharper photo, then the newest, as before.
- **The rule that weak photos come after good ones is unchanged.**

## Tests

`test_photo_subject.py` (29):
- **The model:**
  - the file is the one the code was written for;
  - a swapped file is refused;
  - a missing file says where it should be;
  - the same photo runs the model once.
- **Finding the subject:**
  - a disc is found where it is, at the right size;
  - a person in front of a busy street is found;
  - a flat, noisy or patterned photo has no clear subject, and the raw answer never
    reaches "inside";
  - what counts as clear (a speck, the whole photo, an unsure mask);
  - the box and top edge are measured;
  - the outline fits any grid.
- **The read:**
  - with no subject, it is exactly as before;
  - the outline carries the salience and the certainty;
  - on the busy street, the person stays in frame and clear of the words (two
    positions, and colour alone fails both);
  - the designs shown keep the person clear, through the real ranking.
- **The upload check:**
  - a cut-off subject is flagged;
  - a subject with room around it is not;
  - a busy photo has no room for words.
- **The ranking:**
  - sharp beats soft;
  - a clear subject with room comes first;
  - being cut off costs;
  - an unmeasured photo scores the middle.
- **Through the service:**
  - an upload records its subject and flags a cut-off one, and a logo gets no subject
    numbers;
  - the picker prefers an older photo with a clear subject to a newer, sharper
    pattern.

Mutation-checked: picking by sharpness alone fails the picker test, and a read that
ignores the outline fails the three busy-street tests. All earlier tests pass
unchanged. Their drawn scenes either have no clear subject (the read is identical) or
a clear one where the outline agrees with the colour salience.

## Risks

- **The model runs in the upload request.** About 0.5 s per photo, plus about 0.1 s
  to load the model the first time. The API already normalises, hashes and uploads
  in that request. If uploads in bulk feel slow, move the check to a job (the numbers
  are only read when a post is drafted).
- **Masks miss fine detail** (hair, thin tools, wires). That does not matter here:
  the outline is averaged into a 64-cell grid for salience. It will matter for the
  depth design (0020b), which draws through the mask. That is why depth is its own
  patch, with its own checks.
- **The weights were trained by their authors on DUTS-TR**, a public research
  benchmark. Stratum uses the released weights unchanged under their Apache-2.0
  licence, the same position as the YuNet face model (MIT weights, trained on WIDER
  FACE). Stratum trains nothing on that data. If Rushil wants the stricter rule
  ("no model trained on research-only data, even frozen"), both models need
  replacing, and that is a written decision.
- **The thresholds are judgement calls:**
  - 2% to 75% cover;
  - separation 0.5;
  - 15% of the top edge;
  - the four weights.
  They are constants in two files. The Taste Lab and the test phase (T1) will show if
  they need moving.
- **Photos uploaded before this patch have no subject numbers.** The picker scores
  them in the middle. Their crops still use the outline, because reading a photo
  for a draft runs the model on the photo itself.

## Five things to be able to say

1. A salient-object model (U²-Net small, 4.6 MB, Apache-2.0) outlines each photo's
   subject. Its raw probability is used, never stretched, so a photo with no subject
   has none.
2. When there is one clear subject, it carries 75% of the salience. The crop, and the
   ranking of designs that uses the crop's cost, keep the person clear of the words,
   even in front of a busy street where colour alone failed.
3. Every uploaded photo is checked: how big and how clear its subject is, how much
   calm room is left for words, and a flag when the subject runs off the top.
4. The photo picker ranks by sharp enough, then a clear subject, then room for words,
   instead of sharpness alone.
5. The model file is committed with its licence and checked by SHA-256 before use, so
   a draft never waits on a download or trusts a swapped file.

## For the ADR (Rushil writes it)

- **A pretrained, frozen salient-object model** rather than training our own. There
  are no labelled photos of our clients, and the outline is a perception task the
  public model already does well.
- **Commit the weights** (4.6 MB, with a hook exclude) rather than download them at
  build time. Same reason as the face model: no third-party server in the draft path.
- **Mix the outline into the existing salience** rather than replace it. So the
  framing code, its tests and its cost scale stay the same, and a photo with no clear
  subject is read exactly as before.
