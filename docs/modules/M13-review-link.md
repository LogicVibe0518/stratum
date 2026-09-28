# M13 — Client review links, and the decision log

**Status:** written by Claude, 28 Sep 2026. Flagged for line-by-line human review
(AGENTS.md: a migration with RLS policies, grants and SECURITY DEFINER functions, and
token code). Migration `20260928125400_client_review_links` applied to project
`piuwwigzpofwrzymmrsw` on 28 Sep.

## Why

The owner's bottleneck is the loop, not the layout: assemble, send to the client, wait,
decode "make it pop" from a WhatsApp voice note, redo, resend. This module closes the
middle of that loop. The designer sends one link; the client opens it on a phone with no
account, sees each post exactly as it will look, and taps Approve, or Request changes
with a sentence and a pin on the picture. The designer sees the verdicts on the post.

It also starts the training data. Every verdict is written to `decision_log` the moment
it happens, which is what the critic and the per-client ranker learn from later
(objective: approved first time). A decision that was not recorded when it happened can
never be recovered, so this ships before the models do.

## What changed

- `supabase/migrations/20260928125400_client_review_links.sql`
  - Tables `reviews`, `review_items`, `review_decisions`, `decision_log`, all with RLS.
  - Two functions the anonymous role may call: `review_open(token)` and
    `review_decide(token, item_id, verdict, comment, pin)`. Nothing else.
  - A trigger that owns `created_at`, `created_by`, the token hash and the tenancy
    columns of a review, and keeps a revoked link revoked.
  - Closes PUBLIC execute on the existing `stratum` functions before granting the
    anonymous role usage of the schema.
- `apps/worker/src/stratum_worker/app/reviews.py` — make a link (as the designer),
  revoke it, open it and decide (as the anonymous role).
- `apps/api/src/stratum_api/main.py` — seven routes (below) and private headers.
- `apps/api/src/stratum_api/static/review.html` — the client's page. Mobile first,
  system fonts, no third-party requests.
- `apps/worker/src/stratum_worker/queue/fake.py` — the fake PostgREST understands
  `is.null`.
- Tests: `test_reviews_contract.py` (21, real Postgres), `test_reviews.py` (15),
  `test_review_api.py` (10).

| Route | Who | What |
| --- | --- | --- |
| `POST /api/reviews` | designer | `{client_id, post_ids[], days?, title?}` → `{review_id, path, expires_at, posts}` |
| `GET /api/reviews?client_id=` | designer | the client's links: title, expiry, `state` open / expired / revoked |
| `POST /api/reviews/{id}/revoke` | designer | kills the link for good |
| `GET /api/posts/{post_id}/feedback` | designer | every verdict on the post, newest first |
| `GET /r/{token}` | client | the page |
| `GET /api/review/{token}` | client | the review, or 404 with one plain sentence |
| `POST /api/review/{token}/decide` | client | `{item_id, verdict, comment, pin?: {x, y, aspect?}}` |

## Five things a reviewer should be able to repeat back

1. **The link is a password, so it is stored like one.** 32 random bytes in the URL;
   the database keeps only the SHA-256. A leaked backup or log line cannot be replayed.
2. **The anonymous role reaches two functions and no table.** Both check the token,
   the expiry, revocation and that the item belongs to that review. Wrong, expired and
   revoked links get the same answer, so guessing learns nothing. A test asserts the
   anonymous role can execute exactly those two functions, so a future function that
   forgets `revoke ... from public` fails CI. Because they are reachable straight
   through the REST API, they store only what they checked: a pin is rebuilt from its
   `x`, `y` and `aspect` (a format the post was shown in), and anything else is dropped.
3. **Only the client can write a client's verdict.** Designers can read
   `review_decisions`, never insert into it; they can append to `decision_log` only as
   themselves and never as `client_verdict`. Approval history is evidence, not a claim.
4. **The client approves the pixels they saw.** Previews are rendered and frozen when
   the link is made, pinned to a version id; a later edit makes a new version, not a
   changed picture.
5. **Links are short-lived and cannot be stretched.** 1–30 days, and a check constraint
   of 31 days from a `created_at` the database sets itself; the token, owner, agency and
   client of a review never change after it is issued, and a revoked link stays
   revoked.

## Risks and known limits

- **Preview URLs outlive a revoke.** A revoked link stops opening, but an image URL
  someone already copied works until the link's original expiry (+1h). Acceptable for
  unposted marketing images; a storage cleanup job is the follow-up.
- **No rate limit on decide.** Someone holding a valid link can post many decisions.
  Each writes one row; bounded by who has the link. Add a per-token limit at the proxy
  when this goes public.
- **Frozen previews are stored,** unlike everywhere else (renders are on demand). They
  live under `{agency}/reviews/{review_id}/` and are not yet cleaned up after expiry.
- `GET /api/posts/{id}/feedback` does one query per review item. Fine at pilot scale.

## Verify it

In the web app, after C5 lands: make a post, "Send for review", open the link on your
phone, request a change with a pin, then approve. The post page shows both verdicts,
and:

    select kind, detail->>'verdict', actor from stratum.decision_log order by id desc limit 2;

returns two `client_verdict` rows with a null actor.

Before C5, with the API running and a signed-in token in `$TOKEN`:

    curl -s localhost:8000/api/reviews -H "Authorization: Bearer $TOKEN" \
      -H 'content-type: application/json' \
      -d '{"client_id":"<client uuid>","post_ids":["<post uuid>"]}'

and open `http://localhost:8000` + the returned `path`.
