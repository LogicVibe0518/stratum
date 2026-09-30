# Model card: Stratum critic v1

Trained by Rushil Shandil on {DATE}, by running `ml/critic/walkthrough.ipynb`. The
numbers below were written by step 9 of that notebook; the full set is in
`ml/critic/report-v1.json`.

A learning-to-rank model that scores social-media poster designs, so the product can
show a designer the best few out of hundreds. Version 1 is a **defect detector**: it
learned to tell a design from broken copies of it. It has not yet learned taste,
meaning which of two good designs a person prefers.

| | |
|---|---|
| Model | LightGBM LambdaRank, {TREES} trees of up to 15 leaves |
| File | `apps/worker/src/stratum_worker/critic/model/critic-v1.json` |
| Input | 35 design measurements of one poster in one size (`critic/features.py`) |
| Output | a score; only the order within one post's candidates means anything |
| Runs on | plain numpy (`critic/ranker.py`); no ML library on the server |

## Data

All of it is made by Stratum's own renderer from fictional material. There is no
client's work, no scraped designs and no third-party dataset.

- **Designs:** candidates from the design space (M11a) plus the nine hand-made
  templates.
- **Brands:** five fictional brand kits.
- **Copy:** 16 short sets of fictional words.
- **Photos:** procedural scenes.
- **Damage:** ten kinds at three strengths:
  - a line out of alignment;
  - words in the margin;
  - the offer run into the headline;
  - small print shrunk;
  - an oversized headline;
  - the scrim dropped;
  - a low-contrast colour;
  - the text block slid away;
  - the logo on the headline;
  - uneven gaps.

**Groups:**
- "broken": the untouched design (label 1) against one copy per kind of damage
  (label 0).
- "severity": one kind of damage at three strengths (labels 3 to 0).

**Splits** are by brand kit and by copy, never by row:
- train: three brands, copy 0 to 9;
- valid: Lone Star Roofing, copy 10 to 12;
- test: Sparkle Squad, copy 13 to 15.

The number of trees was chosen on validation.

## Results on the test split

The test brand and words were never seen in training or used for any choice. Ranges
are 95% bootstrap intervals over groups.

| question | critic v1 | hand-written rule |
|---|---|---|
| untouched design beats each damaged copy | {M_WINS} | {R_WINS} |
| untouched design ranked first of its group | {M_TOP1} | {R_TOP1} |
| more of the same damage scored lower | {M_ORDER} | {R_ORDER} |

The hand-written rule (`ranker.heuristic`) is the baseline. Its penalties are
judgement, not fitted.

Most useful measurements (share of gain): {TOP_FEATURES}.

## Limits

- **Not taste yet.** Every example says "this is the undamaged design". None says
  "people preferred this good design to that one". Two correct designs may get close,
  meaningless scores.
- **Synthetic photos.** Busyness is learned from procedural scenes, not real photos.
- **One size at a time.** It scores one size of a poster and does not know whether a
  story matters less than a feed post.
- **Three fonts.** It has only seen the faces the fictional kits use.
- **Scores are not probabilities.** They are not comparable across posts or model
  versions.

## Next

1. Teacher pairs: an AI vision model compares two good designs, asked twice with the
   positions swapped.
2. Human pairs from a labelling page. The held-out part is the real test.
3. The top three in the product, with a logged exploration slot.
4. Retraining from designers' picks. A new model ships only if it beats the current
   one on held-out human pairs.

## Licences

- LightGBM (MIT): training only, as a development dependency.
- numpy (BSD): runs the shipped model.
- The data is generated here from fictional material.
