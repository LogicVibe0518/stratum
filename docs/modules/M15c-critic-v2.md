# M15c — Critic v2 from people's answers, and a bar for "good enough"

**Status:** written by Claude, 6 Oct 2026. It builds on M12a (critic v1), M12b (the
top three), M15a (the Taste Lab) and M15b (lab screens).

**Review flags:**
- **Two new model files the product reads, both absent until Rushil trains them:**
  - `critic/model/ACTIVE` names the critic in use. Without it, v1 is used, as today.
    A switch naming a missing model raises at first use, rather than quietly
    scoring with something else.
  - `critic/model/bar.json` is the quality bar. Without it there is no bar, and
    drafting is exactly as before.
- **A draft can do more work:** only when a bar is shipped and none of the top
  designs clears it. The repair steps are bounded by `repair.BUDGET_S` (30 s) and run
  in the queued draft job, never in a request.
- **New lab answer fields:** `LabAnswer.send` (the designs the rater would send) and
  `LabAnswer.role` (designer, design_student, other). Both are optional; an older
  page works unchanged. Stored in `decision_log.detail`.
- **New flag:** `below_bar` (a warning; nothing is blocked or sent).
- **`.gitignore`:** `ml/critic/labels/` (Taste Lab exports hold client posts).
- No migration. No RLS, auth or token change. No new dependency (LightGBM was
  already a dev dependency; the bar is numpy).

## Why

**Critic v1 learned the wrong question for choosing between good designs.** It
learned "a design beats a broken copy of itself", from synthetic groups. It never
saw two good designs compared, so among valid designs its order is close to noise,
and that is the order a designer sees.

**A ranker cannot say "none of these".** It always has a best. When every candidate
is weak (a busy photo, a long headline), the product showed the best of a bad lot
with the same confidence as a good one.

**Whose taste?** Rushil is not a designer. A critic trained on his picks alone would
learn one non-expert's taste. The answer key has to be designers.

## What changed

### 1. Richer answers in the Taste Lab (`app/lab.answer`, issue I-31)

A rater can now say three things about each design, and who they are:

| | best | would send | unmarked | never send |
|---|---|---|---|---|
| label for the ranker | 3 | 2 | 1 | 0 |
| for the bar | good enough | good enough | below | below |

- The best is always one they would send: a pick is added to the ticks, and a
  single tick with no pick is the pick. Several ticks with no pick is refused
  ("Pick the best of the designs you would send.").
- A design cannot be both "would send" and "never send".
- `role` is one of designer, design_student, other. A rater's answers before they
  said take the same role.
- An older answer (no ticks) keeps its meaning: picked 2, never 0, the rest 1. For
  the bar, its unmarked designs are *unknown*: not chosen is not the same as not
  good enough.

The export (`GET /api/lab/export`) now carries `send`, `role` and the critic's 35
measurements of each design (computed at export for screens made before M15c), so
training needs no photos or brand kits.

### 2. Critic v2 data and measures (`critic/taste.py`)

Plain numpy, no database:
- **reading the export**, labels, the bar's labels (`acceptable`, `bar_rows`);
- **folds by post**: every screen of a post is on one side, so a model is always
  tested on posts it never saw;
- **the ceiling**: self-consistency on repeat screens, and agreement between raters
  on the same screen;
