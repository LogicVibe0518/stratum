# M12b — Best three: Stratum lays out many designs and shows three different ones

**Status:** written by Claude, 1 Oct 2026. **One migration** (new table
`stratum.post_options`, its row-level security and grants) — **flagged below for
line-by-line review.** No change to auth or token code. No spec schema change. No new
dependency.

Also in this PR: log lines as JSON on stdout (`stratum_worker/logs.py`), because
Railway showed every line as an error.

## Why

A designer choosing between three good, different options is faster than editing
one. Every choice is also a label: "of these three, they picked this one". That is
what the critic needs to learn taste (see the project's taste plan). The labels are
only usable if we record what was shown, where it was shown, and how likely it was to
be shown. A model retrained only on picks would otherwise learn to prefer whatever it
already showed.

## What it does

A post whose design is `best_three` (now first in the design picker) is drafted like
this (`design/choose.py`, `app/options.py`):

1. **Copy, photos, logo** are prepared as for any post. The copy is written to the
   character budget of an overlay design, which has the least room among the
   one-photo families.
2. **Candidates:** every design the material can fill.
   - Two-photo designs need two real photos; real photos are never invented.
   - One-photo designs need a photo or the post's generated background.
   - Words-only designs need nothing.
   - Up to 240 are sampled evenly across families, with a seed from the post id, so
     the same post always gets the same candidates.
3. **Round 1, the post's own size:** each design is laid out around the real words,
   gets its logo panel and has its photo framed (M10b). It is then scored:
   `critic z-score − 0.5 × framing cost − 0.75 if it shows none of the client's
   photos`. Designs with a layout blocker are out.
   - The framing cost is the M10b cost of the chosen crop. A face under the words
     costs about 12, so such a design drops about 6 standard deviations.
4. **Round 2, every size:** the best 24, plus at least the best 4 of each family, are
   framed in every size the post needs. They are scored per size and averaged. A
   blocker in any size removes the design.
5. **Three from different families.** The best, then the best of another family,
   then the best of a third.
   - With probability 0.15 the third is drawn at random from the shortlist instead,
     preferring families not already shown.
   - Each option's **propensity** (the probability this policy shows it) is stored:
     1 for the first two, and for the third
     `(1 − 0.15) × [it is the greedy choice] + 0.15 / |pool|`.
6. **Display order is shuffled** (seeded) and stored, so a "first one" bias can be
   measured and removed later.

**Version 1 is the ranking's best**, so everything that shows "the poster" keeps
working. `posts.template_id` becomes that design id, so tweaks, quick changes and the
brief check work exactly as for a chosen design.

**Picking** (`POST /api/posts/{id}/options/{option_id}/pick`):
- makes that option the next version and sets `template_id` to its design;
- writes one `decision_log` row of kind `designer_pick` (the kind already existed)
  with the batch, the option picked, its slot and position, and every option shown
  with its propensity;
- picking the option already shown records the choice without a new version;
- the same pick twice is one decision.

**Reading:**
- `GET /api/posts/{id}` has `options` (three objects in display order, or `[]`).
- `GET /api/posts/{id}/options` returns the same list.
- `GET /previews/options/{id}/{shape}.webp` renders an option on demand and caches
  it, like a version.

**Time:** about 2–3 s of CPU for the ranking, run in a thread, on top of the usual
draft.

## MIGRATION — for line-by-line review

`supabase/migrations/20261001081217_post_options.sql`:

- **New table `stratum.post_options`.**
  - Columns: agency_id, post_id, batch, slot 1–3, position 0–2, design_id, spec,
    score, propensity in (0, 1], explored, detail, created_by, created_at.
  - `unique (batch, slot)` and `unique (batch, position)`.
  - Deleted with its post (foreign key cascade).
- **RLS enabled.**
- **Select:** only rows of the caller's agency.
- **Insert:** the row's agency must be the caller's, **and** the post must belong to
  the caller's agency. This is stricter than the older tables: it stops a row that
  carries our agency id but someone else's post id.
- **Restrictive insert policy:** only owners and designers, the same rule as posts;
  viewers cannot write options.
- **Grants:**
  - authenticated gets select and insert only. Update, delete and truncate are revoked
    explicitly, because the schema's default privileges grant everything to
    authenticated.
  - anon and public get nothing (anon already has no access to the schema; the revoke
    is belt and braces).

**Tested** in `test_options_contract.py` (9 tests, as the roles PostgREST uses):
- own rows: write and read;
- another agency sees nothing;
- an option cannot point at another agency's post, with either agency id;
- a viewer cannot write;
- no update or delete;
- anon cannot read;
- one option per slot and per position;
- propensity bounds;
- options go when their post is deleted.

**Each guard broken once on purpose:**

| guard removed | test that fails |
|---|---|
| the post check | the cross-agency test |
| the update/delete revoke | the "never changes" test |
| the viewer policy | the viewer test |
| the anon revoke | none: anon never had access to the schema, so the revoke is defensive |

**Applied live on 1 Oct** (Supabase version 20261001081217, with Rushil's OK). A smoke test
as the demo user, rolled back, inserted an option for its own post, read it back, and was
refused an update.

## Logs

- `stratum_worker/logs.py`: every log line goes to stdout.
  - On Railway (when `RAILWAY_ENVIRONMENT_NAME` is set) it is one JSON object per line,
    with a `level` Railway understands: debug, info, warn, error.
  - On a laptop it is the same short line as before.
- The worker uses it.
- The API's uvicorn uses `infra/uvicorn-logging.json` (Dockerfile CMD
  `--log-config`).
- Before: "Application startup complete" and "4 workers waiting" were red in
  Railway, and a real error looked the same.

## Tests

- `test_top_three.py` (14), covering:
  - eligibility by photos;
  - an even, repeatable sample;
  - a two-photo design gets both photos;
  - the same post gives the same ranking;
  - designs that would put words on a face are filtered out;
  - three different families;
  - exact propensities, including exploration on and off;
  - fewer than three families;
  - the exploration rate;
  - draft → three options and version 1 = best;
  - pick → new version, `template_id` and a decision with everything shown;
  - the same pick again → one decision;
  - picking the shown option → logged, no new version;
  - an option of another post is refused.
- `test_options_api.py` (5): best three is first in the picker; three options with
  pictures; a pick makes the next version; an unknown option is refused clearly; a
  one-design post has no options.
- `test_options_contract.py` (9, Postgres; see above).
- `test_logs.py` (4).

**By eye:** three options for a two-photo post (public-domain test photos, not in
the repository): a panel, an overlay and a two-photo design, with no face under any
words.

## Risks

- **Critic v1 is a defect gate, not taste.** Among valid designs, the order of the
  options is mostly the framing cost and diversity. The picks this records are what
  fixes that (Taste Lab, M15).
- **A sample of 240 of 648 designs** can miss the single best one. It is seeded per
  post, so it is repeatable and testable.
- **The copy is written once**, to an overlay's budget. A design the words do not fit
  is not a candidate. It is never truncated.
- **The UI shows only version 1** until Cursor brief C6 lands. Picks need that UI.

## Five things to be able to say

1. "Best three" lays out up to 240 designs around the real words and photos, scores
   each with the critic and the photo framing cost, re-scores the best in every size,
   and keeps three from different families.
2. A design that would put words on a face drops about six standard deviations, so it
   is never shown when anything else fits.
3. Every option stores its display position and its propensity, the probability the
   policy had of showing it. The third slot explores 15% of the time, so future
   training can correct for what the ranking favoured.
4. Version 1 is the best option and the post becomes that design, so tweaks and quick
   changes work as before. A pick is a new version plus one `designer_pick` decision
   with everything that was shown.
5. The new table only lets an agency see and add options for its own posts. Viewers
   cannot add, nobody can change or delete, and each guard has a test that fails
   without it.

## For the ADR (Rushil writes it)

- Options in their own table rather than as extra versions: a version is something
  the poster was; an option is something that was shown.
- Exploration rate 0.15 on one slot of three, with propensities logged (enables
  off-policy evaluation in M15).
- The copy is written before the design is chosen, to the tightest one-photo budget.
