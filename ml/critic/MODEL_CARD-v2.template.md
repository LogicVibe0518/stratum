# Model card: Stratum critic v2 and the quality bar

Trained by Rushil Shandil on {DATE}, by running `ml/critic/walkthrough-v2.ipynb`.
Every number below is in `ml/critic/report-v2.json`. Copy this file to
`MODEL_CARD-v2.md` and fill the braces from the report.

Two models, both read by the server with plain numpy:

| | critic v2 | the quality bar |
|---|---|---|
| Question | which of these designs would a designer send? | would a designer send this at all? |
| Model | LightGBM LambdaRank, {ROUNDS} trees of up to 7 leaves | logistic regression, 35 weights |
| File | `critic/model/critic-v2.json`, switched on by `critic/model/ACTIVE` | `critic/model/bar.json` |
| Input | the same 35 measurements as v1 (`critic/features.py`) | the same |
| Output | a score; only the order within one post means anything | a probability of "would not send", and a threshold |
| Status | {SHIPPED or NOT SHIPPED: which check failed} | {SHIPPED or NOT SHIPPED} |

## Data

- **Answers:** {N} Taste Lab answers from {RATERS} raters ({DESIGNERS} designers,
  {STUDENTS} design students, {OTHERS} others) on {POSTS} posts of the agency's clients,
  with the agency's agreement. {TOO_QUICK} answers quicker than 1.5 s were left out.
- **What a rater does:** sees four designs for one real post, picks the one they
  would send, may tick every one they would send as it is, and may mark any as
  "never send". Labels: best 3, would send 2, unmarked 1, never 0 (older answers
  without ticks: 2, 1, 0).
- **Synthetic groups:** {SYNTH} of v1's broken-copy groups (fictional brands, words
  and photos), weighted 0.3, so v2 keeps knowing a broken design.
- **In the files:** trees and weights only. No client words, photos or measurements;
  the proof rows are fictional designs. The export itself is never committed
  (`ml/critic/labels/` is git-ignored).

## How it was judged

Out of fold, by post (5 folds): every score comes from a model that never saw that
post. The answer key is {KEY}. The rule was written before any result:

| check | result |
|---|---|
| learned from at least 150 answers | {PASS/FAIL} |
| orders pairs better than v1 in at least 95% of paired bootstrap resamples | {WINS} |
| no worse than v1 at picking the best (top1) | {TOP1_DIFF} |
| still knows a broken design (synthetic base_wins at least 0.95) | {BASE_WINS} |

## Results on the answer key (out of fold, 95% ranges)

| | top1 | pairs | never_last | top_ok |
|---|---|---|---|---|
| chance | | | | |
| the product's ranking | | | | |
| critic v1 | | | | |
| critic v2 | | | | |

**The ceiling:** raters picked the same design again on {SELF}% of repeat screens;
two raters agreed on {AGREE}% of shared screens.

## The quality bar

- {BAD} designs below the bar, {GOOD} good enough.
- Out of fold: AUC {AUC}; Brier {BRIER} against {BASE} for the base rate alone.
- Threshold {THRESHOLD}, set so a bad design let through costs 3 times a needless
  look: {HELD}% of designs held for a look, {CAUGHT}% of bad ones caught.
- The measurements that push a design under the bar most: {TOP_WEIGHTS}.

## Limits

- **Whose taste:** a few raters at one agency, mostly for trades businesses. It is
  not "good design" in general.
- **What it cannot see:** the critic assumes any photo under words is mid-grey, so
  neither model can judge a photo's own brightness or busyness beyond the edge map
  (issue I-36).
- **Small data:** a few hundred answers. The ranges above are wide on purpose.
- **What it may never do:** send, publish or delete anything. Below the bar, the
  product puts other designs first, tries four repairs, and flags the post for a
  person.
