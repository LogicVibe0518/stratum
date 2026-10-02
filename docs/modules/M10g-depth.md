# M10g — Depth: the headline behind the person, and a cover design where it happens

**Status:** written by Claude, 3 Oct 2026. It builds on M10f (the subject's outline).

**Review flags:**
- **Spec schema 6:** `ImageLayer.subject` (a matte asset) and `TextLayer.behind` (a photo
  layer id). The upgrader and tests are included. Older posters upgrade unchanged.
- **A new design family, `cover`:** 16 designs. The ranking, the Taste Lab and restyles
  see it like any other family.
- **A new kind of stored file:** the subject matte. It is a grey PNG in storage beside
  the photo (`{agency}/assets/{sha}.png`), with no asset row.
- No migration. No RLS, auth or token change. No new dependency.

## Why

When a person's head rises into the headline, letters printed over the head look like
a mistake. With the head in front of the letters, the same picture looks intended:
it is how magazine covers are made. M10f already outlines the subject. This module
uses that outline to put the person in front of the words, and only where the words
can still be read.

## What changed

### 1. The spec (schema 6)

- **`ImageLayer.subject`:** the photo's subject as a matte. Content-addressed, made
  from this photo only.
- **`TextLayer.behind`:** the id of a photo drawn below this text. That photo's subject
  comes in front of the letters.
- **Validation:** `behind` must name an image layer that is drawn below the text.
- **`PosterSpec.assets()`:** every asset a poster draws, mattes included. Seven places
  that each collected assets on their own now call it.
- **Swapping the photo** (`SwapAsset`) drops its matte and brings the words back in
  front. The matte belonged to the old photo.
- **Pan, zoom and moves** keep the depth. The matte follows the photo's own crop.

### 2. Drawing it (`render/svg.py`, `export/pptx.py`)

**The renderer** draws the photo a second time, right after the headline, through the
matte:
- same box, crop, blur, dimming, opacity and rotation;
- the matte framed with the photo's own geometry.

Outside the subject the matte is black, so nothing changes there. On the subject, the
person covers the letters and the band they sit on. Tested pixel by pixel:
- on the subject, the picture equals the photo alone;
- everywhere else, it equals the poster with the words in front.

**When depth does nothing:** if the matte is missing or the photo is hidden, the words
are simply in front. A spec that says "behind" can never break a render.

**The PowerPoint export** cannot mask a text box. So it places the subject as a
cut-out picture (photo pixels with the matte as transparency, dimmed and blurred like
the photo) exactly over the photo, above the headline. A designer can move or delete
it. Checked by converting the file to an image with LibreOffice: it matches the
render.

### 3. The matte (`photo/subject.matte`)

- **Size:** U²-Net's answer at the photo's own size, at most 1600 px on the longer
  side.
- **Edges:** snapped to the photo's edges with a guided filter (He, Sun and Tang 2010).
- **Hardened:** a smooth step from 0.2 to 0.8. A faint "maybe" (a lamp behind the
  person at 0.2), drawn again over a band, showed as a ghost; a soft edge showed as a
  halo.
- **Made once per photo per agency** (`service.matte_for`), and kept in storage so the
  other machine can draw it.
- **`service.with_mattes`** runs before every poster is drawn or kept: drafts, tweaks,
  quick actions, the top three and Taste Lab screens.

### 4. When the words go behind (`photo/depth.py`)

Crops are chosen first, exactly as before, with every word in front. Then, for the
headline over a photo with one clear subject, the headline goes behind when **all of
these hold in every size**:

| rule | why |
|---|---|
| in at least one size, a head (a YuNet face grown to the whole head) reaches the letters, and the subject covers at least 3% of them | depth is for a person; enough overlap to look deliberate |
| the subject covers at most 25% of the headline's letters, and at most 40% of any one line | the words must still be read |
| no other words that drawing the subject again would change: words drawn before the headline, or words on a panel drawn before it | the offer must not lose the panel it was made to be read on |

Otherwise the words stay in front, exactly as before.

**Depth never steers the crop.** If depth could steer it, a reframe could slide the
subject further under the words because depth would hide the collision.

**Depth changes how a poster is scored.** With depth, the framing cost and the face
check count the headline, and the panels between it and the photo, as hiding none of
the subject. So a cover whose headline meets a head is no longer punished as "words
over a face". It ranks as what it is.

**Products are excluded on purpose.** We first allowed any subject. On a test photo a
horse's back broke through the band behind the offer, and the offer (dark letters made
for a light card) ended up on a black horse. Requiring a head, and refusing when other
words would lose their panel, came from that picture.