- **marks**: `top1` (the pick scored best), `pairs` (pairs in the rater's order),
  `never_last` (a never-send below the pick), `top_ok` (the best-scored design is
  one they would send);
- **95% bootstrap ranges**, and a **paired bootstrap** (both models scored on the
  same resampled answers) for "is v2 really better than v1?";
- **weights**: designer 3, design student 2, other 1 (judgement, stated);
- `simulate`: a made-up rater with a known taste, for tests and the dry run.

### 3. The walkthrough (`ml/critic/walkthrough-v2.ipynb`, `make notebook-v2`)

Rushil runs it on his export. Without the export it runs on made-up answers and saves
nothing (a dry run, about 80 s). The steps:

1. what is in the data, per rater, with answers under 1.5 s left out;
2. the ceiling;
3. baselines with no training: chance, the product's own ranking, the hand-written
   rule, critic v1;
4. v2 trained out of fold by post, two ways: lab only, and lab plus v1's synthetic
   groups (weighted 0.3). Settings and 100 trees are fixed in advance;
5. every scorer on the answer key, and the paired bootstrap against v1;
6. **the decision rule, written before any result.** v2 replaces v1 only if:
   - it learned from at least 150 answers;
   - it orders pairs better than v1 in at least 95% of paired resamples;
   - it is no worse than v1 at `top1`;
   - it still knows a broken design (synthetic `base_wins` at least 0.95).

   **The answer key** is designers' answers when there are at least 100, otherwise
   everyone's, and the report says which.
7. if it passed: the final v2 is saved with fictional proof rows (no client
   measurements in the file) and `ACTIVE` is written;
8. the quality bar (below), with its own rule;
9. `ml/critic/report-v2.json`, and `MODEL_CARD-v2.template.md` to fill.

**In the dry run, the made-up raters show why the synthetic groups are there:**
- lab-only v2 forgets what a broken design is (synthetic `base_wins` 0.49);
- with them it keeps it (0.96; v1: 0.99).

These are made-up numbers and say nothing about real taste.

### 4. The switch (`critic/ranker.py`)

- `active_model()`: the model `ACTIVE` names, else v1.
- `write_active(name)`: only for a model that is in the folder.
- `model_name()`: recorded with every option (`post_options.detail.critic_model`),
  so a pick can always be traced to the critic that ranked it.
- **Going back to v1 is deleting one file.**

### 5. The quality bar (`critic/bar.py`)

The chance a designer would **not** send a design as it is:
- **The model** is a logistic regression on the same 35 measurements, fitted by
  Newton's method with an L2 penalty, in numpy. Small data, readable weights, and
  probabilities the walkthrough checks out of fold (a calibration table and the
  Brier score).
- **The threshold** comes from a stated cost: a bad design let through costs 3
  (`MISS_COST`), a needless look costs 1. It is chosen on out-of-fold predictions.
- **Shipped only if:**
  - there are at least 50 designs on each side;
  - the out-of-fold AUC is at least 0.70;
  - the Brier score beats the base rate alone.

### 6. When nothing is good enough (`design/repair.py`)

In a top-three draft (`app/options.draft_options`), after the ranking:

1. **The top 12 designs are measured against the bar.** Those that clear it go
   first, whatever the critic said, and each option records its `p_bad`.
2. **If none clears it, the repair steps run**, cheapest and likeliest first, and
   stop at the first that works:
   1. the other photo first (when the client sent two);
   2. the shortest headline the copy has;
   3. words-only designs on equal terms (the photo may be the problem);
   4. a wider search: three times the designs, drawn afresh.
3. **If nothing works**, the attempt that came closest is kept and the post gets a
   `below_bar` warning that says what was tried:

   > "None of the designs looks ready to send yet (Stratum's quality bar, learned
   > from designers' choices). It also tried the other photo first, the shortest
   > headline, words-only designs on equal terms and a wider search. Look closely
   > before this goes to the client: a calmer or sharper photo, or a shorter
   > headline, usually helps."

**Bounds:**
- Repairs stop starting after 30 s.
- The steps tried are recorded in `post_options.detail.repairs`.
- The bar never sends, publishes, deletes or blocks anything. It orders and flags.

**A step that was left out on purpose: a darker photo.** The chat can darken a
photo, but the critic's measurements assume any photo under words is mid-grey
(`draft/backdrop.py`), so the bar cannot tell whether darkening helped. The repair
was written, measured, found to change nothing the bar sees, and removed. Measuring
the real photo under the words comes first (issue I-36).

## Results

**No real results yet:** Rushil trains on his export once enough answers exist.
Fill this from `ml/critic/report-v2.json`:

| on the answer key (out of fold) | top1 | pairs | never_last | top_ok |
|---|---|---|---|---|
| chance | | | | |
| the product's ranking | | | | |
| critic v1 | | | | |
| critic v2 | | | | |

**Decision:** {v2 shipped / v1 stays, because ...}. **Bar:** {shipped at threshold
... / not shipped, because ...}.

## Tests

**`test_taste.py`** (19):
- **Labels and fate:**
  - an older answer is picked over the rest over never;
  - ticks add a level;
  - "none of these" orders nothing but is all below the bar;
  - an older answer's unmarked designs are unknown.
- **Reading the export:**
  - ticks and roles are read;
  - lines without measurements are skipped;
  - the wrong width is refused;
  - a rater's role fills their earlier answers.
- **The answer key** is designers plus any rater named.
- **What is trained on:** repeats and answers that order nothing are not.
- **The ceiling:** self-consistency, and agreement between raters on the same screen.
- **Splits:** a post's screens are never on both sides of a fold.
- **Rows:** rows, labels, groups and weights line up; the bar's rows are only the
  designs whose fate is known.
- **The measures:**
  - full marks for a scorer that agrees, none for one that disagrees;
  - chance;
  - bootstrap ranges and the paired difference.
- **A known taste:**
  - a simulated rater is mostly self-consistent;
  - lambdarank recovers its taste out of fold (`top1` more than twice chance,
    `pairs` above 0.75).

**`test_quality_bar.py`** (11):
- the fit finds the measurements that matter and ranks unseen designs (AUC above
  0.8);
- its probabilities line up with what happens (within 0.08 per bin);
- weights change the fit;
- the threshold follows the cost;
- one-sided data is refused;
- AUC and Brier on known cases;
- a missing measurement counts as the average;
- the file round trip;
- other measurements are refused;
- no file means no bar;
- a shipped bar loads.

**`test_critic_switch.py`** (7):
- no switch means v1;
- the switch names a model next to it;
- anything else fails loudly (3 cases);
- scoring follows the switch;
- whatever is shipped loads and proves itself against LightGBM's own scores.

**`test_repair.py`** (8):
- no bar, no change;
- designs that clear the bar go first;
- when nothing clears it, every step runs in order and the post is flagged;
- repairs stop at the first step that works;
- steps that cannot help are not tried;
- out of time, repairs stop starting.
- **In a real draft:**
  - below the bar, the flag is saved and options record the critic, the repairs
    and `p_bad`;
  - with no bar, only the critic is recorded.

**`apps/api/tests/test_taste_lab.py`** (+8):
- ticks and a role are kept and exported with the measurements;
- screens made before measurements were stored are measured at export, to the same
  numbers;
- a single tick is the pick;
- an older page works;
- four refusals as sentences.

## Risks

- **Few raters, one agency.** v2 learns this agency's designers' taste for trades
  clients, not good design in general. The model card says so.
- **Client posts in the export.** The export is git-ignored. The shipped files hold
  trees and weights only, with fictional proof rows. Get the agency's agreement in
  writing to train on ratings of their posts.
- **The bar learns from what raters saw.** Lab screens are chosen to be different
  from each other (M15b), not to be a random sample of drafts, so its probabilities
  are for designs like those.
- **More work in a draft when nothing clears the bar.** It is bounded at 30 s and
  only happens with a shipped bar.

## Not in this module (issue log)

- **The page:** "would send" ticks per card, "never send" as a separate labelled
  toggle, a role picker on first visit, and the `below_bar` flag shown on the post.
  These go in the Day 2 Cursor UI batch (with I-17 to I-21, I-29).
- **Photo brightness and busyness under the words** measured from the real photo, so
  a darker-photo repair can be judged. Issue I-36.
- **The propensities from the top three (M12b)** are not used yet: v2 learns from
  the lab only. Learning from in-product picks needs inverse-propensity weighting
  and more picks.

## Five things to be able to say

1. v1 learned "a design beats a broken copy of itself". v2 learns which of four real
   designs people would send, from Taste Lab answers that rank every design: best,
   would send, unmarked, never.
2. Designers' answers are the answer key and weigh three times as much, so v2 is not
   the builder's taste. Every number is out of fold by post.
3. v2 replaces v1 only on a rule written before the results: a paired bootstrap win
   in 95% of resamples, no loss at top1, and no forgetting of broken designs. The
   switch is one file; going back is deleting it.
4. A ranker's best of a bad lot is still bad. The quality bar is a separate
   logistic model of "would not send", calibrated out of fold, with a threshold set
   by a stated cost.
5. When nothing clears the bar, Stratum tries four repairs, then flags the post for
   a person. A darker-photo repair was built, shown to be invisible to the critic's
   measurements, and removed rather than shipped as theatre.

## For the ADR (Rushil writes it)

- **A separate bar rather than a ranker threshold.** Ranker scores are only
  comparable within one post. "Good enough" needs a probability that means the same
  everywhere.
- **Logistic regression for the bar, gradient-boosted trees for the ranker.** The
  bar has fewer labels and must be calibrated and explainable. The ranker has
  groups, interactions, and v1's synthetic data to keep.
- **Ship behind a file, decided by a pre-stated rule.** It is reversible, and the
  decision cannot be bent after seeing the numbers.
