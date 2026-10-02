# M15a — The Taste Lab: people pick between good designs, which is what taste is

**Status:** written by Claude, 2 Oct 2026.

**Review flags:**
- **One migration** (new table `stratum.lab_screens`, its row-level security and grants):
  flagged below for line-by-line review.
- No change to auth or token code.
- No new dependency.
- No spec schema change.

## Why

Critic v1 learned "an original beats a damaged copy of itself", so it can tell broken
from fine. Among the 648 valid designs it mostly scores noise (taste plan, 30 Sep). More
damaged copies cannot fix that: the information is not in those labels. Taste only comes
from people choosing between good designs.

The research (taste plan) says three things:
- designers agree with each other only about 74% of the time;
- off-the-shelf vision-language models are near chance on close calls;
- small models trained on real human choices do learn.

So the plan is: collect choices, measure the human ceiling, then train critic v2 (M15b)
and report it against that ceiling.

## What it does

### Screens (`design/lab.py`, `app/lab.make_screens`, job `lab_screens`)

1. **Which posts:** the agency's newest posts that have words and a client with photos.
2. **Material:** each post's words, the client's best photos and logo, read exactly as for
   the top three (`options.material_for`, shared now).
3. **Ranking:** up to 240 designs laid out and ranked (critic and framing cost), in 4:5.
4. **Each screen has four different designs:**
   - two from the ranking's top 12, of different families where possible: the close calls
     between good designs, where v1 is blind;
   - one from the middle of the ranking: does the order mean anything;
   - one from anywhere among the valid designs: what the ranking never shows.
   - Each keeps its bucket, rank and score for the walkthrough.
5. **Six screens per post by default.** The first three are **shared**: every rater sees
   them, so agreement between people can be measured.
6. **It runs as a job,** about 2–3 s of CPU per post, as the person who asked
   (`factory(job)`), so row-level security applies.

### Rating (`app/lab.next_screen`, `answer`)

- **Shared screens come first** for every rater.
  - After that, screens with the fewest answers come first, to spread coverage.
  - After 10 answers, 1 in 10 is a **repeat** of a screen the rater answered earlier (not
    one of their last 5). That measures self-consistency.
- **Each serving shuffles the order** per rater. The server works the order out again from
  `(screen, rater, served)` when the answer arrives, so the page cannot change what is
  recorded.
- **An answer** is a pick (or "none of these"), any designs marked "never send", optional
  reasons from a fixed list, and the time taken.
  - It is checked: designs on the screen, a pick that is not also "never send", known
    reasons, a sane time.
  - It is stored as one `decision_log` row: kind `designer_pick` (existing), `version_id`
    null, detail `{source: "lab", screen, served, picked, never, reasons, ms, order, repeat,
    shared, design_ids}`.
  - The same serving sent twice is one answer.
- **Who can rate:** anyone in the agency, viewers included, because `decision_log` already
  lets any member append their own decisions. Making screens needs an owner or designer.

### Numbers (`summarise`)

- **Agreement on shared screens:** the share of rater pairs who picked the same design (or
  both picked none). This is the human ceiling.
- **Self-consistency:** a rater's repeats matching their first answer.

### Export

`GET /api/lab/export` returns JSONL: every answer with its four designs (id, family,
bucket, rank, score, spec). Raters are numbered, not named. This is what the critic v2
walkthrough reads.

### Endpoints

| endpoint | what |
|---|---|
| `POST /api/lab/screens` | queue the job |
| `GET /api/lab/next` | the next screen for the rater |
| `POST /api/lab/screens/{id}/answer` | record an answer |
| `GET /api/lab/progress` | counts, agreement, self-consistency |
| `GET /api/lab/export` | every answer, as JSONL |
| `GET /previews/lab/{screen}/{design}/{shape}.webp` | one design's picture, cached for good |

## MIGRATION — for line-by-line review

`supabase/migrations/20261002080317_lab_screens.sql` follows the same rules as
`post_options` (reviewed, and smoke-tested live on 1 Oct).

- **New table `stratum.lab_screens`.**
  - Columns: id, agency_id, post_id, shared, designs, detail, created_by, created_at.
  - `designs` must be a JSON array of 2–6 entries.
  - Deleted with its post (foreign key cascade).
