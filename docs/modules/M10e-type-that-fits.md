# M10e — Type: lines that break by meaning, and copy that fits each design

**Status:** written by Claude, 2 Oct 2026.

**Review flags:**
- **Spec schema 5** (`TextLayer.wrap`), with an upgrader and tests. Every older poster
  renders exactly as it was approved.
- Prompt `post.copy` goes from v1 to v2.
- No migration. No RLS, auth or token change. No new dependency.

## Why

The control panel's read-out of a live poster showed the headline set as
**"A LITTLE PEACE FOR YOUR / MIND"**. Wrapping was greedy: each line takes as many words
as fit, and the last line gets what is left. Two consequences:
- **Widows:** one word stranded on the last line.
- **Phrases split:** a line ends on "for" or "your", "20%" lands on one line and "off" on
  the next, a phone number is cut in half.

Separately, the copy model wrote **one** headline for one character budget. In a design
with less room, that headline shrinks towards its minimum size, or at the minimum it is
cut short with an ellipsis. In a design with more room, the room goes unused.

## What changed

### 1. Lines break where the meaning breaks (`render/text.py`)

`wrap_balanced` keeps **the same number of lines as greedy wrapping**. So:
- every text height stays the same;
- every layout the design space measured stays the same;
- every "fits N characters" capacity stays the same;
- every layout check stays the same.

Within those lines it finds the cheapest arrangement, by dynamic programming over the
words. The cost is the evenness of the lines (sum of squared slack) plus a cost for each
break:

| break | cost |
|---|---|
| after a comma, colon, full stop or dash | −0.6 (a good place) |
| before "for", "with", "from", "when"… (a phrase starts) | −0.3 |
| after "the", "a", "for", "your", "with"… (the word leans forward) | +1.0 |
| between a number and its unit ("20% off", "5 Nov", "3 days") or the next number (a phone number) | +1.0 |
| "under 18", "from 5", "by 10" | +0.5 |
| a last line of one word | +0.5 |

Examples (tested):
- "A LITTLE PEACE / FOR YOUR MIND"
- "Women and under-18s get 20% off / for 2 months from 5 November 2026."
- "Call / 0161 496 0000"

A property test with random words, sizes and widths checks three things:
- the same line count as greedy;
- no word lost or reordered;
- every line fits.

### 2. Spec schema 5: `TextLayer.wrap` (`"greedy"` or `"balanced"`)

- **New text is `"balanced"`:** the default, used by every design-space layer.
- **The 4→5 upgrader sets `"greedy"`** on every text layer of an older poster. So an
  approved poster re-exported later is the same picture, line for line. A tweak on an
  old poster keeps its wrapping too.
- **The renderer (`place_text`) and the layout check (`check_layout`) both follow the
  layer's `wrap`.** The panel's read-out and the PowerPoint export read the same lines.

### 3. Copy that fits (`design/copyfit.py`, prompt `post.copy` v2)

**The copy model writes the headline three ways:**
- the main one, within its limit;
- `headline_short`: within 60% of the limit, the words a passer-by must not miss;
- `headline_long`: up to 1.5× the limit, adding one true fact from the brief, or empty.

All three follow the same rules: no invented facts, banned phrases, required phrases.
The claims check now reads all of them (`PostCopy.everything`). A "short" version that
is not shorter, or a "long" one that is not longer, is dropped.

**Each design picks which version to set.** Every version is laid out by the design space,
then the main headline is kept **unless another is clearly better in that design**:

| situation | version set |
|---|---|
| the main one does not fit | the biggest version that does |
| the main one is cramped (3+ lines) and the short one sets at least 25% bigger | the short one |
| the fuller one sets at least 90% as big, on at most 3 lines | the fuller one: it says more at the same impact |

This runs in two places:
- `choose.fill`: the top three, the Taste Lab, and restyles;
- drafts of a chosen design (`service.draft`).

Copy with only one headline (older model answers, the fake copy) is laid out once,
exactly as before.

**Cost:** about 8 ms per design instead of 3, only when there are versions. That adds
about 1–2 s to a ranking of 240 designs. Rankings run in a job.

## Tests

- `test_line_breaks.py` (14):
  - the live examples;
  - a line that fits is left alone;
  - eight break costs;
  - the property test (150 random cases);
  - fitting sets the same size either way.
- `test_copyfit.py` (9):
  - versions main first, each once;
  - the main one kept unless clearly worse;
  - a cramped main one gives way to a much bigger short one;
  - a fuller one at the same impact wins;
  - a main one that does not fit;
  - real designs follow the room;
  - one version is laid out exactly as before;
  - nothing fits.
- `test_copy_versions_draft.py` (7), on real drafts:
  - the post keeps every version;
  - each design sets the version `best_copy` chooses for it;
  - a poster can carry a version other than the main one;
  - versions that add nothing are dropped;
  - an older model answer still drafts.
- Spec `test_migrate.py`:
  - a version-4 poster keeps greedy wrapping;
  - new text is balanced;
  - the older upgrade tests now expect greedy.
- `test_critic.py`: the orphan feature is tested on a greedy poster, and on the same
  poster balanced, where the orphan goes.

## Risks

- **Critic v1's training features came from greedy posters.** New posters have fewer
  orphans and more even lines, so features like `orphan` shift slightly in the good
  direction. Critic v2 trains on lab screens, which are all balanced.
- **The English word lists** (leans forward, opens, units) are English only. Another
  language gets balance without phrase hints. That is still better than greedy.
- **Version choice thresholds** (25%, 90%, 3 lines) are judgement calls. The Taste Lab
  will show whether people prefer the short or fuller lines. They are constants in one
  place.

## Five things to be able to say

1. Lines now break where the meaning breaks, with the same number of lines as before, so
   no layout, height or capacity changes, only which words share a line.
2. Breaks are chosen by dynamic programming: evenness plus a cost for splitting phrases,
   offers, dates and phone numbers, and a cost for a stranded last word.
3. Old posters are upgraded with greedy wrapping (spec schema 5), so an approved poster
   never re-renders differently.
4. The copy model writes the headline short, main and full. Each design sets the main
   one unless another is clearly better there: it does not fit, it is cramped, or the
   fuller one keeps its impact.
5. Every version passes the same fact checks, and versions that add nothing are
   dropped.

## For the ADR (Rushil writes it)

- **Balanced wrapping with greedy's line count**, rather than optimal paragraph layout
  with a free line count. It guarantees no layout change.
- **The wrap mode is stored per text layer** (schema 5), rather than tied to a renderer
  version. Old posters stay byte-identical.
- **A rule-based version choice**, rather than a weighted score. It is explainable, and
  the lab can tune it.
