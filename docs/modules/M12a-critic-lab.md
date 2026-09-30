# M12a — The critic lab, and a notebook to train the critic in

**Status:** written by Claude, 30 Sep 2026. No migration, no auth or token code. One
new **development** dependency, LightGBM (MIT), used only for training. Jupyter,
pandas and matplotlib are fetched only for `make notebook` and are not project
dependencies.

## What this is, and what it is not

This module builds everything needed to train the design critic, and a guided
notebook. It does **not** include a trained model. Rushil trains it himself by
running `ml/critic/walkthrough.ipynb`, then commits the model, the report and the
model card that the notebook writes.

Until a model file exists, `critic.ranker.score()` falls back to the hand-written
rule. Nothing breaks either way.

## The lab (apps/worker/src/stratum_worker/critic/)

- `features.py`: 35 design measurements, each something a designer would say out
  loud. They are taken from the poster as drawn: fitted sizes, real line breaks and
  ink extents. There is also an edge-density map for how busy the photo is right
  under the words.
- `damage.py`: ten ways to break a good design, at three strengths.
- `data.py`: training groups made by the renderer from the fictional brand kits,
  fictional copy and procedural photos. There are two kinds:
  - "broken": the untouched design against one copy per kind of damage;
  - "severity": one kind of damage at three strengths.

  Splits are by brand kit and by copy, never by row.
- `ranker.py`:
  - the hand-written rule, which is the baseline to beat;
  - a numpy evaluator for LightGBM's tree dump, so the server needs no ML library;
  - `save_model()`, which writes only what prediction needs, stores LightGBM's own
    scores for a few rows as proof, and refuses to write if numpy disagrees;
  - `score()`.

## The notebook (ml/critic/walkthrough.ipynb)

It has eleven steps, each with what the code does, what you should see and why it
matters:

1. Look at one design, its 35 numbers, and a broken copy.
2. Build the data.
3. The hand-written baseline and three ways to mark a ranking.
4. Train LightGBM LambdaRank.
5. Choose the number of trees on validation.
6. One final test on an unseen brand, with bootstrap intervals.
7. What it learned (gain) and where it fails (per damage).
8. Save the model and prove it.
9. Write the report and the model card.
10. Practise explaining it.
11. Commit.

It runs end to end in about two minutes. On the test brand, a run in the cloud gave
the untouched design ranked first 0.932 of the time, against the rule's 0.245. The
notebook states each expected number so the person running it can check theirs.

## Five things a reviewer should be able to repeat back

1. **The model is trained by a person** in a notebook that explains every step. The
   repository ships the lab, not a black box.
2. **The data is fictional and made here.** It is split by brand and by copy, so the
   test brand and its words are never seen in training.
3. **The baseline is a reasonable hand-written rule**, and the notebook shows its
   source before anything is trained.
4. **The server runs the model with numpy.** The file carries its own proof, and
   `make check` re-checks it once a model is committed.
5. **Version 1 is a defect detector, not taste.** The model card says so.

## Tests

`test_critic.py` (15, plus one that runs once a model is committed):

- seven kinds of damage each move their measurement the right way;
- a dropped scrim puts the words on the photo;
- busyness is read from the photo under the words;
- a lonely last headline line reads as an orphan;
- the measurements come in the published order;
- the numpy evaluator matches LightGBM on zeros and missing values;
- a saved model loads and carries its proof;
- the shipped model still reproduces its proof (skipped until one is committed);
- the splits never share a brand or a piece of copy;
- with no model file, scoring falls back to the rule.

The notebook itself was run end to end with `jupyter nbconvert --execute` before
hand-over.