- **RLS enabled.**
- **Select:** own agency only.
- **Insert:** own agency, **and** the post must be the agency's own.
- **Restrictive insert:** owners and designers only. Viewers cannot make screens, but can
  rate them.
- **Grants:**
  - authenticated gets select and insert only. Update, delete and truncate are revoked
    explicitly.
  - anon and public get nothing.
- **No change to `decision_log`:** answers use the existing kind and the existing append
  policy.

**Tested** in `test_lab_contract.py` (10, against Postgres, as the PostgREST roles):
- own screens: write and read;
- another agency sees nothing;
- a screen cannot point at another agency's post, with either agency id;
- a viewer cannot make screens but can rate them;
- no update or delete;
- anon cannot read;
- 2–6 designs;
- screens go when their post is deleted.

**Each guard broken once on purpose:**

| guard removed | test that fails |
|---|---|
| the post check | the cross-agency test |
| the update/delete revoke | the "never changes" test |
| the viewer policy | the viewer test |

All 66 Postgres contract tests pass with the migration.

**Applied live on 2 Oct** (Supabase version 20261002080317), with Rushil's OK. A smoke
test as the demo owner, rolled back, inserted a screen for its own post and read it back.
Privileges live: authenticated select and insert only (no update or delete), anon
nothing, RLS on, three policies.

## How Rushil uses it

1. **Make posts.** About 15–20 posts across clients, with real photos and varied briefs.
   Posts already made count.
2. **Make screens.** Taste Lab → Make more screens: 6 per post, 3 of them shared.
3. **Rate.** You, plus two friends invited to the agency as viewers.
   - Each rates about 150 screens, about 20 minutes at 8 s a screen.
   - Your labels are tagged as yours in the export.
4. **Before critic v2 (M15b): download the labels.**
   - The walkthrough reports agreement (the ceiling), self-consistency, and v1 against a
     coin flip on good-vs-good comparisons.
   - Expect v1 near 50%: the chart that proves the problem.

## Tests

- `test_lab_sampling.py` (7): four different designs; two from the top of different
  families; one middle and one any; shuffled; reproducible; short rankings; one family.
- `apps/api/tests/test_taste_lab.py` (15, on a real draft), covering:
  - screens made from the agency's posts;
  - shared screens first, with pictures;
  - the answer stored with the order shown;
  - the same serving twice is one answer;
  - impossible answers are sentences (4);
  - "none of these";
  - agreement measured between two raters;
  - the order varies and is reproducible;
  - repeats measure self-consistency;
  - done when everything is rated;
  - the export;
  - the job runs as the person who asked.
- `test_lab_contract.py` (10, Postgres).

**One bug found by the tests:** `RECENT = 0` made every screen "recent", because
`seen[-0:]` is the whole list. Fixed.

## Risks

- **Few raters, few posts.** Every number is reported with its count. The walkthrough
  adds intervals.
- **The rater is Rushil.** His labels are tagged. Friends dilute a single person's taste.
- **Posts drafted before photos were uploaded** are skipped and listed in the job result.
- **The lab's 4:5 pictures only.** Taste in stories may differ. Later.

## Five things to be able to say

1. Taste only comes from people choosing between good designs, so the lab shows four
   designs for one real post and asks which you would send the client.
2. Each screen mixes two top-ranked designs of different families, one middle and one
   from anywhere, so the labels test the ranking instead of flattering it.
3. Shared screens measure agreement between people, the ceiling any model can reach.
   Repeats measure each rater's own consistency.
4. The display order is shuffled per rater and worked out again by the server, so a
   "first one" habit can be removed and the page cannot change what is recorded.
5. Screens are stored in a table only the agency can read and nobody can change. Ratings
   use the existing decision log, so viewers can rate without being able to edit.

## For the ADR (Rushil writes it)

- Lab answers live in `decision_log` with `source=lab`, rather than in a new answers
  table.
- Screens are made from the agency's own posts, rather than from public datasets
  (licences) or procedural photos (realism).
- Four designs per screen, in the buckets above, with a 10% repeat rate.
