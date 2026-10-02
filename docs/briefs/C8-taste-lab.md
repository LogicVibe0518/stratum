# C8 — The Taste Lab page: pick the one you would send

**Branch:** `feat/ui-taste-lab` · **Who:** Cursor · **Reviewer:** Claude
**Needs:** M15a merged and its migration applied live.

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C8-taste-lab.md, and implement it exactly. Only edit
apps/api/src/stratum_api/static/index.html and apps/api/tests/test_ui_safety.py. Do not change
any Python in apps/ or packages/. Every string from the server goes through esc() or
textContent/setAttribute. Run make check. Do not commit or push. Finish with a report: what you
changed, what you could not do, anything you guessed.
```

## Why

Critic v1 can tell a broken design from a fine one, but it cannot tell which of two good
designs a person prefers. That only comes from people choosing.

The Taste Lab shows a rater four designs for one real post and asks one question. Their
answers train critic v2. The labels are only worth something if the page does not nudge
the choice, so:
- no scores, design names, families or ranks, ever;
- the cards appear in the order the server gives.

## The API (M15a)

**`GET /api/lab/next`** returns one of two shapes:

```
{ done: false, screen_id, served, repeat,
  post: {brief, client},
  cards: [{design, preview}],    // in display order; preview has {shape}: use "4x5"
  reasons: ["Hard to read", ...],
  progress }

{ done: true, progress }
```

**`POST /api/lab/screens/{screen_id}/answer`** takes:

```
{ served, picked: <design> | null, never: [<design>...], reasons: [...], ms, repeat }
```

- `picked` and `never` are the cards' `design` numbers, not their positions on screen.
- `ms` is the time from when all four pictures had loaded to when Next was clicked.
- `repeat` is the value `next` returned.
- It returns `{ok, progress}`. A 422 carries a sentence: show it.

**`progress`** looks like:

```
{ screens, shared_screens, answers, mine, raters,
  agreement: {pairs, rate|null},
  self_consistency: {repeats, rate|null} }
```

**Other endpoints:**
- `POST /api/lab/screens` with `{}` queues a job that makes screens from the agency's
  posts. It returns `{job_id}`. Poll `GET /api/jobs/{id}` the way drafts are polled.
- `GET /api/lab/export` downloads every answer as JSONL. Fetch it with the auth header and
  save it as `taste-lab.jsonl`.

## The page

1. **"Taste Lab" in the top bar**, next to System. It opens a new `<section id="lab">`.
2. **Heading:** "Which would you send the client?" Under it, in muted text, the client name
   and the brief.
3. **The cards: a 2×2 grid**, in the order of `cards`.
   - Each card is a `<button>` with the 4:5 picture, loaded through the same authenticated
     preview loader as options.
   - `aria-label` is "Design 1" … "Design 4", numbered by position on screen.
   - Clicking a card makes it the pick: a 2px `--focus` border and the text "Picked". Only
     one card can be the pick.
   - Under each card, a checkbox "Never send". A card cannot be both the pick and "never
     send": ticking one clears the other.
4. **"None of these"**, a secondary button. It sets the pick to null. Clicking any card
   cancels it.
5. **Reason chips:** one toggle button per entry in `reasons` (`aria-pressed`), under a
   muted label "Optional: anything wrong?"
6. **Next**, the primary button. It is disabled until there is a pick or "None of these".
   - It sends the answer, then loads the next screen.
   - While sending, it shows "Saving…" and disables everything.
7. **Keyboard:**
   - `1`–`4` pick a card by its position on screen;
   - `0` is "None of these";
   - `Shift` + `1`–`4` toggles "Never send";
   - `Enter` is Next.
   - Put this on one muted line under the grid.
8. **The progress line**, from `progress`, for example "You have rated 23 · Raters agree
   64% (18 pairs) · Your repeats match 4 of 5".
   - Leave out a part when its rate is null.
   - Never say which screen was a repeat.
9. **When `done` is true:** "You have rated every screen."
   - Owners and designers also see a "Make more screens" button. It queues the job, shows
     "Making screens… about a minute per few posts", polls the job, then loads the next
     screen.
   - If the job's result has `screens: 0`, show "No posts with words and photos yet.
     Make a few posts first."
10. **A small "Download labels" link** at the bottom (the JSONL export).
11. **Phone:** keep the 2×2 grid with smaller cards. Put the chips and Next under the grid.
12. **Colours:** monochrome chrome only. The posters are the only colour.

## Tests (`test_ui_safety.py`)

- The page uses `/api/lab/next`, `/answer` and `/api/lab/progress`, and contains "Which
  would you send the client?".
- `paintLab`, `labCard` and `labReasons` contain no `innerHTML` (checked like
  `optionCard`).
- The page never shows `bucket`, `rank`, `score` or `family` from a lab response. Check that
  those words are not read anywhere in the lab functions.
- There is no new external URL, script or font.
