# C3 — Brand colours from the logo, and a new-client form that asks for the logo

**Branch:** `feat/ui-brand` · **Who:** Cursor · **Reviewer:** Claude · **Needs:** M9 and
C1b merged (same file as C1b, so do it after).

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C3-brand-and-onboarding.md, and implement it exactly.
Only edit apps/api/src/stratum_api/static/index.html and
apps/api/tests/test_ui_safety.py. Do not change any Python in apps/ or packages/.
Keep esc() or textContent for everything shown. Run make check. Do not commit or push.
Finish with a report: what you changed, what you could not do, anything you guessed.
```

## Why

M9 reads a client's colours from their logo and fills in a whole palette, with a
reason for each colour. This brief is the screen for it, and it removes the
Kowalski navy-and-orange kit that the page currently gives every new client.

## The API (already built in M9)

- `GET /api/clients/{id}/brand` returns
  `{kit: {version, palette: {token: hex}}, starter: bool, checks: [{what, ratio, needs, ok}], history: [{version, created_at}]}`.
- `GET /api/clients/{id}/brand/suggestion` returns
  `{kit, tokens: {token: {hex, source: "logo"|"derived", why}}, checks, notes: [str], swatches: [{hex, share}], tone, logo}`.
  It saves nothing. It returns 422 with a sentence if the client has no logo.
- `PUT /api/clients/{id}/brand` takes
  `{palette: {token: hex}, version: <kit.version you showed>, suggested?: {token: hex}}`
  and returns `{kit, checks}`. A 409 means someone else saved first; show the server's
  sentence and reload the panel. A 422 means a bad palette; show the sentence.
- `POST /api/clients` no longer needs `brand`. Leave it out.

## 1. New-client dialog

- Remove the hard-coded `brand` object from the submit handler. Send
  `{slug, name, trade, rules}` only.
- Add an optional **Logo** file input under Trade (`accept="image/png,image/jpeg,image/webp"`),
  with the hint "PNG with a transparent background works best."
- On submit:
  1. Create the client.
  2. If a logo was chosen, upload it (`POST /api/clients/{id}/assets`, `kind=logo`), the
     same way the existing upload does.
  3. Open the client.
  4. If a logo was uploaded, open the Brand sheet (section 3) straight away with the
     suggestion.

## 2. Brand panel on the client screen

A compact section above the posts list, titled **Brand**.

- One row of seven swatches in this order, each with a plain label and its hex in
  small monospace underneath:

  | Token | Label |
  | --- | --- |
  | primary | Main |
  | secondary | Accent |
  | surface | Page |
  | text | Text |
  | text_on_primary | On main |
  | text_on_dark | On dark |
  | muted | Small print |

- Under the row, one muted line: "Version N · saved <relative date>" from `history[0]`.
- If `starter` is true, replace that line with: "These are placeholder colours. Read the
  real ones from the logo." (No red. This is information, not an error.)
- Two buttons: **Read colours from logo** (primary if `starter`, otherwise secondary) and
  **Edit colours** (ghost). If the client has no logo asset, disable "Read colours from
  logo" and show the hint "Upload a logo first."

## 3. Brand sheet (a dialog)

It opens from either button. "Read colours from logo" fills it from `/brand/suggestion`;
"Edit colours" fills it from the current kit and shows no reasons.

- **Top:** "Found in the logo:" followed by the swatches from `swatches`, each a small
  chip with its hex and share (e.g. `#12264a · 88%`).
- **Then one row per token**, in the order of the table above:
  - the label;
  - `<input type="color">` and a text field with the hex, kept in sync;
  - under them, the `why` sentence in muted text, prefixed with "From the logo:" when
    `source` is `logo`.
  - When the user edits a colour, replace its `why` with "Changed by you."
- **Live sample:** a small, brand-neutral block that updates as colours change.
  - a 16:9 box in the main colour with the word "Headline" in the on-main colour;
  - under it, a page-coloured box with "Body text" in the text colour and "Small print"
    in the muted colour;
  - a thin accent bar.

  This is the one place colours other than greys appear in the chrome, and it is the
  client's colours, which AGENTS.md allows.
- **Checks:** recompute contrast in the browser whenever a colour changes. Use the
  same WCAG formula as `stratum_spec/colour.py`, ported to JS in about 15 lines. Show
  each check as "Words on the main colour: 12.6:1" plus a status dot: green if ≥ needs,
  red if below. The three text checks come from `checks`; show any logo checks from
  the suggestion as they were returned, since they don't change with edits.
- **Notes:** each note as a muted line under the checks.
- **Footer:** Cancel and **Save as version N+1**.
  - Save sends `PUT` with `version` = the kit version that was shown, and `suggested` =
    the suggestion's palette (only when the sheet came from the suggestion).
  - On success: close, refresh the panel, and toast "Saved. Existing posts keep their
    colours; new posts use these."
  - If any text check is red, the Save button still works but its label becomes "Save
    anyway". Designers decide; the checks inform.

## Rules

- Validate every hex with `/^#[0-9a-f]{6}$/i` before it touches `style`. Anything else
  is ignored and shown as a grey swatch.
- Everything shown goes through `textContent`. Swatch colours are set with
  `el.style.backgroundColor = hex` after validation, never through `innerHTML`.
- Phones: the swatch row wraps to two rows. The sheet is full-screen under 600px wide.
- Keyboard: the dialog traps focus the way the existing dialogs do. Escape cancels.

## 4. Tests to add to `apps/api/tests/test_ui_safety.py`

```python
def test_the_page_reads_and_saves_brand_colours() -> None:
    assert "/brand/suggestion" in PAGE
    assert '"PUT"' in PAGE


def test_new_clients_are_not_given_a_hard_coded_brand() -> None:
    assert "#ff6a00" not in PAGE.lower()
```

## Acceptance (Claude checks)

- [ ] Creating a client with a logo opens the Brand sheet filled from the logo.
- [ ] Creating one without a logo shows the placeholder line and a disabled "Read colours
      from logo" with its hint.
- [ ] Editing a colour updates the sample and the checks immediately; "Save anyway"
      appears when a text check fails.
- [ ] Saving shows the new version; a second tab saving the old version gets the 409
      sentence and reloads.
- [ ] No hex reaches `style` without the regex; no `innerHTML` with server data.
- [ ] No Python changed. `make check` passes.
