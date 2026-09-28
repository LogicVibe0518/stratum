# C1 — UI foundation: a calm, fast, safe interface

**Branch:** `feat/ui-foundation` · **Who:** Cursor · **Reviewer:** Claude

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C1-ui-foundation.md, and implement it exactly.
Only edit apps/api/src/stratum_api/static/index.html and add
apps/api/tests/test_ui_safety.py. Do not change any Python in apps/ or packages/.
Keep it one HTML file with plain JavaScript: no npm, no build step, no frameworks.
Use the CSS tokens, the esc() helper and the session code from the brief verbatim.
Work through sections 1 to 7 in order, screen by screen, and run `make check` at the end.
Do not commit or push; I will do that after review.
Finish with a report: what you changed, what you could not do, anything you guessed.
```

## Why this module exists

The agency owner decides in the first minute whether this looks like a product or a
student project. Today the page is monospace, uses `prompt()` and `alert()`, signs
people out after an hour, and has a real security hole (below). This brief fixes all
of it without touching the API, so it can run in parallel with anything else.

## 1. Two safety fixes first (non-negotiable)

**Cross-site scripting.** The page builds HTML with template strings and assigns them
to `innerHTML`: client names, trades, post slugs, statuses, asset flags, flag messages,
edit messages. Several of these contain text a model or a user wrote. A client named
`<img src=x onerror=alert(1)>` runs script in every designer's browser. Add this helper
and wrap **every** interpolated value inside any string assigned to `innerHTML`:

```js
// Everything that came from a user, a client or a model is text, never markup.
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[ch]));
```

Numbers you computed yourself (`toFixed`, lengths, counts) may stay unwrapped. URLs in
`src` go through `esc()` too. Prefer `textContent` whenever a node holds only text.

**Sessions that expire.** Supabase access tokens last an hour; today the page then
throws people back to sign-in, mid-edit. Replace `api()` with this and route the
preview-image fetches through `authHeader()`:

```js
// Refresh a minute before expiry, and once more if the server still says 401.
let refreshing = null;
async function freshSession(force = false) {
  if (!SESSION) return null;
  const soon = (SESSION.expires_at || 0) * 1000 - Date.now() < 60_000;
  if (!force && !soon) return SESSION;
  refreshing ??= (async () => {
    try {
      SESSION = await auth("token?grant_type=refresh_token",
                           { refresh_token: SESSION.refresh_token });
    } catch { SESSION = null; }
    save();
    refreshing = null;
    return SESSION;
  })();
  return refreshing;
}

async function authHeader() {
  const session = await freshSession();
  if (!session) { show("auth"); throw new Error("please sign in again"); }
  return { Authorization: `Bearer ${session.access_token}` };
}

