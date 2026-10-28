# The shared demo ("Try the demo")

The live site's front page has a **Try the demo** button. It opens a shared account in its
own agency, with fictional clients and real, freely licensed (CC0) photos. Nobody needs an
account or a password.

## What the demo can and cannot do

| Can | Cannot (the API answers 403) |
|---|---|
| Open any post, pick another of its three designs | Upload photos or logos |
| Change it on the canvas: move, resize, type, swap photos | Add clients or new posts |
| Ask for changes in plain English | Change a brand kit |
| Undo, redo, bring back any version, download | Answer the Taste Lab (its answers train the ranker) |

- The password never reaches a browser: `POST /api/demo` signs in on the server and returns
  only the session (`apps/api`, `demo_session`).
- Its AI spend has its own daily cap, $0.50 (`DEMO_DAILY_CAP_USD`), separate from the pilot's.
- Sign-ins are rate-limited: 10 per visitor per 10 minutes, 300 an hour overall.
- The demo agency's picks and edits must never be used as training data (they are strangers
  clicking, not designers choosing).

## Setting it up (once)

1. **Make the user.** Supabase dashboard > Authentication > Users > Add user > Create new user.
   A separate address (for example `you+stratum-demo@gmail.com`), a long generated password
   kept in a password manager, and **Auto Confirm User** ticked.
2. **Put it in your local `.env`** (gitignored), next to `SUPABASE_URL` and
   `SUPABASE_ANON_KEY`:
   ```
   DEMO_EMAIL=you+stratum-demo@gmail.com
   DEMO_PASSWORD=...
   ```
3. **Fill it** with one command. It signs in as the demo, makes its agency, adds three
   fictional clients from `clients/` with their brand kits, logos and photos, and drafts four
   posts through the live API and queue (a few cents of AI spend, once):
   ```bash
   uv run python tools/seed_demo.py --photos ~/dev/stratum-photos
   ```
   The photo folder has one subfolder per client (`sparkle-squad/`, `lone-star-roofing/`,
   `kowalski-plumbing/`), each with `<client>-01.jpg` and so on, and optionally `logo.png`.
   Running it again adds only what is missing and retries a draft that failed.
4. **Only then turn it on**: add the same `DEMO_EMAIL` and `DEMO_PASSWORD` to the API
   service's variables on the host (never the worker) and redeploy. From that moment the
   server refuses uploads and new posts for the account, so step 3 has to come first.
5. **Check**: `GET /api/config` answers `"demo": true`, and the button appears.

## Changing or turning it off

- New password: change it in Supabase, then in `.env` and on the API's host.
- Off: remove `DEMO_EMAIL` or `DEMO_PASSWORD` from the API's host. The button disappears and
  `POST /api/demo` answers 404.
- Reset the content: delete the demo agency's posts in Supabase and run step 3 again (with
  the variables removed from the host while it runs).
