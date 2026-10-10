# M10i — The first editing set: fades, blended photos, light and colour, word effects, alignment, order and an offer badge

**Status:** written by Claude, 6 Oct 2026. It is the first slice of the editing standard
(`claude/editing-standard.md` in the project), building on M10c (tweaks), M10d (the
control panel) and M10h (point and say).

**Review flags:**
- **Spec schema 6 → 7.** New fields:
  - `ImageLayer.fades`, `.flip` and `.adjust`;
  - `ShapeLayer.fades`, plus the shapes `"pill"` and `"burst"`;
  - `TextLayer.effect`;
  - `LayoutOverride.fades`.

  The upgrader changes nothing (old posters have none of these). **Deploy the API and
  the worker together** (Railway does), and stop any laptop worker first: old code
  refuses a schema-7 poster as "newer".
- **Prompt change:** edit.parse v6 → v7, with the pinned hash updated. v6 is kept as
  `ml/chat/edit_parse_v6.md` and is now the chat eval's baseline.
- **Ten new spec ops:** fade, flip, adjust, set_align, set_font, set_case,
  text_effect, reorder, set_opacity, align.
- **One new service action:** badge.
- **The control panel accepts all of them** (`EDITOR_OPS`), plus a new optional
  `PanelEdit.badge` field.
- **A new design family, `collage`:** 192 designs, so the space grows from 688 to 880.
- **PowerPoint/Canva export:** a photo that is faded, mirrored or colour-changed is
  baked into a picture of exactly its box. The original photo is never changed.
- No migration. No RLS, auth or token change. No new dependency.

## Why

Missing editing features were being found one at a time, by trying a post and hitting
a wall. Two of those, on 6 Oct:
- photos joined "with some fade effect";
- multiple photos and elements.

`claude/editing-standard.md` sets out:
- a fixed checklist, taken from Polotno's public feature list and Canva's help pages
  (feature names only, nothing copied);
- one rule for when a feature is done;
- a walk that tests them all at once.

This module is the checklist's T1, except "pick a photo by description", which Rushil
is building himself (photo understanding).

## The rule every feature here follows

1. **One allowlisted op** in `packages/spec/ops.py`: pure, validated, tested.
2. **Reachable three ways:**
   - typed in the chat (prompt v7, plus 39 new chat-eval cases);
   - one word on a pointed part, where it is one word;
   - a panel control (Cursor brief C11).
3. **Guarded:**
   - the logo is never mirrored or recoloured;
   - real photo pixels are never changed (light and colour are drawn, not saved);
   - a font must be one of the brand's;
   - badge words are checked like any other words on the poster.
4. **The same in every size.**
5. **Can be undone** (a new version).
6. **One line in the feature walk.**

## What changed

### 1. Photos

| op | what it does |
|---|---|
| `fade` | the photo (or a band of colour) melts into what is behind it at one edge, across a share of its size; 0 takes it away. Eased (smoothstep), so no band shows where it starts |
| `flip` | mirrors a photo left to right; again to undo. Its subject matte (depth, M10g) is mirrored with it. Refused on a logo |
| `adjust` | brightness, contrast, saturation (−1 is black and white) and warmth, drawn as an SVG filter. The ranges stop short of making a photo something the client did not take; back to 0 is no adjustment at all |

### 2. Text

| op | what it does |
|---|---|
| `set_align` | left, centred or right within its own box |
| `set_font` | another of the brand's fonts. Any other font is refused with the brand's font list |
| `set_case` | capitals or normal case |
| `text_effect` | a shadow or an outline, for words on a photo. With no colour asked, black or white, whichever stands out from the words |

A size that set its own alignment or case follows an explicit ask, as it does for
visibility.

### 3. Arrangement

| op | what it does |
|---|---|
| `reorder` | front, back, forward or backward. Never behind a backdrop that covers the whole poster (it would vanish). Words that sit behind a photo's subject stay above that photo |
| `set_opacity` | see-through, down to 10% (fainter is hidden: `set_visible`) |
| `align` | against the margin every design keeps (7.4% of the shorter side), or in the middle; in a story, inside Instagram's safe band. Each size works out its own position. A test keeps these numbers equal to the designs' and preflight's |

### 4. Two photos with no hard seam: the collage family

**The layout:** two photos overlap. The second fades into the first across `seam`
(24% or 40% of the width). Then one faded band of the words' colour melts both into
the area under the words.

