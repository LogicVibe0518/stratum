# M15b — Taste Lab screens people can actually choose between

**Status:** written by Claude, 5 Oct 2026. It builds on M15a (the Taste Lab) and the
design space (M11a).

**Review flags:**
- **Behaviour change:** screens made the old way are no longer served to raters. Their
  answers stay in the export, marked `chooser: 1`.
- **Behaviour change:** "Make more screens" skips posts that already have current
  screens, so pressing it again continues with new posts instead of doubling old ones.
- **The job stops itself:** it stops starting new posts after 100 s, inside the 150 s a
  job may run on the default lease. It reports how many posts it `left` for the next
  press.
- No migration. No RLS, auth or token change. No new dependency.

## Why

The first live batch (5 Oct, Sparkle Squad, 6 screens) showed what the lab was really
asking people:
- 21 of its 24 designs were the `panel` family;
- every one used the same photo;
- pairs differed by an alignment or a thin rule.

A person choosing between near-twins picks at random. A random pick is a label that
teaches critic v2 nothing, and costs a rater's time. Rushil saw it at once: "it is
getting confusing".

Two causes:
1. **"The top" was the ranking's first 12.** Critic v1 ranks one family high on a
   given post, so the top was one family.
2. **Nothing checked that designs look different.** Two designs whose settings differ
   can still look the same.

## What changed

### 1. How different two designs look (`design/lab.py`)

`apart(a, b)` is the mean colour difference (CIELAB ΔE 1976) between small renders of
the two designs, 96 px wide. It measures what a person sees at a glance, from what is
actually drawn, not from the settings that drew it:
- 0 means identical;
- black against white is 100.

**Calibrated** on the 5 Oct Sparkle post (561 pairs of real candidates on real stock
photos, looked at side by side):
- below about 6, pairs differed only by alignment or which headline version was used;
- from about 9, the headline size or the panel changed.

So `MIN_APART` is 8.

**Rendering small** (`render_poster(width=...)`): the poster's own layout, with only
the picture scaled. It takes about 11 ms per design, and a test checks it matches the
full render shrunk (ΔE < 3).

### 2. What goes on a screen (`lab_screen`)

The four slots keep their meaning:
- two from the top;
- one from the middle;
- one from anywhere.

**What is new:**
- **The top** is the ranking's first 12 *plus each family's best two*. So a family the
  critic happens to rank low still meets people, which is the point of asking them.
- **The middle** runs from rank 12 to two thirds of the way down (it was to halfway). A
  post's six screens no longer all show the same middle design.
- **No two designs on a screen look alike** when anything else is left. If everything
  left is a near-twin, the least alike is used.
- **Across a post's screens** (`Shown`):
  - the top picks rotate through the families;
  - designs already shown are used again only when nothing new is left.

### 3. More than one photo (`app/lab.py`)

When the client has more than two photos:
- the best two photos lay out one ranking;
- the next two lay out a second;
- a post's screens alternate between them.

Each screen records:
- `photos`;
- `closest` (its hardest comparison, in ΔE);
- `chooser: 2`.

The job's result reports `near_twins` and the `families` used.

## Before and after

The same three posts Rushil made on 5 Oct, with his starter photos. Six screens per
post, old chooser against new, same seeds:

| post | near-twin screens (closest ΔE < 8) | families across 24 designs | photo sets |
|---|---|---|---|
| Sparkle "first time booking" | 0 → 0 | panel 12, pair 9, type 2, cover 1 → all five, 4–6 each | 1 → 2 |
| Lone Star "done well" | 3 → 0 | panel 23, pair 1 → all five, 3–9 each | 1 → 2 |
| Lone Star "8 years" | 4 → 0 | panel 21, pair 2, type 1 → all five, 3–9 each | 1 → 2 |

**The closest comparison on any new screen is ΔE 16.6.** On the old roofing screens it
went as low as 1.5.

## Tests

**`test_lab_sampling.py`** (the old 7, plus 10 new):
- **Measure:**
  - `apart` is 0 for the same look and 100 for black against white;
  - looks of different sizes are as different as can be;
  - white is L\* 100 with no colour.
- **Near-twins:**
  - look-alikes never share a screen while anything else is left (40 seeds);
  - when everything looks alike, a screen is still made from the least alike.
- **Families:**
  - a family the critic ranks low still reaches the top (the 5 Oct case: a top 12 of
    panels only);
  - each family's best two join the top;
  - a post's screens rotate through all the families, and panels take at most 4 of the
    12 top picks (before: all 12).
- **Plumbing:**
  - a look per design or an error;
  - photo sets are the best two, then the next two.

**`test_render_small.py`** (2):
- a small render is the same poster shrunk;
- without a width, the render is full size.

**`apps/api/tests/test_taste_lab.py`** (the old 14, plus 5 new):
- alternate screens use the client's next-best photos;
- pressing again skips posts that already have screens;
- screens say how they were chosen and how alike they are, in storage and in the
  export;
- old screens are not served, but their answers are kept;
- a job out of time leaves the rest for the next press.

**Mutation-checked** (each of these broke a test):
- dropping the look-alike check;
- leaving each family's best out of the top;
- ignoring which families a post's screens already showed;
- not keeping what was shown.

## Risks

- **ΔE on small renders is a proxy for "a person sees a difference".** Two designs
  with the same colour masses but a different type treatment can score low. The
  floor is set on one post. Check it against the first real ratings: if people
  agree with each other less on screens with a low `closest`, raise it.
- **Pushing families in means more weak designs are shown.** That is intended: whether
  the ranking is right about families is exactly what the lab must find out. But it
  means "people prefer the top" will look weaker than with the old screens.
- **Two rankings per post double the time to make screens** (about 3.5 s → 7 s per
  post, plus about 1 s of small renders). The time budget keeps the job inside its
  lease; a big agency needs a few presses.
- **Old screens stop being served.** There were 6 and no answers yet; nothing is lost.

## Five things to be able to say

1. A comparison only teaches the critic something if a person can see the difference,
   so the lab now measures difference on what is drawn: mean ΔE between 96 px
   renders, with a floor of 8 calibrated on real candidates.
2. "The top" includes every family's best two. Otherwise the critic's favourite family
   on that post fills every screen, and the lab never tests the critic where it is
   most likely wrong.
3. A post's screens rotate through families and avoid repeating designs, and they
   alternate between the client's best and next-best photos.
4. On Rushil's three real posts: near-twin screens went from 7 of 18 to 0, and every
   family now appears.
5. Every screen records how it was chosen (`chooser`) and its hardest comparison
   (`closest`), so the critic v2 walkthrough can filter, or check that agreement rises
   with `closest`.

## For the ADR (Rushil writes it)

- **Difference is measured on pixels, not on design settings.** Settings that differ
  can render alike (a rule under a short headline), and settings that match can
  render very differently on another photo.
- **Information over coverage of the ranking's top.** Showing a person two designs they
  cannot tell apart wastes their time and adds noise to the labels. Uncertainty-based
  pair choice (pairs where critic v2 is unsure) comes once v2 exists.
