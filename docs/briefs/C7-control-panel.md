# C7 — The control panel: layers, style, measure, brand colours, clickable answers

**Branch:** `feat/ui-control-panel` · **Who:** Cursor · **Reviewer:** Claude
**Needs:** M10d merged (the two endpoints below) and M10c (already live).

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C7-control-panel.md, and implement it exactly, part by part,
in order. Only edit apps/api/src/stratum_api/static/index.html and
apps/api/tests/test_ui_safety.py. Do not change any Python in apps/ or packages/.
Every string from the server goes through esc() or textContent/setAttribute. Run make check
after each part. Do not commit or push. Finish with a report: what you changed per part, what
you could not do, anything you guessed.
```

## Why

Designers asked for "an editor like Lightroom, with a properly detailed control panel", and
for the brand colours to be visible while editing. The server now measures every layer
exactly as the renderer draws it and accepts changes by hand, with no model and no cost.
This brief builds the panel on top of that.

It also fixes three things seen in the first live test of the chat:
- the answer's choices were plain text, not buttons;
- the note ("Changed the style: …") showed in the warning colour;
- History showed `needs_human` and wrapped its time column into "Ti / me".

## The API (M10d; read `docs/modules/M10d-control-panel.md` for every field)

### `GET /api/posts/{id}/inspect?aspect=4x5`

`aspect` is one of the post's sizes (`4x5` or `4:5` both work). It returns:

```
{ version, version_id, aspect, canvas: {width, height},
  layers: [ { id, name, kind: "text"|"shape"|"photo"|"logo", visible, locked, where,
              box: {x,y,w,h}            // fractions of the canvas
              px:  {x,y,w,h}            // canvas pixels
              // text:
              text, fits, size_px, lines: [..], align, ink: {x,y,w,h},
              colour: {token, hex, name},
              contrast: {ratio, needed, ok, behind: {token, hex, name} | null}
              // shape:
              colour: {token, hex, name}, opacity
              // photo:
              crop: {x, y, zoom}, dim, can_move: {x: bool, y: bool} } ],
  palette: [ {token, hex, name, used_by: ["headline", ...]} ],
  measure: { short_side_px,
             margins: { <layer id>: {left, top, right, bottom} },   // px
             gaps: [ {above, below, px} ],
             aligned_left: [ [ids...] ],
             story_safe: {top_px, bottom_px, outside: [ids]} | null },
  style: { family, title,
           settings: [ {name, label, value, options: [{value, label}]} ],
           designs: [ {family, title, current, available, why} ] } | null }
```

`style` is null for a poster made from a fixed template.

### `POST /api/posts/{id}/edit`

Body: `{ "version": <inspect.version>, "ops": [...], "restyle": {<name>: <value>} | null }`.
It returns the new version, the same shape as a tweak: `outcome`, `version`, `note`, `why`.

Allowed ops, all relative to the current poster:

| op | what it does |
|---|---|
| `{"op":"set_text","layer":id,"text":str}` | new words |
| `{"op":"set_color","layer":id,"color":<palette token>}` | text or shape colour |
| `{"op":"move","layer":id,"dx":f,"dy":f}` | fractions of the canvas, −1..1 |
| `{"op":"scale","layer":id,"factor":f}` | 0.2..5 |
| `{"op":"set_visible","layer":id,"visible":bool}` | show or hide |
| `{"op":"pan","layer":photo,"dx":f,"dy":f}` | the photo inside its frame; +dy shows more of the top |
| `{"op":"zoom","layer":photo,"factor":f}` | the photo inside its frame |
| `{"op":"dim","layer":photo,"amount":0..0.8}` | an absolute value, not a step |

`restyle` takes `{name: value}` from `style.settings`, or `{"family": <design family>}`.

Errors:
- **409:** the poster moved on. Show the sentence, then reload the post and the panel.
- **422:** a sentence to show as it is.

Edits are free and instant (about 1 s). History records them as "Editor: …".

### Tweak response (M10c, already live)

- An applied tweak has `note` (what was done or understood) and `unable` (what could not be
  done).
- A question has `question` and `choices: [{label, kind: "tweak"|"brand"}]`.
- `why` is still everything in one line, as a fallback.

## Part 1 — Answers you can click, and History in words (do this first)

1. **In `applyTweak()`, when the answer has `choices`:**
   - Show `question` in `#tweakwhy`, using the normal `why` class, not `bad`.
   - Under it, show one button per choice (class `secondary`), labelled with `label`.
   - A `tweak` choice puts the label into `#tweak` and applies it, as if typed.
   - A `brand` choice opens this client's screen and scrolls to `#brandpanel`. Reuse the
     code that opens a client from the breadcrumb.
   - The choice buttons go away on the next tweak.
