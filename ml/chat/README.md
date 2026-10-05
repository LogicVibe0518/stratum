# The chat eval

79 changes a designer might type, about five fixed posters (one of each design family,
made offline for the fictional Kowalski Plumbing), read by the real model through the
same code tweaks use (`edit_request`, the op schema, and `quick_ops` for a short
message about a pointed part), then scored in code (`stratum_worker/edit/chateval.py`).
Built for M10h (`docs/modules/M10h-smarter-chat.md`).

```bash
make chat-eval ARGS="--baseline"  # the chat before M10h: prompt v5, no pointing
make chat-eval                    # now: prompt v6, pointing
```

About a cent or two per run.

## Cases (`cases.jsonl`)

Each case has:
- `id`, `category`, `poster` (panel, overlay, pair, cover or type) and `message`;
- optionally `pointed` (the part tapped first) and `recent` (the last changes);
- `expect`, with three parts:
  - `outcome`: `change`, `ask`, `refuse`, `change_or_ask`, or a list of the outcomes
    that are right;
  - `ops`: each op must be found, matched on its kind, its layer (one or a list) and
    the direction of its numbers (`"factor": ">1"` is bigger, `"dx": "<0"` is left).
    `"any": [...]` accepts one of several ways, `text_has` checks words in new text,
    and `changes` checks restyle settings;
  - `only`: no other part may be touched.

| category | what it tests |
|---|---|
| direct | one plain change to one named part |
| colour | brand colours by name; a colour the brand lacks must be asked about, never substituted |
| text | new words, prices, dates, a phone number or website (it belongs in the call to action) |
| photo | darken, zoom, pan inside the frame, one photo of two |
| layout | restyles: words over the photo, two photos, words only, words above, cover |
| vague | "make it pop": a sensible change or a question, never a refusal |
| multi | several changes in one message, including one impossible half |
| recent | "again", "a bit more", "the other way" |
| pointing | "bigger", "this", "that one" about a tapped part |
| live | messages typed on the live site on 5 Oct |
| scope | a new post, publishing, animation, a font: say so or ask |

## Reading a report

| field | meaning |
|---|---|
| `passed` | the share of cases right on outcome, ops and touching nothing else |
| `by_category` | the same, by category |
| `outcomes` | how many answers were each outcome |
| `without_a_model` | pointed short messages carried out by `quick_ops` |

Run each twice when two runs are close: one run is noisy.