**Why one band, and not a fade on each photo:** fading each photo leaves a visible
step where they overlap. The first version did exactly that; it showed on the contact
sheet and was replaced.

**Per size:**
- a feed post has the photos above the words;
- a banner has them on the left;
- so each size fades its own way, using the new `LayoutOverride.fades`.

**What else:**
- The words never sit on the photos or the band (tested in every size).
- It needs two real photos, like the pair family.
- The chat reaches it with "blend the two photos" (restyle family `collage`).

### 5. The offer badge

**What it is:** `{"op": "badge", "text": "20% OFF", "corner": "auto"}` adds two
layers:
- a burst in the brand's accent colour;
- its words, in the brand colour that stands out most from it (contrast ratio at
  least 3, tested).

`{"op": "badge", "remove": true}` takes it away. A second badge replaces the first.

**Limits:** at most 14 characters. The prompt says the words come from the designer
or the poster's own offer: never invent a discount. The claims check reads them too.
A made-up "70% OFF" is flagged like any other number nobody wrote (tested).

**Where it goes:**
- "auto" picks the corner covering the least of the words and logo in the post's own
  size.
- A size where that corner would sit on the words takes its own best corner. A
  banner's words are on the right, so its badge goes bottom left. The first version
  kept one corner everywhere and covered the banner's headline.
- A corner the designer asks for is kept in every size.

### 6. The chat (edit.parse v7, `edit/context.py`)

**The prompt** lists the new ops and when to use them:
- "blend the photos" is the collage design;
- "centre the text" means the lines;
- "centre it on the poster" means the position;
- "black and white" is saturation −1;
- a person facing away from the words is a flip;
- hard-to-read words need a shadow, or a darker photo.

**What the model is told about the poster:**
- each layer's new settings in words: "fades out at the bottom (30%)", "mirrored",
  "warmth +0.20", "centred", "font display", "in capitals", "with a shadow",
  "50% opaque";
- the brand's fonts by name.

### 7. One word on a pointed part, with no model (`edit/pointing.py`)

| word | does |
|---|---|
| flip, mirror | flips the pointed photo (never a logo) |
| centre | centres a text's lines; centres a logo or shape on the poster |
| caps, lowercase | sets capitals on or off |
| front, back (and "bring to front") | reorders |
| shadow, outline | adds that effect to pointed text |

### 8. The control panel (`app/service.edit`, `edit/inspect.py`)

**It accepts** every new op and a badge, with no model and no cost. History says what
changed in plain words: "bg faded at the bottom", "headline in normal case",
"badge NEW".

**The inspector now returns:**
- each layer's opacity and order;
- text: font, case and effect;
- photos: flip, fades and light/colour;
- shapes: shape and fades;
- the brand's fonts.

The page part is Cursor brief C11.

### 9. Export to PowerPoint and Canva (`export/pptx.py`)

- **Shapes:** a pill is a rounded rectangle, a burst is a 16-point star.
- **Faded band:** a picture of its colour fading out. PowerPoint's gradients do not
  survive Canva's import.
- **Text effects:** an outline or shadow is PowerPoint's own, so it stays editable.
- **Faded, mirrored or colour-changed photo:** baked into exactly its box, with its
  darkening in the pixels so the fade covers both. A "contain" photo keeps its
  transparent sides.

### 10. The feature walk (`edit/walk.py`, `make feature-walk`)

**What it is:** every editing feature once (21 rows, old ops and new) on the chat
eval's fixed posters, in all four sizes, on one contact sheet. A feature that raises
or changes nothing is printed as a problem, and the command fails.

**Guarded by tests:**
- every op the panel accepts has a row;
- a sample of the walk runs clean.

**It already earned its keep while this module was built:**
- the stepped collage seam;
- the badge on the banner's headline;
- an older problem: "bigger headline" pushes a banner headline off the left edge
  (issue I-37).

## Results

Rushil runs the chat eval:
- `make chat-eval ARGS="--baseline"`: v6, with the short words known before M10i;
- `make chat-eval`: v7.

There are 118 cases, 39 of them new, in category `editing`.

**Two old cases were changed, before any run:** "centre the words" and "left align the
text" (M10h) accepted only a restyle. They now also accept lining up each text layer
(`set_align`), which did not exist before. Both are right.

