# The copy eval

Thirty briefs for the five fictional benchmark clients, written by the real model
through the same code drafts use (`service.ask_for_copy`), then scored in code
(`stratum_worker/draft/copyeval.py`). Built for post.copy v3 (M7b,
`docs/modules/M7b-copy-v3.md`).

```bash
make copy-eval                    # all 30 cases, about a cent
make copy-eval ARGS="--limit 5"   # the first five
```

- `cases.jsonl`: one brief per line: `id`, `client` (a folder in `clients/`), `profile`
  (`full`, or `empty` for a client made in the app with no services or areas), `brief`,
  and `about` (what the case tests). The three `live-` cases are the briefs from 5 Oct.
- `reports/`: one JSON per run, named by prompt version and time: every case with the
  words that came back, its scores, model and cost, and a summary of problem rates.

## Reading a report

`summary.rates` is the share of cases with each problem. Lower is better:

| problem | means |
|---|---|
| echo | the offer line repeats the headline |
| copied | a line copies 5+ words in a row from the brief |
| cta_no_verb | the call to action does not start with a verb |
| invented_number | a number in the words that the brief does not have |
| banned_claim | a phrase the client's rules forbid |
| missing_required_phrase | a phrase the rules require is absent |
| no_service_first / no_area_first | the caption's first sentence lacks a service / area (only when the client has them) |
| too_long | a line over its character limit (60 / 120 / 40) |

`clean` is the share of cases with no problem at all. `second_tries` counts drafts
sent back once for an echo.

## Old against new

```bash
make copy-eval ARGS="--baseline"  # the product before v3: the v2 prompt, one call
make copy-eval                    # v3: new prompt, one second try on an echo
```

`post_copy_v2.md` is the v2 prompt, kept here so the baseline runs through the same
code. One run is noisy: the same brief can come back differently, so treat a
difference of one or two cases out of thirty as noise; run each twice if it is close.
