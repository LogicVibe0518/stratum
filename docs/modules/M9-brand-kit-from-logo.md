# M9 — Brand kit from the logo, and brand kits that keep their history

**Status:** written by Claude, 29 Sep 2026.
**⚠ Needs line-by-line review:** one migration (`20260928174733_brand_kit_history.sql`)
with a new table, a row-level security policy, grants, and a `SECURITY DEFINER`
trigger function. Details under "For the reviewer" below. It is already applied to
the Supabase project (additive, and the code on `main` works with or without it). A
live check as the demo user, rolled back afterwards, confirmed the behaviour: a
colour change made version 2 and kept version 1, asking for version 99 made no new
version, the author was recorded, and deleting history was refused. The security
advisor shows only the old leaked-password warning.

## Why

The engine cannot draw a poster without a brand kit: seven exact colours and the
fonts. Until now a new client created from the web page silently got Kowalski
Plumbing's navy and orange, because the page had that kit written into it. Every new
client looked like the same plumber until someone edited JSON by hand.

The owner's bottleneck is fitting photo, logo, copy and brand together. The
brand part should take one click: upload the logo, and Stratum reads the colours out
of it, fills in the rest so every word stays readable, explains each choice in a
sentence, and waits for a person to save.

Making the kit easy to change exposed a second problem. Each poster records the
brand-kit version it was made with, but only the current kit was stored. Changing
the colours would have recoloured every old poster the next time it was drawn: a
preview falling out of the cache, a download, a Canva export. The client approved
navy; next month's download must still be navy. So kits now keep their history.

## What changed

- `apps/worker/src/stratum_worker/brand/` (new, no I/O, no model):
  - `oklab.py`: conversions between sRGB and OKLab, the colour space where distance
    matches what people see. Checked against Ottosson's published values.
  - `logo.py`: `read_logo(file)` returns the logo's colours, biggest share first.
  - `suggest.py`: `suggest(reading, kit)` returns a whole palette, a reason for every
    token, contrast checks and plain-English notes. `starter_kit()` is the plain
    charcoal-and-grey kit a client gets before their logo is read.
- Migration `20260928174733_brand_kit_history.sql`: `stratum.brand_kits`, one row per
  kit version, written only by triggers on `clients`. Existing kits are copied in as
  their current version.
- `service.kit_for(ctx, client, version)`: the kit a poster was made with. Every path
  that draws an existing version uses it: preview, download, Canva export, quick
  changes and tweaks. New drafts use the current kit.
- `service.brand_suggestion`, `service.save_brand` (with a conflict check), and a
  `decision_log` row (`suggestion_accepted`) recording which suggested colours the
  designer changed. That record is training data for a better suggester later.
- A draft now uses the client's newest logo. Before, it took whichever row the
  database returned first, so uploading a new logo might not replace the old one.
- Routes:

  | Route | Does |
  | --- | --- |
  | `GET /api/clients/{id}/brand` | the current kit, whether it is still the starter kit, its contrast checks, and version history |
  | `GET /api/clients/{id}/brand/suggestion?logo=<sha256>` | a palette read from the newest logo (or the one named). Saves nothing |
  | `PUT /api/clients/{id}/brand` | `{palette, version, suggested?}` saves the next version. 409 if someone saved since, 422 if a token is missing or not a colour |
  | `POST /api/clients` | `brand` is now optional: without it the client starts on the starter kit. Typing an existing client's name again opens that client and leaves its colours alone |

- The fake database (`queue/fake.py`) copies the triggers' behaviour, as it already
  copies `claim_job`.
- `Makefile`: `make setup` no longer installs git hooks from inside a worktree. That
  is what broke the M14 push: the shared hook pointed at a deleted worktree.

## How the logo is read

1. **Only the logo.** Transparent pixels are ignored. A plain white, grey or black
   border around the logo is recognised as background and ignored. A coloured fill
   that reaches the edges is kept, because then the logo is a badge and the fill is
   the brand colour.
2. **No edges.** Where two colours meet, anti-aliasing makes blended pixels that are
   no brand colour. Only pixels in a flat 3×3 patch are used to find colours. The
   rule loosens for JPEG noise and for hairline logos, and says so.
3. **Group like a person would.** Weighted k-means in OKLab, with no randomness.
   Seeds are chosen farthest-first and stop when every remaining colour is within
   0.05 of a seed, so the number of colours is found, not guessed.
