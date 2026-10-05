# M7b — Words that earn their space, and a brief box that asks

**Status:** written by Claude, 5 Oct 2026. It builds on M7 (draft post) and M10e
(post.copy v2).

**Review flags:**
- **Prompt change:** post.copy v2 → v3. The pinned hash is updated.
- **New fields:**
  - `PostCopyRequest.feedback`;
  - `PostCopyResult.missing` and `PostCopy.missing`, stored in `posts.words`.
- **A draft can make a second model call:** only when the offer line repeats the
  headline. Its cost is added to the post.
- **New endpoint:** `POST /api/clients/{id}/brief/check`. It reads the client through
  the caller's own token, like every client read.
- **An eval-only prompt override** (`prompts.overridden`). It is used by the copy eval
  to run the old prompt, never by the product.
- No migration. No RLS, auth or token change. No new dependency.

## Why

Three posts made on the live site on 5 Oct:

| brief | headline | offer line |
|---|---|---|
| first time booking is free | First time booking is free | Your first time booking is free. |
| we have been doing roofing for the past 8yrs and are experienced | 8 years of roofing experience | We've been doing roofing for the past 8 years and are experienced. |
| normal advertisement about the brand and that they do their job really well | Lone Star Roofing, done well | We take pride in doing our job really well. |

**The problems:**
- Two of the three say the same thing twice.
- The third polishes the brief's own words back.

**The causes:**
- The prompt asked for an offer line, required it to be non-empty, and forbade
  inventing facts. So when a brief gives one fact, the only true thing left to write
  is that fact again.
- Nothing measured it.
- The clients were made in the app with no services or areas, so there was nothing
  else true to add (issue I-23).

## What changed

### 1. post.copy v3 (`gateway/prompts/post_copy.md`)