2. **Applied tweaks:**
   - Show `note` in normal muted text.
   - Show `unable` after it, in the `warn` class.
   - If neither exists, fall back to `why`, as now.
3. **History:**
   - Result labels: `applied` → "Done", `needs_human` → "Asked you", `failed` → "Didn't work".
   - Any other value is shown as it is.
   - The Time and Result cells get `white-space: nowrap`.
   - Keep `esc()`.

## Part 2 — The editor panel

Add a new `<section class="card" id="editor">` at the top of `aside.side`, above "To check".
Title it **Editor**, and give it three tabs (a `.segment`, like the view switcher): **Layers**,
**Style**, **Measure**.

**When to load it.** Fetch `/inspect` for the post's **first** aspect (the big poster):
- after every `paintPost`;
- after every successful edit, tweak or quick change.

Keep the result in `INSPECT`. While it loads, keep the old panel and disable its controls.

**Every change goes through one function, `sendEdit(body)`:**
- it adds `version: INSPECT.version`;
- it disables the editor and shows "Saving…" in `#editorstatus`;
- it POSTs to `/edit`;
- on success it reloads like a quick change (fade, keep scroll, toast `v{n} · {s}s`);
- on 409 it shows the sentence and reloads everything;
- on any other error it shows the sentence and re-enables the editor.

### Layers tab

**A list of layers, in reverse order** (top of the poster first, like Lightroom or Figma).
Each row is a `<button>`:
- the `name`, plus a small muted `kind`;
- "hidden" in muted text when the layer is hidden;
- clicking it selects the layer (row `aria-pressed="true"`, a 2px `--focus` left border).

Each row also has an eye button:
- `aria-label="Hide the headline"` or `"Show the headline"`;
- it sends `set_visible`;
- it is disabled when `locked`.

**Under the list, the inspector for the selected layer.** By default nothing is selected,
and it says "Pick a layer to change it."

All layers:
- A line with `px` as `x 80 · y 864 · 920 × 187 px` and `where` in muted text.
- **Position:** four buttons ← ↑ ↓ →. They send `move` with ±0.01. With Shift held, ±0.05.
- **Size:** − / + buttons. They send `scale` with 0.95 / 1.05.

Text layers:
- A `<textarea>` with the words.
- A live counter `23 / 51 characters` (`fits`). It turns `--bad` above `fits`, and Save is
  disabled then.
- **Save words** sends `set_text`.
- **Colour:** one swatch button per `palette` entry (see "Swatches").
- A contrast line, e.g. `Contrast 15.3 : 1 · needs 3.0`, then "Readable" in `--good` or "Hard
  to read" in `--bad`. Use `--good` and `--bad` only for this status word.
- Size and lines, e.g. `86 px · 2 lines`.

Shape layers: colour swatches.

Photo layers:
- **Move in frame:** four arrows. They send `pan` with ±0.05.
  - Disable the left/right arrows when `can_move.x` is false, and the up/down arrows when
    `can_move.y` is false.
  - A disabled arrow has `title="Zoom in first"`.
  - Pan direction: the up arrow means "show more of the top", so it sends `dy: +0.05`.