4. **Report real colours.** If one exact value fills most of a group, that value is
   returned (#ff6a00, not the average of #ff6a00 and its noise). Shares are measured
   over the whole visible logo, so they don't change with the file's size.

Then `suggest` picks roles:

| Token | From |
| --- | --- |
| primary | the logo's biggest colour. For a black logo, black. For a light-only logo (made for dark backgrounds), a dark shade of it, with a note |
| secondary | the logo's second colour, scored by share × difference. With only one colour, a lighter or darker shade of it, with a note |
| surface | near-white with a trace of the main colour |
| text | the main colour if it is dark enough (≥ 7:1 on the page), otherwise a near-black of the same hue |
| text_on_primary, text_on_dark | white, black, the text colour or the logo's own light colour, whichever clears 4.5:1 |
| muted | a soft shade of the main colour that still clears 4.5:1 on the page |

## Five things a reviewer should be able to repeat back

1. **Old posters keep their colours.** A poster names its kit version, every version
   is kept, and anything drawing an existing poster asks for that version. If the
   version is ever missing, it refuses with a sentence rather than drawing it in
   today's colours.
2. **The database owns the version number and the history.** Triggers on `clients`
   stamp the version (1 for a new client, +1 for each change of colours) and copy
   every kit into `brand_kits`. Nobody signed in can write, edit or delete history,
   including through a direct REST call.
3. **A suggestion is never saved by itself.** The logo reader and the suggester
   propose; a person saves. Saving checks the version they were looking at, so two
   designers cannot silently overwrite each other.
4. **Readable by construction.** For any logo, the suggested palette clears 4.5:1 for
   every pairing the templates use, and 7:1 for text on the page. This is a
   Hypothesis property over randomly drawn logos (80 per run, 1,500 in one long run
   while writing it), not a handful of examples.
5. **Every colour has a reason.** Each token says where it came from ("The logo's
   second colour, 12% of it" or "A near-black with a hint of the main colour"), so a
   designer can disagree in one click.

## For the reviewer (security-sensitive lines)

`supabase/migrations/20260928174733_brand_kit_history.sql`:

- `stratum.brand_kits`: RLS on. There is one policy, `brand_kits_read`: SELECT where
  `agency_id = (select stratum.auth_agency_id())`. There is no insert, update or
  delete policy, and those grants are revoked from `authenticated`. `anon` has no
  grant.
- `stratum.stamp_brand_version()`: a BEFORE trigger, `SECURITY INVOKER`. It only
  rewrites `new.brand.version`.
- `stratum.record_brand_kit()`: an AFTER trigger, **`SECURITY DEFINER`**, with
  `search_path` pinned. It is needed because signed-in users cannot write
  `brand_kits`. It copies `agency_id` and `id` from the `clients` row, which
  `clients_rw` has already checked the caller may write, so it cannot be aimed at
  another agency. `created_by` is `auth.uid()`.
- `revoke all` on both functions from `public, anon, authenticated`. The existing
  contract test (anon can run exactly two functions) still passes.
- Contract tests (`test_brand_kits_contract.py`, 13 tests) run every change as
  `authenticated`. Each guard was checked by breaking it on purpose: removing the
  revoke, the version trigger or the agency check makes the matching tests fail.

Already true before this PR, and not changed here: a `viewer` can update a client
row (including its brand), because `clients_rw` does not check roles. Worth a
restrictive policy in a follow-up.

## Known limits

- **Fonts are not read from the logo.** That needs a trained model, and a wrong font
  is worse than a plain one. The kit keeps its fonts.
- **A logo can vanish against its backdrop.** Some templates put the logo on the
  main colour or on the page colour. A navy logo on a navy band, or a white logo on a
  white page, won't show. The suggestion now says so ("The logo on the main colour:
  1.0:1, needs 3:1"). The fix is M9b: accept a second, reversed logo and pick the one
  that shows on each backdrop, plus a preflight blocker.
- A gradient or photographic logo is summarised by its main steps, with a note.
- Changing the kit does not move existing drafts to the new colours. That would be
  its own action ("use the new brand on this post"), which makes a new version.

## Verify it

1. On a client, upload a logo, then call `GET /api/clients/{id}/brand/suggestion`.
2. Save the result with `PUT /api/clients/{id}/brand`.
3. Download an old post: it is in its old colours. Make a new post: it is in the new
   ones.

The screens for this are brief C6.

## Decisions for an ADR (Rushil writes these)

- Deterministic colour science (OKLab + k-means) instead of a vision model for
  reading logos.
- Brand-kit history written by database triggers, not by application code.
- A suggestion is saved only by a person, and what they changed is logged as training
  data.
