# M16a — The editor, phase 1: the poster canvas in React

**Status:** written by Claude, 21 Oct 2026, from Rushil's brief ("make it really really good …
something common in most editor tools so the interface is not new to them … take super care").
Served at `/app`. The page at `/` is untouched and stays until the editor does everything it does.

**Review flags:**
- **New app, new dependencies (apps/web, Node 22).** Runtime: React, Radix (dropdown, popover,
  slider, tooltip), TanStack Query, sonner, lucide-react (ISC), DOMPurify (used under its Apache-2.0
  option). Build/test: Vite 7, TypeScript, Vitest, jsdom, Prettier. Every version pinned exactly in
  `package.json` and locked in `package-lock.json`.
- **Licences outside the AGENTS.md list, for your OK:** `tslib` (0BSD — the "zero-clause" BSD, a
  public-domain-like BSD variant; pulled in by Radix, ships in the page) and `caniuse-lite`
  (CC-BY-4.0 browser data that Babel reads at build time; never shipped). Avoided on purpose:
  Tailwind CSS and Vite 8, which both pull `lightningcss` (MPL-2.0).
- **AGENTS.md:** the Stack line and the `apps/web` repo-map line now say Vite + React instead of
  Next.js (your decision of 21 Oct). Worth an ADR in your words: why Vite + React served by the API
  rather than Next.js on its own host.
- **Dockerfile:** a `node:22-slim` stage builds the editor; the Python image copies `dist/` in.
- **CI:** a new `web` job (`npm ci`, then Prettier, TypeScript, Vitest, build). `make check` and the
  pre-push hook are unchanged (no Node needed to push Python work).
