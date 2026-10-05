# M10h — Point and say, and a chat that is measured

**Status:** written by Claude, 6 Oct 2026. It builds on M10c (smarter tweaks) and
M10d (the control panel).

**Review flags:**
- **Prompt change:** edit.parse v5 → v6. The pinned hash is updated.
- **New request field:** `Tweak.pointed` (`{layer}` or `{x, y, aspect}`). It is
  validated: x and y are 0..1 and the aspect is one of the four sizes.
- **Some tweaks need no model:** a short message about a pointed part is carried out
  in code (`quick_ops`). It is recorded in `edits` with its ops and a cost of 0.
- **An eval-only prompt override** (`prompts.overridden`, from M7b). It is used by the
  chat eval to run v5.
- No migration. No RLS, auth or token change. No new dependency.

## Why

Live, 5 Oct, the edits table:
- "add phone number +61 412 354 961" was appended to the offer line.
- "some more photos like use 3" and "use 5" ended needs_human. That was right, because
  no design holds three photos.
- Earlier live tests: "make this bigger" and similar vague tweaks failed because the
  model had to guess which part was meant.

**There was no way to say which part, and no way to measure the chat.** Every change
to the prompt was judged by trying a few messages by hand.

## What changed

### 1. Point and say (`edit/pointing.py`, `service.tweak(pointing=...)`)

The page can send the part the designer pointed at with their message:
- **a layer picked in the layer list:** `{"layer": "logo"}`;
- **a tap on the poster:** `{"x": 0.5, "y": 0.2, "aspect": "4:5"}`, as fractions of
  the size they were looking at.

**Reading a tap:**
- `layer_at` finds the topmost visible part there.
- Text is hit by its letters, not its box (with a fingertip's slack, 2% of the shorter
  side). A headline's box is wider than its words, so a tap beside a short headline on
  a photo means the photo.

**Short messages need no model** (`quick_ops`): "bigger", "make this a bit smaller",
"hide it", "up", "a little darker", "zoom in". If the message says exactly one known
thing about the pointed part, it is done in code:

| instruction | what happens |
|---|---|
| bigger / smaller | ×1.15 or ÷1.15 (on a photo, bigger zooms in; smaller goes to the model) |
| hide | set_visible false |
| up / down / left / right | a 3% move |
| darker / lighter | a photo's dim ±0.15 |
| closer | zoom ×1.15 |

There is no wait and no cost. Anything else goes to the model.

**The model is told the part** (prompt v6, `<pointed>`): "this", "it", "here" and "that
one" mean the pointed part, and other parts are left alone unless the message names
them. A message that names another part wins ("make the headline bigger" while
pointing at the logo).

### 2. Prompt v6 (`gateway/prompts/edit_parse.md`)

- **The `<pointed>` section** and its rule (above).
- **Contact details have a place:** a phone number, website, email or address goes in
  the call to action, or the offer line if the call to action cannot hold it. Never in
  the headline, and never appended to a sentence it does not belong to.

### 3. One request builder (`edit/context.edit_request`)

Tweaks and the chat eval build the model's request with the same function, so the
eval asks exactly what the product asks.

### 4. The chat eval (`edit/chateval.py`, `ml/chat/`, `make chat-eval`)

**79 cases on five fixed posters**, one per design family, made offline for the
fictional Kowalski Plumbing. The categories:
- direct changes and colours, including colours the brand lacks;
- text: prices, dates, a phone number and a website;
- photo changes, layout restyles and vague requests;
- several changes in one message, and "again" with recent history;
- pointing;
- the live 5 Oct messages;
- out-of-scope asks.

**How each case is scored:**
- **outcome:** change, ask or refuse;
- **ops:** the kind, the layer and the direction of the numbers;
- **only:** nothing else touched.

**What runs, and what is reported:**
- Pointed short messages go through `quick_ops`, as in the product. The report counts
  how many were answered without a model.
- `--baseline` runs the chat as it was: v5, and no pointing (there was no way to
  point).

**Rushil runs both** and writes the numbers below.

## Results

