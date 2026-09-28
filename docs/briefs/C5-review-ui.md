# C5 — Send for review, and see what the client said

**Branch:** `feat/review-ui` · **Who:** Cursor · **Reviewer:** Claude ·
**Needs:** C1 merged (buttons, dialog, toast, badge, skeleton exist) and M13 merged.

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C5-review-ui.md, and implement it exactly.
Only edit apps/api/src/stratum_api/static/index.html,
apps/api/src/stratum_api/static/review.html and apps/api/tests/test_ui_safety.py.
Do not change any Python in apps/ or packages/, and do not change any API route.
Reuse the components C1 built (buttons, dialog, toast, badge, skeleton, empty state)
and the esc() helper. Plain JavaScript, no npm, no frameworks, no CDN scripts.
Work in the order of the sections, and run `make check` at the end.
Finish with a report: what you changed, what you could not do, anything you guessed.
```

## Why this module exists

The owner's real cost is the loop: send posts to the client, wait, decode a voice note,
redo, resend. M13 built the secure part (the link, the database functions, the log).
This brief builds the two screens people touch: the designer sending a batch of posts
in three clicks, and the designer seeing exactly what the client said, and where on
the picture.

## The API you are calling (already built, do not change)

All designer calls go through `api()` from C1, which adds the session token.

| Call | Body / query | Returns |
| --- | --- | --- |
| `POST /api/reviews` | `{client_id, post_ids: [..1–31], days: 1–30, title?: ≤200}` | `{review_id, path: "/r/<token>", expires_at, posts}` |
| `GET /api/reviews?client_id=<id>` | – | `[{id, title, expires_at, revoked_at, created_at, state: "open"\|"expired"\|"revoked"}]` newest first |
| `POST /api/reviews/<id>/revoke` | – | `{revoked: true}`, or 404 if already revoked |
| `GET /api/posts/<id>/feedback` | – | `[{verdict: "approved"\|"changes", comment, pin: {x, y, aspect?}\|null, created_at, version_id}]` newest first |
| `GET /api/posts/<id>` | – | already used by the post page; `versions` maps `version_id` to a `version` number |

Errors come back as `{"detail": "<one sentence>"}`. Show the sentence in a toast or
inline; never show raw JSON.

## 1. "Send for review" on the Client screen

- In the "Posts" card header, add a secondary button **Send for review**. Disabled,
  with a tooltip "Make a post first", when the client has no posts.
- It opens a dialog (C1's dialog, 480px):
  - Title field, optional, placeholder `October posts`, max 200.
  - A checklist of this client's posts: checkbox, slug, design, status badge. Posts
    with status `draft` or `review` are ticked by default; `approved` and `archived`
    are unticked. A "Select all / none" link above the list. Scrolls after 8 rows.
  - "Link works for" select: 3 days, 7 days, **14 days (default)**, 30 days.
  - Primary button **Create link** (busy spinner while it runs; the server renders
    each post, so it can take a few seconds per post). Disabled when nothing is ticked.
- On success the same dialog changes to the **link step**:
  - The full URL (`location.origin + path`) in a read-only mono input, selected.
  - **Copy link** (primary; `navigator.clipboard.writeText`, then the button reads
    "Copied" for 2s) and, when `navigator.share` exists, **Share…** (secondary) calling
    `navigator.share({ title, url })`.
  - One line in `var(--muted)`: `Anyone with this link can approve these posts until
    <date>. Send it only to the client.`
  - One line in `var(--warn)`: `Copy it now: Stratum does not keep the link, only a
    fingerprint of it. If it is lost, revoke it and make a new one.` (This is true:
    the token exists only in that URL.)
  - **Done** closes the dialog and refreshes the posts list (their status is now
    `review`).
- On error (e.g. "diwali-offer has no draft yet"), keep the dialog on the first step and
  show the sentence inline under the checklist in `var(--bad)`.

## 2. "Review links" on the Client screen (side column, under Photos and logo)

- A card listing `GET /api/reviews?client_id=`: title (or "Untitled review"), a badge
  for `state` (open in `var(--good)`, expired and revoked in `var(--subtle)`), and
  "until 12 Oct" / "expired 3 Oct" / "revoked" in mono.
- Open links have a ghost **Revoke** button. It opens a small confirm dialog (C1's
  dialog, never `confirm()`): "Revoke this link? The client will see that it no longer
  works. This cannot be undone." with a danger-styled **Revoke** (text `var(--bad)`).
  On success, toast "Link revoked" and refresh the card.
- There is no "copy" here: the link cannot be shown again. Say so in the empty state:
  "Links you send appear here. Each one is shown once, when you make it."

## 3. "Client feedback" on the Post screen (side panel, first section when present)

- Fetch `GET /api/posts/<id>/feedback` alongside the post. Hide the section when the
  list is empty; show a skeleton row while loading.
- Newest first. Each entry: verdict badge (`Approved` in `var(--good)`, `Changes` in
  `var(--warn)`), the comment as text (`esc()`, keep line breaks with
  `white-space: pre-wrap`), `v3 · 2h ago` in mono (map `version_id` through
  `versions`; say `earlier version` if not found), and, when `pin` is set, a small
  **Show on poster** ghost button.
- **Show on poster** switches the stage to the pin's `aspect` if given (else 4:5) and
  places a marker over that render at `left: x*100%; top: y*100%`: a 22px circle,
  3px white border, `var(--bad)` fill, with a 2px dark outer ring so it is visible on
  light and dark posters. The render's wrapper needs `position: relative`. Clicking the
  marker or pressing Esc removes it. Only one marker at a time.
- If the newest entry is `changes`, add a secondary button under it: **Use as a
  change** which puts the comment into the "Change something" input and focuses it.
  Do not submit it automatically: a human decides.
- The status badge in the post header reflects the post's status (`approved` after a
  client approves); refresh it after loading feedback.

## 4. Polish the client page (`review.html`) without changing its safety rules

Keep, verbatim: the `<meta name="referrer" content="no-referrer">` and robots metas,
`referrerPolicy: "no-referrer"` on every fetch, the `esc()` helper, system fonts only
(no Google Fonts on this page: its URL is a credential), and how `pin` is sent
(`{ ...s.pin, aspect: s.aspect }`). Then:

- A sticky footer bar on phones: `3 of 5 approved` on the left, and when every post
  has a verdict, a **Done** state that reads "All done — your designer has your
  answers." with a check mark (text, not an image).
- While `changes` mode is on, a one-line hint over the image: "Tap where the change
  is" that hides once a pin is placed. Placing a pin gives a short haptic tap where
  supported (`navigator.vibrate?.(10)`).
- Pinch-zoom friendly: tapping an image outside `changes` mode opens it full screen in
  a `<dialog>`; Esc or tap closes.
- After Approve, scroll smoothly to the next undecided post (respect reduced motion).
- Buttons stay at least 48px tall. Test at 360px wide: no horizontal scroll.

## 5. Tests to add to `apps/api/tests/test_ui_safety.py`

```python
REVIEW = (Path(__file__).parents[1] / "src/stratum_api/static/review.html").read_text()


