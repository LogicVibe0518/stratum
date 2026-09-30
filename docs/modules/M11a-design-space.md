# M11a — The design space: hundreds of valid designs per post, laid out around the words

**Status:** written by Claude, 30 Sep 2026. **Spec schema 2 → 3**, with its upgrader
and tests. No migration, no auth or token code, no new dependency.

## Why

There were nine hand-made templates, each with fixed boxes. That caused three
problems the live tests found:

- A short headline left a gap and a long one shrank. Nothing measured the words.
- Asking for "both photos and the offer" had no answer. The one design with two
  photos had no offer line, and the others had one photo.
- Two templates left the bottom fifth of a story empty, and the preview showed it
  black.

The ranking model (M12) also needs something to rank. The plan agreed on 28 Sep is
about 200 or more valid candidates per post, with the critic choosing. The reasoning:
the more good options there are to choose from, the more a good chooser gains.

## What it is

**Four families**, each one layout idea with a few honest choices:

| family | the idea | choices | designs |
|---|---|---|---|
| `panel` | a photo and a solid panel of brand colour | side, split, colour, alignment, accent rule, logo on photo or panel, headline scale | 288 |
| `overlay` | a full-bleed photo with the words on a scrim, band or card | top or bottom, treatment, alignment, rule, logo corner, scale | 96 |
| `pair` (**Two photos**) | two of the client's own photos, then the words | split, gutter, colour, alignment, rule, logo place, scale | 192 |
| `type` | no photo; the words are the design | colour, alignment, rule, centred or low, logo top or bottom, scale | 72 valid of 96 |

That is 648 designs per post. With every fictional brand kit and a real brief, all
648 lay out in all four sizes with no preflight flag of any kind. That is 12,960
layouts checked.

**Words are measured, not guessed.** Each design is laid out around the real copy
using the brand's own fonts (`design/typeset.py`). The headline takes the largest
size that fits in three lines or fewer. The offer sits one fixed gap under the
headline's last line, and the call to action one gap under that. If the words cannot
fit at a readable size, that design is not a candidate for this copy. It is never a
poster with cut-off words.

**The logo box takes the logo's shape**, sized by area. A tall mark, such as a crest
or a photo used as a logo, gets as much presence as a wide wordmark. It is never
recoloured: M9b's panel still appears only where it is needed.

**Stories are designed as stories.**
- Words and the logo stay inside Instagram's safe band.
- Each size moves the split to give the words room.
- In a size that is too cramped, the logo moves onto the photo, as a designer would.
- The bottom fifth of a story is always covered by the panel, the photo or the page
  colour. A test enforces this.

**Ids decode to designs.** For example, `ds.pair.1.0000010` means the Two photos
family, generation 1, and one choice per parameter. A preset gives a friendly name to
a fixed id: `two_photos` is offered in the design picker today. When M12 arrives, the
critic picks designs by itself.

## Spec schema 3: a poster carries its own sizes

`PosterSpec.variants` holds, for each other size, the layout-only changes that size
makes: box, alignment, type size range, visibility and so on (`LayoutOverride`).
Content, colour, font and asset are not layout fields, so no size can say or show
something different from another. The model rejects any attempt.

- **New posters carry their sizes.** Hand-made templates are embedded at draft time
  (`embed`). Design-space designs are laid out per size.
- **Old posters work as before.** A schema 2 poster upgrades with no variants and is
  laid out in other sizes from its template file, exactly as it was (`_v2_to_v3`,
  tested).
- **Edits now reach every size.** "Make the logo bigger" scales the logo's box in
  each size that places the logo itself, about that size's own centre.
  - "Move" moves it everywhere.
  - "Show" or "hide" also applies to sizes that had deliberately hidden a layer.
  - Before this, a story with its own logo box kept the old logo. That was an
    existing bug in the template path, found while building this.
- **Editing a template file no longer moves old posters.**

## Also

- `stratum_worker.design.preview` renders contact sheets from procedural scenes. It
  uses no photos and no client data, and every image can be made again from its
  seed. The README images and the critic's training renders come from it.

  ```
  uv run python -m stratum_worker.design.preview --client kowalski-plumbing --size 9:16
  ```
- A design needing more of the client's photos than they have says so in their
  terms. "Before/after" still says it "shows real work". Two photos says "This design
  shows 2 of Kowalski's own photos and has 1 usable. Upload more, or pick a design
  with one photo."
- `/api/designs` and `/healthz` list the presets. `POST /api/posts` accepts any
  design id.

## Five things a reviewer should be able to repeat back

1. **Every candidate is correct before the critic sees it.** Brand colour pairs,
   measured words, safe zones and margins are built in. Preflight finds nothing in
   12,960 layouts.
2. **The words decide the boxes**, not the other way round. Words that do not fit
   remove a design from the choice; they never produce a cut-off poster.
3. **A poster carries its own layout for every size** (schema 3), so edits reach
   every size and template changes never move old posters.
4. **A size may only lay a layer out differently.** It can never say, show or colour
   anything differently, and the model enforces that.
5. **Two photos exists**: both photos, the offer and the logo on one poster, in all
   four sizes.

## Tests

- `test_design_space.py` (19):
  - the size of the space;
  - every design is flag-free in every size for all five kits;
  - ids round-trip and nonsense ids are refused;
  - the preset;
  - every size keeps the same layers and words;
  - the offer gap is constant whatever the headline;
  - words that cannot fit produce no design;
  - the logo box takes the logo's shape;
  - no story leaves its bottom fifth empty;
  - story text and logo stay in the safe band;
  - Two photos drafts with both photos and the offer;
  - Two photos with one photo says what it needs;
  - any design id drafts in four sizes;
  - "logo bigger" reaches the story;
  - a schema 2 poster still renders its story.
- `packages/spec/tests/test_variants.py` (10): embedding; its own layout wins; old
  posters use the file; a laid-out size has no sizes; unknown layers and non-layout
  fields are refused; scale, move and show or hide reach every size.
- `test_migrate.py`: the v1 → current and v2 → v3 upgraders.

## Not in this module

- Choosing among the 648 designs: the critic and the top three (M12).
- Cropping photos around their subject (M10).
- The hand-made templates stay as they are, and posts made with them keep working.
