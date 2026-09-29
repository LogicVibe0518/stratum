# M10a-3 — Tweaks that ask for more than the poster has room for, and posts that start alike

**Status:** written by Claude, 29 Sep 2026, from the third live test. No migration,
no auth or token code, no new dependency. One prompt change (edit.parse v2 → v3).

## What went wrong on the live site

With #21 deployed, the Krishna before/after post drafted on the cloud worker. Small
changes worked ("remove the before/after labels", "hide the logo"). Then the designer
typed a longer message with four asks in it: add the 50% offer for women and
under-18s, add the dates, keep the brand colours, and make a second post with this
information. Twice the page said `edit.parse: every provider failed`, after 22 s and
18 s (a one-line change took 4.8 s).

Two things were wrong at first, and the next session on a Full bleed post found three
more (listed after these).

1. **The draft had quietly dropped the offer and the dates.** The brief said "5th nov
   2026 to 1st feb 2027 ... 50% off". The copy model wrote both into the offer line
   ("5 November 2026 to 1 February 2027. 50% off for women."). But the before/after
   design is the only one of nine with no offer line, so the poster showed neither.
   The draft then said "Nothing to check". The designer's message was reasonable: the
   poster left out what the client asked for.
2. **A longer change could not be read, and the error said nothing useful.**
   edit.parse had 400 output tokens and 8 seconds. The gateway logs recorded nothing,
   so the exact failure isn't known. The timings fit an answer cut off at 400 tokens:
   two attempts on each of two providers, each attempt about 5 s, and every retry cut
   off at the same limit. They also fit two 8-second timeouts. Either way the
   designer got a 503 and a task name, and nothing was written to History.
3. **"I need both the images to be there" was called "applied" and changed
   nothing.** Full bleed holds one photo. The model was never told which layers are
   hidden, so it answered with "show the photo" and "show the logo", both already
   shown. A new, identical version was saved and History said `applied`. The next
   message, "give the other image as well", got an honest but unhelpful "I can't add
   or provide another image".
4. **The same problem in two sizes was listed twice**, word for word ("The logo is
   inside its own 2% safe margin"), with no way to tell which sizes.
5. **A second post that starts with the same words opened the first one.** The page
   names a post from the first four words of its brief, and the queue's idempotency
   key is built from that name. A new brief, or the same brief in another design,
   silently returned the earlier post until the words were reordered.

## What changed

- **New warning: `brief_not_on_poster`.** When the brief's money, percentages or
  dates ("5th nov", "Nov 5", "5 November") are not on the poster, the draft says
  which, whether the caption has them, and what to do. If the design has an offer
  line, it suggests a change like "add 5th nov to the offer line". If it has none, it
  says to make the post with a design that has one. It runs on every new version
  (draft, tweak, quick change), so it stays until the poster shows them and clears
  once it does. Only visible text counts.
- **A change no model could read gets a sentence and a History row, not a 503.** For
  a cut-off or unreadable answer, or a timeout: "That was too much to work out in one
  go. Try it as two or three shorter changes, one per message." For an outage: "The
  AI that reads changes is not answering right now. Try again in a minute; the
  instant changes above still work." The poster is untouched and the attempt is
  recorded as `failed`.
- **edit.parse has room for several changes:** 1,200 output tokens (was 400) and
  20 s (was 8). A one-line change still answers in about 3 s; the extra is only used
  when needed. The pre-call estimate for the answer rises from $0.0002 to $0.0006.
- **A cut-off answer is named.** When a provider stops at `finish_reason: "length"`
  and the JSON is incomplete, the result says "the answer was cut off at
  max_output_tokens" instead of an unparseable fragment.
- **The gateway logs every failed attempt**, one line each: task, provider, outcome
  and reason, for example `edit.parse via openrouter: invalid_output (the answer was
  cut off at max_output_tokens)`. It never logs the model's words or the request;
  those are client data.
- **edit.parse prompt v3.** New facts go into a text layer only if they fit its
  limit; otherwise the model names what did not fit. A request for another post is
  not an edit to this one. More photos than the design's image layers cannot be added:
  the model says how many this design holds and points to "Use the next photo". It
  never returns an operation that leaves a layer as it is. `needs_human` is at most
  two short sentences.
- **The model is told which layers are hidden** (", hidden" in the layer list).
- **A change that changes nothing is not saved and not called applied.** It comes
  back as `needs_human` with the model's note, or: "That would not change anything on
  this poster, so nothing was saved."
- **One flag per problem, naming the sizes.** "... it will touch the edge. (1:1 and
  9:16 only)". A problem in every size says nothing about sizes.
- **A different post gets its own short name.** If a post with that name exists and
  the brief, design or sizes differ, the new one becomes "...-2", "...-3". The same
  request sent twice still lands on the same post and job, as idempotency requires.
  The API decides this, so the page needs no change.

## Five things a reviewer should be able to repeat back

1. **What the brief asked for, the poster shows, or the draft says so.** Money,
   percentages and dates are checked on the visible text of every version.
2. **A failed tweak is a sentence and a row in History.** The poster is never
   touched and the page never shows a task name.
3. **The server log now says why a model call failed**, without logging a word the
   client wrote.
4. **A cut-off answer is a budget problem, not a model problem**, and it is named as
   one, so nobody retries or swaps models to fix it.
5. **The tweak box edits the poster in front of you.** A second post is a new post,
   and the model says so rather than guessing. A change that changes nothing is
   reported as that. Two different posts never share a name; one post sent twice
   is still one post.

## Tests

`test_tweaks_ask_for_more.py` (18):

- the live before/after poster is flagged with '5th nov', '1st feb' and '50%', and the
  flag says the caption has them;
- the same facts written differently ("5 Nov", "50 %", "£20" for "GBP 20") count as
  shown;
- "5th nov 1st feb" is two dates, not also "nov 1st";
- a real before/after draft is flagged; an offer design that prints the offer is not;
- the flag survives a quick change;
- the live four-part message with a cut-off answer gives a sentence, a `failed` edit,
  no new version, and a log line with the reason and none of the designer's words;
- a provider outage says to try again;
- the budgets are at least 1,200 tokens and 20 s;
- `finish_reason: "length"` with incomplete JSON is reported as cut off, and complete
  JSON is not;
- the live "both images" answer (show two visible layers) is `needs_human`, with no
  new version;
- a hidden logo is described as hidden, the photo is not;
- one flag per problem, with the sizes named only when not all sizes have it.

`test_queue_api.py` (+3, one made exact):

- a different brief with the same opening words gets "boiler-offer-2";
- the same brief in another design gets its own post and job;
- the second post sent twice is still one post;
- the orphan-post retry test now sends the same request as the orphan (it used "x"
  as filler) and checks that the job points at that post.

Mutation checks: without the draft check, the draft test fails. Without the tweak's
handling, both failure tests get the old `every provider failed`. Without the
finish_reason check, the cut-off test fails. Without the no-change check, the "both
images" test fails. Without `_own_slug`, the three new API tests fail.

## Still to do

- The before/after design needs an offer line, or a way to add one. This belongs
  with the layout work (M11).
- "Make another post from this brief" as a button, with the brief and any extra lines
  carried over (Cursor, C4 follow-up).
- Found while checking: quick changes re-run only the layout checks, so a new
  version made by "Hide the logo" loses the copy's `invented_number` flags. They
  should re-run `check_claims` like tweaks do.