- **New spec op `place`** (in `EditOp` and the panel's allowlist). The chat prompt is unchanged, so
  the model is never told about it; if it sent one, it would be validated like any op.
- **New routes:** `GET /api/posts/{id}/canvas`, `POST /api/posts/{id}/canvas/try`,
  `POST /api/posts/{id}/restore`, `GET /api/versions/{v}/assets/{sha}`, `GET /fonts/{name}`,
  `GET /app/...`. All data routes run as the caller (RLS); `/fonts` and `/app` serve only files from
  `assets/fonts` and `apps/web/dist`.
- No migration, no RLS change, no auth or token code. The editor signs in exactly as the old page
  does (same Supabase password flow, same `stratum.session` key, so one sign-in covers both).

## Why

Designers at the agency work in Canva all day. The old page edits by panel and by chat; it cannot be
grabbed. Every small layout fix ("move it here", "a bit bigger", "line it up") cost a message. The
editor makes those fixes direct, with the gestures they already know, and keeps every Stratum rule:
the poster is still a spec, every change is still a validated op and an immutable version.

## What changed

### Backend

1. **`place` op** (`packages/spec/src/stratum_spec/ops.py`). A part at an exact box in **one** size:
   `{layer, aspect, box, text_size?, max_lines?}`. In another size it writes
   `spec.variants[aspect][layer]`; in the base size it changes the layer and **pins** every other size
   that was using the base values, so a drag in one tab never moves another size. Words and logos stop
   at the poster edge; photos and shapes may bleed. `text_size` (words only) is what a corner drag
   sends, so the words grow with the box; `max_lines` is what a side drag sends, so a narrower column
   takes more lines at the same size instead of shrinking the words until they are cut short.
   History says "headline placed in the story". Two feature-walk rows.
2. **Older posters** (before schema 3 carried their own sizes) get their template's sizes embedded
   before the first `place` (`service.with_own_layout`), or the other sizes would lose their layout.
3. **The canvas** (`service.canvas_view`). The same `build_svg` as the finished PNG, with each photo's
   file path swapped for `/api/versions/{v}/assets/{sha}` (`render/svg.relinked`; framing still reads
   the real files), plus `inspect()` and the brand's font files. With `raw_ops`, the poster those
   changes would make, saved nowhere: what the canvas shows within a moment of letting go, and while
   words are typed or a slider moves.
4. **Undo and redo by name** (`actions.restore`). Typed "undo" goes back one step from whatever is
   newest, so a second undo undid the first; an editor keeps its own list of states and names the one
   it wants. Refused (409) when the poster moved on, so a double press is one step. Shares
   `_bring_back` with typed undo, which behaves exactly as before.
5. **Assets for the browser** (`service.version_asset`, `web_copy`): only an asset that a version the
   caller can see draws (a subject matte has no asset row, so the version decides), at most 2048 px,
   JPEG for photos and PNG for anything see-through, made once and kept, marked immutable.

### The editor (`apps/web`)

- **Layout like Canva:** top bar (back, name, size tabs, undo/redo, "All changes saved", status,
  Download), left rail (Layers, Brand), context toolbar above the canvas, Ask Stratum on the right.
- **Canvas:** hover outline; click to select; drag with snapping guides (poster edges and centre, the
  margins the server aligns to, a story's safe band, other parts' edges and centres) and a position
  tag; Shift keeps one axis; Alt turns snapping off; corner handles keep the shape (words scale with
  it), side handles move one edge (words re-flow); arrows nudge 1 px, Shift+arrows 10 px, saved 400 ms
  after the last; double-click words (or Enter) to type in place in the brand font; Delete hides with
  an Undo toast; Tab walks the parts; zoom with the buttons, ⌘+/−/0 or a pinch. A backdrop is selected
  but never dragged by accident.
- **Toolbar by selection:** words (edit, font, size −/+, brand colours, align, case, effect), photo
  (darken, flip), shape colour, transparency, Position (layer order, align to page in this size), hide.
  Sliders show the change live and keep it on release, on close and when the selection moves on.
- **Ask Stratum:** the existing chat (`/tweak`), pointing at the selected part, with the answer's
  choices as buttons, suggestion chips, and the checks' warnings as cards.
- **How it stays honest** (`lib/session.ts`): every change shows at once, is saved in order, one at a
  time (waiting changes are merged), and the server's own picture replaces the guess within a moment.
  A refusal puts the part back and says why; a 409 reloads and says the poster changed elsewhere.
- **Safety** (`lib/poster-svg.ts`): the SVG is cleaned by DOMPurify, parsed as XML (an HTML parse
  lower-cases `feDropShadow` and the shadow silently disappears), its ids prefixed per poster, and its
  pictures fetched with the person's token. A test fails if any source writes HTML from a string,
  uses a browser dialog or loads anything from another site but the interface font.

## Decisions

- **Vite + React served by the API** (Rushil, 21 Oct) over Next.js: one service, one origin (the
  session and RLS work unchanged), static files cached forever.
- **The browser draws the server's SVG** instead of re-implementing the renderer: what the canvas
  shows is what downloads, and words break exactly where they will (same font files, same line
  breaks from the server). Downloads and the Canva file stay server-rendered.
- **`place` instead of `move`/`scale` with an aspect** (the first plan): exact boxes are what a drag
  produces, and per-size by construction. `move` and `scale` are unchanged for the chat.
- **Plain CSS with the old page's tokens** instead of Tailwind (licence, above). Monochrome chrome,
  one accent (blue) for focus, selection and guides, status colours only for status.

## Try it

```bash
make api        # tab 1
make web-dev    # tab 2: http://localhost:5173/app (or `make web` and open http://localhost:8000/app)
```

## Tests

- `packages/spec/tests/test_place.py` (13): one size only, pinning, edges, words sized, more lines,
  refusals, input never changed, round trip.
- `apps/api/tests/test_canvas_api.py` (14): SVG points at URLs not files; try saves nothing and is
  checked; 409; a story drag keeps the post; assets only for a version that draws them (bad sha, bad
  version id, other agency → 404, cached, ≤2048 px); restore undo/redo/409/missing; fonts only from
  `assets/fonts`; `/app` serves the build and falls back to the page.
- `apps/web` (42, `npm run check`): geometry 21, session 9 (fake server: merge, preview never
  repeats a flip, local undo, restore, 409 reload, refusal roll-back, per-size overrides, chat order),
  poster-svg 6 (on the server's real SVG), colours 1, safety 5.
- In Chromium against the real API on the fake world: select, drag with guides, corner resize (the
  server drew the words at exactly the size the canvas showed), side resize re-flow, undo/redo, story
  tab, typing in place, nudges, darken slider, hide + Undo, chat, dark mode.

## Five things a reviewer can say back

1. The canvas is the server's own SVG, cleaned and drawn in the page; a drag moves the layer's wrapper
   until the server draws the new version.
2. Every gesture becomes one validated op (`place`, `set_text`, …) through the same `/edit` as the
   panel, with the version check: nothing new can reach the spec.
3. `place` changes one size only; in the base size it pins the others, so tabs are independent.
4. `/canvas/try` shows the server's picture of unsaved changes; it writes nothing and runs no model.
5. Undo/redo name the version to restore; versions are never overwritten.

## Not in this phase

Crop mode, drag-to-reorder layers, History strip, Designs and Photos rails, ⌘K, shortcut sheet,
right-click menu, multi-select, rotation handle, rename, Share for review, warnings with a fix button,
a browser smoke test in CI (run by hand for this PR). Parts that belong together move apart today: a
logo and the panel drawn behind it, a headline and its band (phase 2: groups, as Canva groups them).