- **Zoom:** − / + buttons. They send `zoom` with 0.9 / 1.1. Show the zoom as `1.25×`.
- **Darkness:** a range input from 0 to 80, step 5, showing `30%`.
  - It sends `dim` with `amount = value / 100` on `change` (when released), not on `input`.

Logo: position and size only.

**Swatches:**
- A row of round buttons, 22 px, each filled with the palette colour.
- The fill goes through a `safeHex(hex)` helper: it returns the hex only if it matches
  `/^#[0-9a-f]{6}$/i`, otherwise no fill.
- Each button has `aria-label="Gold"` (the `name`) and a `title` that also lists `used_by`.
- The current colour has a 2px `--text` ring and `aria-pressed="true"`.
- Clicking another swatch sends `set_color` with its `token`.

### Style tab

If `style` is null, show "This poster was made from a fixed template, so it has no style
settings." Otherwise:

- Show the design `title`.
- **One `<label>` and `<select>` per setting.**
  - The label is the setting's `label`; each option shows its `label` and sends its `value`.
  - The current `value` is selected.
  - On change, send `{restyle: {[name]: value}}`.
- **Designs:** one button per entry in `designs`.
  - The current one is disabled and marked "Current".
  - One with `available` false is disabled, with `why` shown under it in muted text.
  - Clicking one sends `{restyle: {family}}`.
- Below, in muted text: "A style change keeps your colours, darker photos, hidden parts and
  logo size."

### Measure tab

1. **A checkbox, "Show on the poster".** It draws an overlay on the big poster in the Canvas
   view only. Draw nothing in Feed, Story or Grid.
2. **A list in words**, built from `measure`:
   - one line per text layer and the logo: "Headline: 80 px from the left, 303 px from the
     bottom" (the two smallest margins);
   - one line per gap: "Headline → offer line: 32 px";
   - "Lined up on the left: headline, offer line, call to action" (from `aligned_left`);
   - for 9:16 only: "Inside the story's safe area" or "Outside the story's safe area: logo".

**The overlay:**
- An inline `<svg>` placed absolutely over the hero image, with
  `viewBox="0 0 {canvas.width} {canvas.height}"` and `pointer-events: none`.
- For each visible text layer, its `ink` rectangle; for the logo, its `px` box.
- Each margin smaller than 25% of `short_side_px`: a line to that edge, labelled with its px.
- Each gap: a line between the two blocks, labelled with its px.
- `aligned_left`: one vertical guide line per group.
- `story_safe`: shade the area outside the band.

**Overlay colours (AGENTS UI rules: the poster is the only colourful thing):**
- every line is drawn twice: a 3 px `rgba(0,0,0,.55)` stroke under a 1 px `#fff` stroke;
- labels are 11 px monospace, white on a `rgba(0,0,0,.6)` rectangle;
- no accent colour.

## Part 3 — Brand colours by the chat box

Under the `#tweak` input, add a muted line "Brand colours:", followed by the palette swatches
at 14 px, each with its `name` next to it in 12 px text.

They are a reference for typing. Clicking one adds the colour name at the cursor in `#tweak`
(for example `gold`) and does nothing else.

## Tests (`test_ui_safety.py`)

- The page calls `/inspect` and `/edit`, and has `sendEdit`, `safeHex` and the result
  labels "Asked you" and "Didn't work".
- The functions that build the editor contain no `innerHTML`: `paintEditor`, `layerRow`,
  `paintInspector`, `paintStyle`, `paintMeasure`, `measureOverlay`, `choiceButtons`. Check
  the same way as `optionCard` is checked today.
- `safeHex` tests its input against `^#[0-9a-f]{6}$`.
- There is no new external URL, script or font.

## Not in this brief

- **Dragging layers on the canvas.** The buttons send the same ops; dragging comes later
  and will reuse `sendEdit`.
- **Undo and redo buttons.** Typing "undo" or "start over" already works.
- **Keyboard shortcuts beyond Shift for bigger steps.**
