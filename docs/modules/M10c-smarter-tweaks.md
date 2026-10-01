# M10c — Smarter tweaks: the chat knows the poster, the brand's colours and the styles

**Status:** written by Claude, 1 Oct 2026.

**Review flags:**
- No migration.
- No change to RLS, auth or token code.
- No new dependency.
- No spec schema change: the new ops change existing fields (`crop`, `dim`).
- Prompt `edit.parse` goes from v3 to v4.

## Why

Live, 30 Sep, on the pilot client's poster:
- The designer typed "change the colour of the text to green".
- The page answered with a technical refusal about palette tokens.

Four things were wrong:
1. **The model could not see the colours.** It was given token names
   (`palette.secondary`), never the colours themselves. It could not know the brand
   is browns, gold and cream with no green.
2. **The refusal was jargon.** Nothing said what the brand does have or what to
   type instead.
3. **The page shows no palette** (that is the UI brief C7, next).
4. **The chat could only nudge.** Its whole vocabulary was move, scale, recolour,
   retext and hide. It could not:
   - "put the words on the left" or "make the panel cream", which are design
     decisions the design space already knows how to make well;
   - move the photo inside its frame ("show his head");
   - undo.

## What changed

### 1. What the model is told (`edit/context.py`, `brand/names.py`)

**Each layer in a designer's words:** what it is, where it is, its colour by name,
how many characters it fits, whether it is hidden, and for a photo which way it can
move. For example:

```
"headline": "text (headline), bottom centre, very dark brown (palette.text), fits 51 characters: Boiler service, sorted"
"bg": "photo, the top 58%, can move up or down; zoom in first to move it left or right"
```

**Each brand colour by name, and what uses it now:**

```
palette.secondary: gold #ca9b5a
palette.surface: cream #fcf6ed; used by the colour panel
```

- Names come from OKLCH (lightness, chroma, hue), not a CSS name lookup. A brand's
  own browns and creams rarely match a CSS name.
- Hidden layers are not counted as users.

**Every colour the message names**, said as one of two things:
- the brand has it ("gold: the brand's gold is palette.secondary"); or
- the brand does not have it, plus the two nearest brand colours: "green: NOT a brand
  colour; the nearest brand colours are …".

**This design's style settings:**
- the current value of each and the other values;
- what each setting does, with the brand's own colour names ("fill: primary = brown,
  surface = cream, dark = very dark brown");
- the other designs, marked "NOT available" when the client has too few photos for
  them.

**The last five changes**, so "again", "bigger still" and "same for the logo" mean
something.

### 2. New ops

| op | what | where |
|---|---|---|
| `pan` | moves the photo inside its frame, every size by the same amount | spec (pure) |
| `zoom` | closer or wider inside the frame, 1–3× | spec (pure) |
| `dim` | darkens the photo, 0–0.8, every size | spec (pure) |
| `restyle` | the neighbouring design: one or more style settings, or another family | service |
| `ask` | a question with 2–4 clickable choices; changes nothing | service |
| `undo` | the version before the last one, as a new version | service |

**Restyle** (`edit/restyle.py`, `app/actions.py`):
- The asked-for settings are kept exactly. Every other setting stays as it was where
  the new design has it.
- The post is laid out again around:
  - the words **now on the poster**, so a tweaked headline stays tweaked;
  - the same photos, plus the client's next-best photo when a two-photo design needs
    one;
  - the same logo.
- The photos are framed again for the new layout (M10b).
- If the words do not fit the exact design, the next candidates keep every asked-for
  setting and as many of the others as possible. The note says when that happened.
- `posts.template_id` becomes the new design.
- One `decision_log` row of kind `edit_request` (an existing kind) records
  `{what: restyle, from, to, changes}`. A designer choosing a style is a taste label
  (M15).

**Undo:**
- Typing "undo", "undo that", "go back" or "revert" needs no model: instant and free.
- The model can also return `undo` from a longer sentence.
- Undoing a restyle brings the design back as well.

### 3. Guards and plain words (`service.tweak`)

**A colour the brand does not have** (`set_color` to `palette.green`) becomes a
question:

> "Green isn't one of Kowalski Plumbing & Heating's brand colours (navy, orange,
> off-white, white, slate blue)."

It comes with choices:
- "Make the headline …" with the two nearest brand colours;
- "Add green to the brand colours" (`kind: brand`, for the brand page).

Before this, such a token passed the spec's pattern and would only have failed at
render time.

**Op errors become sentences.** For example, "The logo has no words to change."
replaces "layer 'logo' is not a text layer".

**An op the editor does not know** → "I could not turn that into a change. Try
saying it another way."

**Leniency where it is safe:**
- "Centre" is `center`, and "Surface" is `surface`.
- A long question or note is shortened rather than failing the whole answer.

### 4. The response (for C7)