def test_the_review_page_keeps_its_link_to_itself() -> None:
    assert '<meta name="referrer" content="no-referrer">' in REVIEW
    assert 'referrerPolicy: "no-referrer"' in REVIEW
    assert "fonts.googleapis.com" not in REVIEW and "<script src=" not in REVIEW


def test_the_review_page_escapes_what_it_shows() -> None:
    assert "const esc = (value)" in REVIEW
    assert "prompt(" not in REVIEW and "alert(" not in REVIEW and "confirm(" not in REVIEW


def test_the_designer_page_never_confirms_with_the_browser() -> None:
    assert "confirm(" not in PAGE
```

## Acceptance (Claude checks each of these in review)

- [ ] Create a link for 3 posts, open it on a phone (or 375px devtools), request a
      change with a pin on the 1:1 format, approve the other two. The post page shows
      the change with its pin on the 1:1 render, and the approvals.
- [ ] The link is shown exactly once; nothing in `localStorage` holds it.
- [ ] Revoke works, the client page then says the link is not working, and the card
      shows "revoked".
- [ ] Every `${...}` into `innerHTML` is `esc(...)` or a number, on both pages.
- [ ] Keyboard only works for the whole send flow; Esc closes dialogs.
- [ ] No Python or route changed. `make check` passes.

## Do not

- Do not auto-apply a client's comment as an edit. A human clicks.
- Do not show, store or log the token anywhere except the link step of the dialog.
- Do not add email or WhatsApp sending; the designer shares the link themselves.
- Do not load anything from another site on `review.html`.