| | before: v6 | v7 |
|---|---|---|
| passed (all 118) | | |
| editing (39) | | |
| without a model | | |
| cost | | |

## Tests

**`packages/spec/tests/test_editing_set.py`** (12):
- schema 7 upgrades unchanged; one fade per edge;
- fade adds, replaces and removes; a size that fades its own way follows;
- a band can fade, words cannot;
- flip twice is the photo as taken; the logo is never mirrored;
- adjust sets what it names; 0 is none; out of range is refused;
- text ops, and sizes that chose their own alignment follow;
- reorder never goes behind the backdrop, and words behind a subject stay above it;
- see-through stops at 10%;
- align uses the margin and a story's safe band.

**`apps/worker/tests/test_editing_render.py`** (8):
- the margins equal the designs' and preflight's;
- a fade goes from the photo to what is behind it;
- a band fades;
- a mirrored photo and its matte are mirrored;
- black and white is grey, brighter is brighter;
- word effects, with the contrasting colour picked;
- burst and pill are drawn;
- the export keeps every new look.

**`apps/worker/tests/test_collage_and_badge.py`** (16):
- a collage needs two photos;
- it blends and melts toward the words in every size, never under them;
- a badge is readable, single and removable;
- impossible badges are sentences;
- auto keeps off the words in every size;
- an asked corner is kept;
- the walk covers every panel op and walks clean;
- six one-word instructions need no model and are unknown to the baseline;
- a pointed logo is never mirrored.

**`apps/worker/tests/test_editing_set_service.py`** (4):
- a typed fade, flip and badge make the next version;
- an invented badge is flagged;
- the panel sends new ops and a badge with no model, and History says so;
- a font the brand lacks is refused in a tweak and in the panel.

**Updated:**
- `test_chateval.py`: the baseline is v6 without the newer words; the newer words are
  answered without a model;
- `test_inspect.py`: the panel sees the new settings and the fonts;
- `test_edit_context.py`: a text line says its font;
- `test_restyle.py`: "collage" is now a real family;
- the schema pins in `test_migrate.py` and `test_depth_spec.py`.

## Risks

- **Schema 7:** an old worker refuses new posters. Deploy together; stop laptop
  workers.
- **The critic does not see photo light or darkness** (it assumes mid-grey under the
  words, I-36). So it judges a darkened, brightened or faded photo as it judged the
  plain one. The edits are the designer's choice, so this matters for ranking, not
  for correctness.
- **The preflight contrast check does not credit an outline or shadow** yet. Words
  with an outline on a busy photo may still be flagged; the designer decides.
- **Collage photos are framed for their own boxes.** The faded part of the second
  photo may hold part of its subject. The framing does not yet know about the fade.

## Not in this module (issue log)

- **Photo by description:** Rushil, guided (photo understanding).
- **T2:** blur, shape masks, cut-out layer, icons, QR code, lock, duplicate, PDF,
  carousel.
- **I-37:** "bigger" on a text box can push it past the canvas edge. Scale should keep
  the box inside the margins. *Fixed after M10i: words and logos now stay within the
  margins in every size (`ops._scaled_inside`, test_bigger_stays_inside.py).*
- **The page:** Cursor brief C11.

## Five things to be able to say

1. Editing features now come from a fixed checklist (Polotno's and Canva's feature
   lists, names only) with one rule for done: an allowlisted op, reachable from the
   chat, a pointed word and the panel, guarded, the same in every size, undoable,
   and in the feature walk.
2. Ten new spec ops and a badge action. Every one is validated like the old ones,
   and real photo pixels are never changed: light and colour are SVG filters, fades
   are masks.
3. Two photos blend through a new collage family. One faded band over both photos
   avoids the visible step that fading each photo separately left. The step was
   found on the contact sheet, not by a user.
4. The badge goes where it covers the least of the words in each size. Its words come
   from the designer or the offer, and the claims check reads them.
5. `make feature-walk` renders every feature in every size on one sheet. A test makes
   it impossible to add a panel op without a row.

## For the ADR (Rushil writes it)

- **A checklist from existing editors, not features found by testing.** Coverage is
  decided once, and gaps are visible in the standard.
- **Fades as masks and light as filters, not new pixels.** Undo and every size stay
  free, the original is untouched, and the export bakes a copy only where the target
  has no equivalent.