Every applied tweak now returns:
- `note`: what was done when the request was read into ("make it pop" → "Made the
  headline a little bigger so it stands out.");
- `unable`: what could not be done.

Every question returns `question` and `choices` (`[{label, kind}]`).

`why` still carries everything in one line, because the current page shows only
`why`. A question's `why` ends with "You could say: 'Make the headline gold' or …".

### 5. Prompt v4 (`gateway/prompts/edit_parse.md`)

The new context and ops. The reading rules:
- match the designer's words to layers ("the title", "the button", "the box behind
  the text");
- never substitute a colour silently: ask with the nearest ones;
- prefer one restyle over many moves for layout and look;
- read vague requests ("make it pop", "cleaner") as one sensible change plus a
  `note`, or ask with the two readings;
- zoom in a little before panning a photo that only moves one way;
- never mention ids, tokens or JSON to the designer.

Size: about 7,000 characters with a real poster, about 1,800 tokens.

Also: the contact-sheet sample words (`design/preview.SAMPLE_COPY`) are plumbing
wording now, to match the fictional test plumber. The critic's training data
(`critic/data.py`) is unchanged.

## Tests

- `test_colour_names.py` (29): names for both palettes and plain colours; the
  brand's own colour found by name; green is not in a brown-and-gold brand; colour
  words found once each.
- `test_edit_context.py` (19): where a layer is in words; text, shape and photo
  lines (which way it can move, zoom, darkened); hidden; colour usage; asked
  colours; the style menu (values, brand names, "NOT available"); a fixed template;
  recent changes; the prompt carries every part.
- `test_restyle.py` (13): one setting changes and the rest stay; every candidate
  keeps the asked settings; most kept first; a family switch carries the shared
  settings; impossible restyles are sentences; a fixed template; a preset name.
- `test_edit_actions.py` (15): spec ops and service ops are separated; spellings;
  long text is shortened; anything else is refused; undo words.
- `packages/spec/tests/test_photo_ops.py` (17): pan (direction, every size, the
  edge, from the centre); zoom limits; dim in every size; only photos; a photo
  shown whole; the input never changes; JSON round trip; out-of-range values
  refused.
- `test_smarter_tweaks.py` (14), on real drafts with a scripted model:
  - the live green request → a question with choices, no new version;
  - the model is told colours by name, the asked colour, the style menu, recent
    changes and photo room;
  - a model question is passed on;
  - restyle switches the design, keeps a tweaked headline and logs the decision;
  - two photos brings in the next photo, or says to upload one;
  - a bad value is a sentence;
  - undo needs no model; undo after a restyle brings the design back; nothing to
    undo on a first draft;
  - pan, zoom and dim;
  - a wrong-layer op and an unknown op are sentences;
  - an inferred change says what it did.

## Risks

- **A real model has not run v4 yet** (there are no provider keys here). The first
  live check is the walkthrough below. If the model ignores `<asked>` and still
  writes `palette.green`, the guard turns it into the same question.
- **Restyle starts from the new design's own colours and logo place.** A
  `set_color` or a hidden logo from an earlier tweak does not carry over. The note
  says which design it switched to, and undo brings the old one back.
- **"logo: panel" is a preference, not a promise.** The family moves the logo to the
  photo when the words need the room, so the style menu can say "panel" while the
  layer line says "top left". The layer line is the truth: it is the geometry.
- **Pan on an axis with no room** stores a new crop that looks the same. The layer
  line tells the model which axes have room, and the prompt says to zoom first.

## Walkthrough (Rushil, live, after deploy, about 10 minutes)

Each line is one message in "Change something", on a Best three post.

1. "make the text green" → a question naming the brand's colours, with two "Make
   the headline …" choices in the line.
2. "make the headline gold" → applied.
3. "put the words on the left" or "move the panel to the top" → a restyle note,
   and the design changes in every size.
4. "make it calmer" → a restyle or scale, with a note saying what changed.
5. "show more of the top of the photo" → pan, or zoom and pan.
6. "darken the photo a bit" → dim.
7. "undo" → instant, back one version.
8. "make the logo text bigger" → a plain sentence.

Note what came back for each line. Anything that reads like jargon is a bug.

## Five things to be able to say

1. The model now sees the poster the way a designer describes it: where each layer
   is, its colour by name, what the brand's colours are called, and which brand
   colours are nearest to the one asked for.
2. A colour the brand does not have is a question with the nearest brand colours as
   clickable choices, never a silent substitute and never an error.
3. "Words on the left" or "cream panel" is a restyle. The post is laid out again as
   the neighbouring design around the same words and photos, which keeps every
   colour pair and margin right. The switch is logged as a taste label.
4. Photos can be panned, zoomed and darkened inside their frames, in every size at
   once. Typing "undo" needs no model at all.
5. Everything the model returns is still validated before it touches the poster:
   spec ops by the spec, and restyle, ask and undo by their own strict models.

## For the ADR (Rushil writes it)

- **Restyle as a service op over the design space, rather than many spec ops.**
  - Why: layout lives in the families, so a restyle reuses their rules (margins,
    colour pairs, safe zones).
- **Colour names from OKLCH rather than a CSS name table.**
- **Questions with choices rather than a refusal.**
  - Choices are complete tweak messages, so the page sends one back as the next
    tweak.