### 5. The `cover` family (`design/families.py`)

The existing families draw one panel behind all the words. So when a head reaches the
headline, it also reaches the offer on the same panel, and depth is always refused.
`cover` is a classic layout of its own:
- the headline on a band across the top;
- the offer, the call to action and the logo on a band at the foot;
- the photo between them.

**Layer order:** the foot band comes after the headline, so depth never touches it.

**Choices:** band or dark fade, alignment, logo side, headline scale. That makes 16
designs. One is kept only where the two bands leave at least 35% of the page to the
photo.

**Already updated for it:**
- The critic's training data counts `cover` as its own family.
- Restyle labels cover it, because it uses the existing setting names.

## Tests

**`test_depth.py`** (25):
- **When depth applies:**
  - a head rising into the headline goes in front;
  - the words stay in front when the head does not reach them, or would hide a quarter,
    or nearly half;
  - only a person, not a product;
  - not when the photo has no clear subject;
  - not when other words would lose their panel;
  - not when one line would lose 40%;
  - it must fit in every size;
  - it is decided again from scratch;
  - framing decides it after choosing the crop.
- **The cover family:**
  - its layer order;
  - its bands leave at least 35% to the photo;
  - the calm cover goes behind a head that reaches it;
  - the bold cover refuses, because it would hide too much.
- **Measures:**
  - letters are measured line by line inside the box;
  - the covered share follows the crop;
  - with depth, the head no longer counts as covered (lower framing cost, no face flag).
- **Drawing:**
  - pixel invariants: on the subject the picture is the photo alone; everywhere else,
    the poster with the words in front;
  - no matte means the words are in front;
  - a hidden photo brings nothing in front;
  - the PowerPoint cut-out sits above the headline with the photo's exact box and crop.
- **The matte:**
  - the guided filter snaps a soft edge to the photo's edge;
  - the matte has the photo's shape, firm edges and few in-between values;
  - it is at most 1600 px.
- **The service:**
  - the matte is stored once, downloadable from storage, and the second call costs
    nothing;
  - a poster without depth is left alone.

**`packages/spec/tests/test_depth_spec.py`** (12):
- validation (behind something that is not a photo below);
- `assets()` includes the matte;
- the 5→6 upgrade;
- a swap drops the depth;
- pan, zoom, moves and hiding keep it.

**Mutation-checked** (each of these broke a test):
- ignoring other words;
- not requiring a head;
- not drawing the subject.

## Risks

- **It will not happen often.** It needs:
  - a cover design;
  - a person whose head reaches its headline in the chosen crop;
  - letters that stay readable.
  Real portraits will show how often. The rules are constants in `photo/depth.py`.
- **Tweaks that move the headline do not decide depth again** (issue log). Only
  framing does: drafts, restyles, swapped photos. A headline dragged down onto a face
  keeps depth if it had it, and could then lose more than 25% of its letters.
- **Hair and fine edges** come from a 320-pixel model, upscaled and snapped to the
  photo's edges. Hard-edged subjects look clean; frizzy hair over a busy band may not.
- **Critic v1 has never seen a cover.** It scores covers on general measurements. The
  Taste Lab will say whether people like them.
- **The `cover` family is new.** Every ranking now samples from five families, so the
  three options shown for an existing post can differ from before (they are made once
  per post and stored).

## Five things to be able to say

1. Words can go behind a person (schema 6): the renderer draws the photo again through
   the subject's matte, so the person stands in front of the headline and its band.
2. It happens only where it looks intended: a head reaches the headline, at most a
   quarter of the letters are hidden, and no other words lose the panel they are read
   on.
3. Depth never steers a crop. It is decided after framing, from scratch, in every size.
4. The matte is the model's answer at full size, edge-snapped with a guided filter and
   hardened, stored once per photo so any machine can draw it. PowerPoint gets the
   same picture as a cut-out layer.
5. A new `cover` family (headline across the top, details at the foot) is where depth
   can happen, because its foot band is drawn after the headline.

## For the ADR (Rushil writes it)

- **Redraw the subject over the words** rather than mask the letters. A mask only works
  when nothing sits between the photo and the text, and every family has a band or a
  scrim there.
- **Depth is decided after framing**, never by it. That keeps crops honest and one
  rule.
- **The matte is a stored asset** referenced by the spec, not recomputed at render
  time. An approved poster renders the same picture forever, whatever happens to the
  model.