async function api(path, options = {}, retried = false) {
  const opts = { ...options, headers: { ...(options.headers || {}), ...(await authHeader()) } };
  if (opts.json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const r = await fetch(`/api${path}`, opts);
  if (r.status === 401 && !retried && await freshSession(true)) return api(path, options, true);
  if (r.status === 401) { SESSION = null; save(); show("auth"); throw new Error("please sign in again"); }
  if (!r.ok) throw new Error(await errorText(r));
  const type = r.headers.get("content-type") || "";
  return type.includes("json") ? r.json() : r.blob();
}

// FastAPI errors arrive as {"detail": "..."}; show the sentence, not the JSON.
async function errorText(r) {
  const text = await r.text();
  try { const body = JSON.parse(text); return body.detail || body.message || text; }
  catch { return text || r.statusText; }
}
```

## 2. Design tokens (paste verbatim, replace the old `:root` block)

Monochrome chrome; the client's poster is the only colourful thing on screen. One
accent, used only for focus. Red, amber and green only for status.

```css
:root {
  --bg: #09090b; --surface: #111113; --raised: #18181b; --line: #27272a;
  --line-strong: #3f3f46; --text: #fafafa; --muted: #a1a1aa; --subtle: #71717a;
  --focus: #60a5fa; --good: #22c55e; --warn: #f59e0b; --bad: #ef4444;
  --radius: 10px; --radius-sm: 8px; --speed: 150ms;
  --font: "Geist", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  color-scheme: dark;
}
:root[data-theme="light"] {
  --bg: #ffffff; --surface: #fafafa; --raised: #f4f4f5; --line: #e4e4e7;
  --line-strong: #d4d4d8; --text: #09090b; --muted: #52525b; --subtle: #71717a;
  --focus: #2563eb; color-scheme: light;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #ffffff; --surface: #fafafa; --raised: #f4f4f5; --line: #e4e4e7;
    --line-strong: #d4d4d8; --text: #09090b; --muted: #52525b; --subtle: #71717a;
    --focus: #2563eb; color-scheme: light;
  }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
```

Load the fonts in `<head>`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
```

Rules: body 14px/1.5 `var(--font)`, headings weight 600 with `letter-spacing:-0.01em`.
Numbers in stats, costs and timings use `var(--mono)` with
`font-variant-numeric: tabular-nums`. Borders 1px `var(--line)`. No shadows except
dialogs and toasts. No gradients, no glass effects except an 8px backdrop blur behind
an open `<dialog>`. Every interactive element gets
`:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }`.
Transitions only on colour, background, border and opacity, `var(--speed) ease-out`.
Buttons scale to `.98` while pressed.

## 3. Components to build once and reuse

- **Buttons:** primary (background `var(--text)`, text `var(--bg)`), secondary (1px
  `var(--line-strong)` border), ghost (no border, hover `var(--raised)`). Height 36px,
  radius `var(--radius-sm)`. A busy button keeps its width and shows a small spinner.
- **Inputs, selects, textareas:** background `var(--surface)`, border `var(--line)`,
  hover border `var(--line-strong)`. Textareas grow with their content up to 12 lines.
- **Card:** background `var(--surface)`, border `var(--line)`, radius `var(--radius)`,
  padding 16px. Clickable cards lift their border to `var(--line-strong)` on hover.
- **Badge:** 20px pill, 12px text. Post statuses are `draft`, `review`, `approved`,
  `archived`: draft and archived in `var(--subtle)`, review in `var(--warn)`, approved
  in `var(--good)`. A post with a blocker flag shows a `var(--bad)` badge.
- **Toast:** bottom-right stack, `aria-live="polite"`, auto-dismiss after 4s, a close
  button. Replace every `alert()` with a toast.
- **Dialog:** native `<dialog>`, 440px wide, Esc closes, focus goes to the first field.
  Replace every `prompt()` with a dialog form.
- **Skeleton:** a block with a subtle shimmer (moving background-position), used for
  every list while it loads and for posters while a draft is being made.
- **Empty state:** one sentence and one primary action, centred in the card area.

## 4. The shell

A 56px sticky top bar: the wordmark "Stratum" (Geist 600, `letter-spacing:-0.02em`),
a breadcrumb (`Clients / Sharma's Dhaba / diwali-offer`, each part a link back), then
on the right the agency name and role in `var(--muted)`, a theme menu (System, Light,
Dark; stored in `localStorage` under `stratum.theme`, applied as `data-theme` on
`<html>` before first paint), and Sign out. Content max-width 1240px, 24px gutters,
16px on phones. `Esc` goes up one level in the breadcrumb when no dialog is open.

## 5. Screens, in this order

1. **Sign in / create account / name your agency.** One centred 380px card. Under the
   wordmark: "Social posts for agencies, on brand and approved." Errors inline under
   the field they belong to, in `var(--bad)`, never as raw JSON.
2. **Clients.** Header "Clients" with the count and a primary "New client" button that
   opens a dialog (name, trade). Keep today's default brand kit object when creating;
   module M9 replaces it later. Grid of client cards: name, trade. Skeleton cards
   while loading; empty state "Add your first client to start making posts."
3. **Client.** Two columns on wide screens (main 2fr, side 1fr), stacked on phones.
   - Main: a "New post" card: brief (autosizing), design select, short name, photo
     note, a switch styled toggle "Run in the background" and the primary "Make post"
     (also `Cmd/Ctrl+Enter` from the brief). Below it, "Posts": cards with slug,
     design, and a status badge. While a post is being made, put a skeleton poster
     card first in the list showing the live job status text (`queued · 4s`,
     `drafting · 9s`, `attempt 2 of 3`).
   - Side: "Photos and logo": a drop zone (drag files in, or click) with a
     Photo / Logo segmented control, then the asset grid with each quality flag as a
     small warn badge. Upload progress per file.
4. **Post.** Breadcrumb header with "Download all" on the right.
   - Stats row: version, last change, cost so far, to check. Mono numbers.
   - Stage: the 4:5 render large, the other formats smaller beside or below it. A
     three-way toggle above the stage sets the canvas behind the poster to black,
     grey (`#808080`) or white, because a post looks different on a black app than
     in a white Instagram feed. Clicking a render opens it full size in a dialog.
   - Side panel, sticky on wide screens: "To check" (each flag: severity badge plus
     the message as text), "Instant changes" (the quick actions as a wrapped button
     grid, each shows a spinner while running), "Change something" (input plus Apply,
     `Cmd/Ctrl+Enter` applies; show the model's "why" when it declines), "History"
     (table: change, result, time in mono).
   - After any change, re-render in place: keep the scroll position, fade the new
     render in over 150ms, and show a toast "v4 · 3.8s".

## 6. Test to add: `apps/api/tests/test_ui_safety.py`

A cheap guard so the two safety fixes cannot quietly regress:

```python
from pathlib import Path

PAGE = (Path(__file__).parents[1] / "src/stratum_api/static/index.html").read_text()


def test_the_page_escapes_what_it_puts_into_html() -> None:
    assert "const esc = (value)" in PAGE


def test_the_page_never_uses_browser_prompts() -> None:
    assert "prompt(" not in PAGE and "alert(" not in PAGE


def test_the_page_refreshes_sessions() -> None:
    assert "grant_type=refresh_token" in PAGE
```

## 7. The agreed product screens (from the UI review, 28 Sep)

These come from Rushil's UI research as reviewed on 28 Sep. Build them after sections
1 to 6, with the same tokens and components. They use only API routes that exist today.

**7.1 Home is "Needs you", not a dashboard.** An agency's unit of work is the client's
month, not a prompt box. Above the client grid on the Clients screen, add a "Needs you"
card fed by `GET /api/posts` (no `client_id`: every post in the agency) and
`GET /api/clients` (to show client names). Three short lists, each capped at 6 rows with a
"Show all" link that expands it:

- **With the client:** status `review`, oldest first (the longest wait on top).
- **Drafts to finish:** status `draft`, newest first.
- **Approved:** status `approved`, newest first (ready to export).

Each row: client name, post slug, status badge, and age in mono (`3d`, `5h`). Clicking a
row opens that post. Empty lists are hidden. If all three are empty, show one line:
"Nothing needs you right now."

**7.2 See the post where the client will see it.** On the Post screen, above the stage,
add a segmented control: **Canvas** (today's stage, with the black/grey/white toggle),
**Feed**, **Story**, **Grid**, plus a Light/Dark switch that applies to Feed, Story and Grid.
All three frames are brand-neutral: no platform names, logos, icons or wordmarks anywhere.

- **Feed:** a 390px-wide phone column: a round avatar with the client's initials, the
  client name, the post's 4:5 render at full column width, a row of four plain outline
  icons drawn with inline SVG (heart, comment, share, save), then the caption from
  `post.words.caption` and hashtags, clamped to 2 lines with "more" that expands it.
- **Story:** a 9:16 frame at 390px wide showing the 9:16 render. If the post has no 9:16
  render, show the frame empty with one line: "This post has no Story format yet."
- **Grid:** the client's latest 9 posts (from `GET /api/posts?client_id=`, then
  `GET /api/posts/{id}` for each post's renders, fetched in parallel), in a 3-column grid
  of 3:4 portrait tiles (`object-fit: cover`), with this post outlined in `var(--focus)`.
  Posts with no render yet show a skeleton tile.

Frame colours come from the Light/Dark switch, never from the app theme: light =
white background, `#0a0a0a` text; dark = `#000` background, `#f5f5f5` text. Every
caption and name goes through `esc()`.

**7.3 Deliberately not in this brief**

- The month grid for planning and approval: posts need a planned date first, a database
  change Claude will make. It becomes its own brief.
- Client review screens: brief C5.
- Glowing borders, magnetic buttons, 3D tilt, parallax, Aceternity and Magic UI: landing
  page only, later. A tool used all day needs calm.
- Glass effects: the 8px blur behind an open dialog (section 2) is the only one.
- A metrics dashboard, scheduling to platforms, X or LinkedIn previews: dropped for now.

**7.4 Add to `apps/api/tests/test_ui_safety.py`:**

```python
def test_previews_stay_brand_neutral() -> None:
    lowered = PAGE.lower()
    for name in ("instagram", "facebook", "tiktok", "linkedin"):
        assert name not in lowered
```

**Acceptance for section 7 (Claude checks these too):**

- [ ] "Needs you" shows the right posts in each list, oldest review first, and hides
      empty lists.
- [ ] Feed, Story and Grid render for a post with 4:5, 1:1 and 9:16 renders, and degrade
      as described for a post without 9:16.
- [ ] The Light/Dark switch changes only the frames, not the app.
- [ ] No platform names or logos anywhere; the test above passes.

## Acceptance (Claude checks each of these in review)

- [ ] Every `${...}` inside a string assigned to `innerHTML` is `esc(...)` or a number.
- [ ] No `prompt(`, `alert(` or `confirm(` anywhere.
- [ ] A session older than an hour keeps working without a sign-in.
- [ ] Light, dark and system themes all readable; theme survives a reload.
- [ ] Keyboard only: every action reachable with Tab, focus ring always visible,
      `Esc` closes dialogs and goes up a level.
- [ ] Phone width (375px): no horizontal scroll on any screen.
- [ ] Reduced motion: no shimmer, no fades.
- [ ] No API path, request body or Python changed. `make check` passes.

## Do not

- Do not add analytics, charts, a dashboard, scheduling or anything that publishes.
- Do not add npm, a bundler, React, Tailwind or any script from a CDN.
- Do not store anything in `localStorage` except the session and the theme.
- Do not change the look of the posters themselves; only the interface around them.
