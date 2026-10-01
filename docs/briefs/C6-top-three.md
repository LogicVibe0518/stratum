# C6 — Best three: show three options, pick one

**Branch:** `feat/ui-top-three` · **Who:** Cursor · **Reviewer:** Claude · **Needs:** M12b
merged (the API below).

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C6-top-three.md, and implement it exactly.
Only edit apps/api/src/stratum_api/static/index.html and
apps/api/tests/test_ui_safety.py. Do not change any Python in apps/ or packages/.
Keep esc() or textContent for everything shown. Run make check. Do not commit or push.
Finish with a report: what you changed, what you could not do, anything you guessed.
```

## Why

M12b lets Stratum lay out many designs around a post's words and photos and keep
three different ones. The server already does it; the page only shows the first.
This brief shows all three and lets the designer pick. Every pick is recorded with
what was shown, which is what the design critic will learn taste from later - so the
page must show the three in the order the server gives, and must not pre-select or
highlight one as "recommended".

## The API (already built in M12b)

- `GET /api/designs` now starts with `"best_three"`. It is the first option of the
  design picker, so it is the default.
- `GET /api/posts/{id}` has a new field `options`: `[]` for a post made from one chosen
  design, or three objects in display order:
  `{id, name: "A"|"B"|"C", position: 0|1|2, design_id, title, preview}`.
  `preview` is a URL with `{shape}` in it: replace `{shape}` with `4x5`, `1x1`,
  `9x16` or `16x9`.
- `POST /api/posts/{id}/options/{option_id}/pick` makes that option the poster and
  returns the version (same shape as a quick change). Picking the option that is
  already the poster is fine and returns the same version. A 4xx carries a sentence
  in `detail`: show it.
- `post.template_id` is the design currently shown. After a pick it is the picked
  option's `design_id`.

## 1. The design picker

- Label `best_three` as **Best three (Stratum chooses)**. Every other id keeps the
  current `designLabel()`.
- No other change to the new-post form.

## 2. The options strip on the post screen

When `options` is not empty, show a strip of three cards **under** the big poster,
before the tweak box, titled **Options**:

- Each card: the 4:5 preview image (`preview` with `4x5`), lazy-loaded, and under it
  `Option A · Two photos` (name and title).
- In the order of the array. Never reorder, sort or highlight one.
- The card whose `design_id` equals `post.template_id` has a 2px border in the focus
  accent and the text `Shown` after its title. The others have a 1px grey border.
- Clicking a card that is not shown: disable all three cards, show "Switching…" in
  the status line, `POST .../pick`, then reload the post exactly as after a quick
  change. On error, re-enable the cards and show the server's sentence.
- Clicking the shown card does nothing.
- Keyboard: each card is a `<button>` with `aria-label="Use option A, Two photos"`.
- On a phone (narrow screen) the strip scrolls horizontally; cards are 40% of the
  screen width.
- Monochrome chrome only (AGENTS.md UI rules); the posters are the only colour.

## 3. The posts list

- Where the list shows a design name, use the post's design as today. A post still
  being drafted shows `best_three` - label it **Best three** like the picker.

## 4. Tests (`test_ui_safety.py`)

- The page contains `best_three` handling and builds option cards with `textContent`
  or `esc()` (extend the existing "no raw innerHTML with server strings" checks to the
  new code).
- No new external URL, script or font.

## Not in this brief

- Showing scores, "recommended" badges or explanations: deliberately not shown, so
  the designer's pick is their own (it is the training label).
- Re-drafting a new set of three ("Try again"): that is C4.
