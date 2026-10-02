# M10d — The control panel: every layer measured, changed by hand, no model

**Status:** written by Claude, 2 Oct 2026.

**Review flags:**
- No migration.
- No change to RLS, auth or token code.
- No new dependency.
- No spec schema change.
- Two new API endpoints, read and write, both scoped by the caller's token like every
  other post endpoint.
- The tweak commit step is moved into a shared function. Its behaviour is unchanged:
  every existing tweak test passes as before.

## Why

Rushil, on 30 Sep, asked for "an editor like Lightroom, a control panel which is properly
detailed", and for the brand's colours to be visible while editing.

The chat is the fast path. But a designer also wants to:
- see exactly where things are and how far apart;
- nudge one thing by one step;
- pick a colour from the brand;
- switch a style setting.

All of that should happen without waiting for a model or paying for one. This module is
the server side. Cursor brief C7 is the page.

## What it does

### `GET /api/posts/{id}/inspect?aspect=4x5` (`service.inspect_post`, `edit/inspect.py`)

It returns one size of the latest version, measured the way the renderer draws it:

- **Every layer, in order:**
  - `name` (in words), `kind`, `visible`, `locked`, `where` (in words), `box`
    (fractions) and `px`.
  - **Text:**
    - the words, and how many characters fit (the same measure the chat uses);
    - the rendered size and lines, from `render.layout.place_text`, the same fit the
      renderer uses;
    - the ink rectangle;
    - the colour by name;
    - contrast against what is behind it. That uses the same blend over the assumed
      backdrop and the same WCAG floor as the layout check, so the panel says "hard to
      read" exactly when the check would flag it.
  - **Shapes:** colour by name, and opacity.
  - **Photos:** crop (x, y, zoom), darkness, and which ways it can move now. That comes
    from the photo's real size and its frame.
- **The palette:** every brand colour by name, and what on this poster uses it.
- **Measurements:**
  - each text block's and the logo's distance to every edge (px);
  - the gaps between text blocks, top to bottom;
  - which left edges line up (within 2 px);
  - for a story, the safe band and what is outside it.
- **The style:**
  - the design's settings as dropdowns, with short labels in the brand's own colour
    names ("Background: Brown / Cream / Very dark brown");
  - the other designs, with `available` and `why` ("Needs 2 of the client's photos;
    there is 1.");
  - null for a fixed template.

### `POST /api/posts/{id}/edit` (`service.edit`)

Body: `{version, ops, restyle}`.

- **Ops:** only `set_text`, `set_color`, `move`, `scale`, `set_visible`, `pan`, `zoom` and
  `dim`.
  - Swapping assets, `needs_human` and anything else is refused with a sentence.
  - The ops are validated by the spec's own models, like a typed tweak's.
- **`restyle`:** validated by the same model as the chat's restyle. It runs the same
  restyle, which keeps the person's earlier changes (M10c-2).
- **Version check:** `version` must be the latest. Otherwise it returns **409**, "The
  poster is on version 3 now, not 2. Reload to see it, then make the change again." That
  protects two tabs, or two people, and makes a double click one change.
- **Colours:** a colour the brand does not have is refused with the brand's colours named.
- **Commit:** the same function as a typed tweak. Photos are framed, every size is rendered,
  layout, claims and brief are checked, then it saves, records and logs a restyle decision.
  - No model is called; the cost is 0.
  - History reads "Editor: headline slate blue; photo darkness 30%", written by the
    server from the ops, never taken from the page.
- **Words typed into the panel are the designer's own.** The claims check trusts them, as
  it trusts typed tweaks. "35% off" typed by hand is not flagged as invented.

### The refactor

`service.commit_change` is the second half of `tweak()`, moved out unchanged: restyle, apply
ops, the no-op check, frame, render, check, save, record, posts and decision. `tweak()` and
`edit()` both call it.

## Tests

- `test_inspect.py` (11):
  - every layer is listed in order with a name;
  - the words are measured exactly as drawn (lines and size equal `place_text`, ink inside
    the box);
  - colour and contrast;
  - low contrast is not ok;
  - a photo's crop, darkness and axes;
  - the palette and what uses it;
  - margins, gaps and alignment;
  - the story safe band;
  - style dropdowns with brand names and design availability;
  - none for a fixed template.
- `apps/api/tests/test_control_panel.py` (11), over HTTP on a real draft:
  - the panel reads a size;
  - a missing size is a sentence;
  - a colour by hand is instant and free with no model call, and History says what changed;
  - a stale version is a 409 with a sentence;
  - swap, `needs_human`, an out-of-range scale and an empty change are sentences;
  - an off-brand colour names the brand's colours;
  - a style by hand restyles with no model;
  - typed words are not flagged as invented. Mutation-checked: without the fix, this test
    fails.
- All the M10c tests still pass through the refactored commit.

## Risks

- **The panel reads one size at a time.** A move or scale applies to every size, as the
  chat's do. The panel shows the size it read. The others are in the previews.
- **The logo's "With the words" setting can show while the logo sits on the photo.** The
  family moves it when the words need the room. The layer geometry is the truth; the
  setting is a preference.
- **Inspect does real work:** it fits every text layer and reads each photo's header.
  That takes about 50–150 ms on a laptop. It runs in a thread, after the first photo
  download.

## Five things to be able to say

1. The control panel reads every layer measured exactly the way the renderer draws it:
   words, size, lines, ink box, colour by name, contrast against what is behind, and the
   photo's crop.
2. Changes by hand go through the same validation and the same commit as typed tweaks,
   but with no model: instant and free.
3. Every change says which version it was made on, so a stale tab or a double click
   cannot overwrite a newer poster (409).
4. Measurements a designer checks by eye come from the same numbers: margins, gaps,
   left-edge alignment and the story's safe band.
5. Style settings appear as dropdowns labelled with the brand's own colour names, and
   switching one keeps the person's earlier changes.

## For the ADR (Rushil writes it)

- **One commit path for typed and hand-made changes, rather than a separate editor
  write path.** It means one set of checks.
- **Optimistic version check rather than idempotency keys** for in-request edits. The
  unique `(post_id, version)` constraint is the backstop.