**Every line on the image does a different job:**
- **headline:** the one message.
- **offer_detail:** a second, different piece of information:
  - the detail (what is included, the price, the dates);
  - the proof (a number from the brief);
  - or who and where (a service and an area from the client's lists).
  It must never restate the headline. With one fact in the brief, it uses a service
  and an area. With no profile, it says plainly what the business does.
- **cta:** 2 to 5 words starting with a verb. A price or a free item only if the
  brief has one.

**The brief is notes, not finished words.** Keep its facts, do not copy its sentences,
and leave out praise it gives no evidence for.

**New output, `missing`:** up to three questions for the business about details that
would make the post stronger (an end date, a price, the area). They are shown to the
designer and never printed.

### 2. Checked in code, sent back once (`draft/copycheck.py`, `service.ask_for_copy`)

**`echo(headline, offer)`** is the share of the offer line's content words already in
the headline:
- words are lightly stemmed ("booking" = "book", "years" = "year");
- little words are dropped.

On the live words:
- 1.0 for "first time booking is free";
- 0.8 for "8 years";
- 0.12–0.25 for offer lines that add something.

Above `ECHO_MAX` (0.6), the draft is sent back once. The second call is told exactly
what was wrong, and the better of the two is kept.

**Also measured, not sent back** (copying a fact is often right):
- `copied_run`: words in a row copied from the brief;
- `cta_verb`: the call to action starts with a verb.

**`service.ask_for_copy`** is the one function that asks for words. Drafts, best three
and the copy eval all call it.

### 3. A flag that stays until it is fixed (`service.repeated_lines`)

It is checked on every version: draft, tweak and quick change. If the offer line, as
the poster now shows it, repeats the headline, the post gets a `copy_repeats` flag:

> "The offer line says the same as the headline. Give it a second detail: a price, a
> date, what is included, or the area."

It goes as soon as either line changes.

### 4. A brief box that asks (`draft/brief.py`, `POST /api/clients/{id}/brief/check`)

This runs before any model, in code, in microseconds, so the page can ask while the
person types. It returns at most three questions, most useful first:

| code | when |
|---|---|
| `too_short` | under 4 words |
| `no_detail` | no price, number, offer word or named service |
| `no_end` | an offer with no time word ("until", "this week", a month, a day) |
| `no_service` | the client has services and the brief names none |
| `no_area` | an offer, the client has areas, and the brief names none |
| `no_profile` | the client has no services and no areas: add them to the client |

**On the live briefs:**
- "first time booking is free" (no profile) asks `no_end` and `no_profile`;
- the vague roofing brief asks `no_detail` and `no_profile`.

The page part is Cursor brief C9.

### 5. The copy eval (`draft/copyeval.py`, `ml/copy/`, `make copy-eval`)

**Thirty briefs for the five fictional clients:**
- the three live briefs, with an empty profile;
- one-fact briefs with a full profile;
- briefs with two or three facts;
- briefs asking for banned claims;
- briefs that tempt the model to invent a number;
- chatty, messy text.

**What happens to each case:**
- it is written by the real model through `ask_for_copy`;
- it is scored for echo, copying, CTA verb, invented numbers, banned and required
  phrases, service and area in the caption's first sentence, length, questions back,
  and calls.

**Old against new, same code:**
- `make copy-eval ARGS="--baseline"` runs the product as it was: the v2 prompt (kept in
  `ml/copy/post_copy_v2.md`) and one call;
- `make copy-eval` runs v3.

Reports are written to `ml/copy/reports/`. **Rushil runs both** (it needs the model
keys, about a cent each) and writes the numbers into this doc.

## Results

Rushil fills this in after the two runs:

| | baseline (v2, one call) | v3 |
|---|---|---|
| echo | | |
| copied | | |
| cta_no_verb | | |
| invented_number | | |
| banned_claim | | |
| clean (no problem) | | |
| second tries | – | |
| with questions back | – | |

## Tests

**`test_copycheck.py`** (6):
- the live echoes are caught, and offer lines that add something are not;
- stemming;
- copied runs;
- CTA verbs;
- only an echo is sent back.

**`test_brief_gaps.py`** (5):
- the live briefs get the questions that would have helped;
- a full brief gets none;
- an offer asks for an end and a place;
- never more than three questions;
- questions name the client's own services.

**`test_copy_v3.py`** (11):
- **Sending back:**
  - an echo is sent back once, with the reason in the second request;
  - good words take one call;
  - a worse second try keeps the first.
- **The prompt and the output:**
  - the prompt carries the feedback and the new rules;
  - questions are lenient and cut to three.
- **The flag:** a poster that repeats is flagged, and one that does not is not.
- **The eval:**
  - every case is a real fictional client;
  - the eval scores what the product writes;
  - a failed case is recorded and the run goes on;
  - the baseline runs the old prompt with one call, then restores the new one.

**`test_brief_check.py`** (3, API):
- a thin brief gets questions from the client's profile;
- a full brief gets none;
- another agency's client is not found.

## Risks

- **A second call costs a second call** (and its seconds), only when the first offer
  line echoed.
- **`echo` is word overlap.** A paraphrase with new words passes ("Get your first visit
  on us" under "First booking free"). The eval's examples show where it stands.
- **A US client still gets en-GB spelling.** Drafts never set the language from the
  client (issue I-33). This change does not fix that.
- **Questions back depend on the model.** They are never printed and are cut to three.

## Five things to be able to say

1. Each line on a post now has its own job, and the code checks it. An offer line
   that repeats the headline (word overlap over 0.6) is sent back once, with the
   reason.
2. The root cause was a prompt rule conflict: a required offer line, no invented
   facts, and a one-fact brief. v3 resolves it with the client's own services and
   areas, and asks back for what is missing.
3. The brief box asks before any model runs: no end date, no detail, no service, no
   area, no profile, in microseconds.
4. A 30-case copy eval runs the product's own function on the real model and scores
   it in code. `--baseline` runs the old prompt through the same code for a fair
   before and after.
5. A `copy_repeats` flag sits on any version whose lines repeat, until a person fixes
   it.

## For the ADR (Rushil writes it)

- **Check in code and send back once, rather than prompt harder.** It is measurable,
  cheap when the first answer is fine, and bounded at two calls.
- **Questions to the business over invented detail.** A thin brief is the client's to
  fix; Stratum says what would help.