The real model (openai/gpt-6-luna), the same 79 cases, 6 Oct 2026 (Rushil's runs).

| | before: v5, no pointing | v6 + pointing |
|---|---|---|
| passed, as run | 0.835 | 0.937 |
| pointing (12 cases) | 0.25 | 1.0 |
| answered without a model | 0 | 6 |
| median seconds (model) | 3.57 | 3.66 |
| cost of 79 | $0.0137 | $0.0129 |

**What the five v6 failures were, and what was done:**
- **Two were product bugs, now fixed in code.** The fixes change what a designer gets,
  in both columns:
  - "make it pop" came back as `restyle share 0.5`. 0.5 was sent as a number, and the
    whole answer was thrown away as invalid ("I could not turn that into a change").
    A restyle value sent as a number is now read as its text (`edit/actions._plain`).
  - "same for the logo" after a colour change tried to recolour the logo. The editor
    said "That change could not be made". A logo recolour is now dropped with the
    reason: the logo is the client's own file, and here is what can be done instead
    (`without_logo_recolours`). The rest of the change still goes ahead.
- **Three were the eval's labels, corrected after seeing the answers:**
  - "get rid of the small text under the headline": asking which of two small lines
    is right;
  - "no, the other way" after "bigger headline": the calm setting is a smaller
    headline too;
  - "make the headline animate": asking with static alternatives is right.

  Corrected labels after seeing results flatter any model, so both numbers are kept:

| | before | after |
|---|---|---|
| as run | 0.835 | 0.937 |
| with the two fixes, original labels | 0.861 | 0.962 |
| with the two fixes and corrected labels | 0.886 | 1.0 |

These were re-scored from the answers each run gave (no new calls), so the model's
words are the same and only the rules changed.

**What 1.0 means.** At 1.0 on 79 cases, this set can no longer tell a better chat
from a worse one. The next cases should be harder: two-step references, conflicting
instructions, cases that need a question back, and messy text (issue I-35). The
pointing gain (0.25 → 1.0) is the robust result: without pointing, "bigger", "make
this orange" and "darken that one" can only be guessed.

## Tests

**`test_point_and_say.py`** (22):
- **Taps:**
  - a tap on the headline's letters is the headline;
  - a tap on the photo is the photo, and a tap off the poster is nothing;
  - a tap beside a short headline means the part under it;
  - a part picked by name must be on the poster.
- **Short messages:**
  - one thing or nothing (9 parametrised);
  - they become ops for the pointed part;
  - what does not suit the part goes to the model (a photo "up", a logo "darker").
- **The prompt:**
  - the model is told what was pointed at, and "(nothing)" when nothing was;
  - the prompt puts a phone number in the call to action.
- **Through the real tweak:**
  - a pointed short message changes the poster with no model call and a cost of 0;
  - a longer message goes to the model with the part;
  - a tap is read in the size it was made in;
  - a logo is never recoloured, and the designer is told why;
  - the rest of a change goes ahead without the logo recolour.

**`test_chateval.py`** (9):
- ops match on kind, layer and direction;
- a case passes on outcome, ops and touching nothing else;
- answers are read by the product's rules;
- every case names parts its poster has;
- a pointed short message is answered without the model;
- the baseline runs v5 with no pointing, then restores v6;
- a failed case is scored and the run goes on;
- a logo recolour is refused and an op that cannot apply is invalid;
- a setting sent as a number is read as its text.

**`apps/api/tests/test_point_and_say_api.py`** (3):
- a picked part with a short message is applied;
- a tap outside the poster is refused (422);
- no pointing is the tweak it always was.

## Not in this module (issue log)

- **Tweaks remembered as habits** (a client's designer always shrinks the logo): needs
  a place in the client's rules and a way to accept or decline. Issue I-34.
- **Depth decided again after layout edits** (I-13): still open.
- **The page:** tap to point, a "Pointing at: Logo ✕" chip, and the layer list
  pointing too. That is Cursor brief C10.

## Five things to be able to say

1. A designer can point at a part (a tap or a pick) before typing. A tap is read
   through the letters, not text boxes, in the size they were looking at.
2. A short message about the pointed part ("bigger", "hide", "a bit darker") is done
   in code with no model: no wait, no cost, nothing to misread.
3. Longer messages go to the model with the part named. Prompt v6 makes "this" mean
   that part, and gives contact details a home (the call to action).
4. A 79-case chat eval runs the product's own request builder and validator on five
   fixed posters and scores outcome, ops and side effects. `--baseline` measures the
   chat before this change.
5. One function builds the model's request for both the product and the eval, so the
   eval cannot drift from what designers get.

## For the ADR (Rushil writes it)

- **Pointing over smarter guessing.** No prompt can know which "this" was meant. A tap
  costs the designer nothing and removes the guess.
- **Code for the common short instructions, the model for the rest.** These are cheap,
  instant and deterministic, and the eval reports how often each path is taken.
