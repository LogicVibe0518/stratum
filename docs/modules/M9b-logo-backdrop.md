# M9b — The logo always shows, and the logo file is never touched

**Status:** written by Claude, 29 Sep 2026. No migration. One spec schema bump (1 → 2)
with its upgrader and test.

## Why

Testing M9 with a coloured logo showed a problem the templates had all along. A
template is designed once, for every client, so it cannot know the logo's colours.
Four templates put the logo on a see-through chip in the "text" colour, which for
most brands is their dark main colour. Two put it straight on the main-colour band.
Three put it on the page colour.

So a navy logo on a navy band disappeared, and so did a white logo on a white page.
Across the library, **87 of 180 combinations** (9 templates × 5 fictional brand kits ×
4 kinds of logo) had a logo that vanished in at least one post size. After this
change: **0**.

AGENTS.md rule 2 says the logo is always the client's original file, never
recoloured. So the fix is what the logo sits on, never the logo itself.

## What changed

- **Spec schema 2:** `LogoLayer.plate` is an optional panel behind the logo, set by
  `fill` (a palette token), `opacity`, `pad` and `radius`. Version-1 posters upgrade
  unchanged (`migrate._v1_to_v2`). The service now reads stored posters through
  `stratum_spec.load`, the upgrading reader, as the spec package always said it
  should.
- **`draft/logo_fit.py`** (pure):
  - `fit_logo(spec, kit, ink, variants, aspects)` decides the panel.
  - `check_logos` reports any logo that still does not show.
  - `LogoInk` holds a logo's colours and their shares, from M9's logo reader.
- **`draft/backdrop.py`:** "what colour is behind this box", shared by the text and
  logo checks. See-through layers blend with whatever is under them.
- **Renderer and Canva export:** `render/layout.plated()` places the logo and its
  panel together inside the logo's box. The SVG draws the panel under the logo. The
  PowerPoint file gets the panel as its own rounded shape called `logo plate`, under
  the logo, so a designer can restyle or delete it in Canva.
- **Draft:** after the spec is built, the logo is fitted for every size the post is
  made in. The logo's colours are read once per file and cached by its SHA-256.
- **Preflight:** a new blocker, `logo_hard_to_see`, fires when even the best panel
  leaves less than three quarters of the logo standing out, for example a logo
  that is half black and half white.
- **Quick action:** "Show the logo" no longer brings back a chip the panel replaced.
- **M9's suggestion note** now says what happens: "Where a design puts the logo
  there, it gets a panel behind it so it still shows."

## How the choice is made

1. Measure how much of the logo stands out from what is directly behind it: each of
   its colours, weighted by the share of the logo it covers, against WCAG 2.2's 3:1
   for graphics. A photo counts as mid-grey, the worst case for both light and dark.
2. If at least 75% stands out in every size of the post, leave the design alone.
3. Otherwise try a light panel (page colour, 96%) and a dark panel (text colour, 92%),
   hiding the template's chip. Keep whichever shows more of the logo, preferring
   light on a tie, because that is how most brands present a logo.
4. The panel and the logo are fitted together inside the logo's box, so the panel
   never runs into the text beside it. The logo shrinks a little to make room; it is
   never stretched or recoloured.

## Five things a reviewer should be able to repeat back

1. **The logo file is never changed.** Only its backdrop is: a panel in a brand colour.
2. **The decision is measured.** It uses the share of the logo's area that clears 3:1
   against what is actually behind it, not "light logo or dark logo".
3. **Every size counts.** A story moves the logo, so the check runs on 4:5, 1:1, 9:16
   and 16:9, and the panel is chosen once for all of them.
4. **Nothing moves if nothing is wrong.** A logo that already shows keeps the
   template's design. The fictional clients' white logos keep their chips on the six
   photo and band designs, and get a dark panel only on the three page-coloured ones,
   where they used to be invisible.
5. **When nothing helps, it is a blocker, not a silent ship.** `logo_hard_to_see`
   names the layer, the size and what to do.

## Evidence

- `test_logo_fit.py`: 189 cases, including the whole library across every size.
  With the fitting switched off, 87 of the library cases fail.
- `test_logo_plate_render.py`: the panel sits inside the box and hugs the logo in
  the rendered pixels. The Canva file has `band`, `logo plate`, `logo` in that order,
  with the logo inside its panel.
- `test_logo_service.py`: real drafts on the SQLite stand-in; hide and show keep the
  panel's choice.
- Contact sheets: 9 templates × 4 logos at 4:5 and at 9:16, checked by eye. Every
  logo is visible, and no panel overlaps text.

## Known limits

- A logo on a photo is judged against mid-grey. On a very busy photo a panel might
  still be wise; a later critic can learn when.
- There is no upload for a second, "for dark backgrounds" logo yet. With panels it
  is rarely needed. It is the answer when `logo_hard_to_see` fires.
- **Found while checking:** the 9:16 versions of `split_panel` and `top_band` leave
  the bottom fifth of a story empty (Instagram's reply bar sits
  there), and the preview shows it black. This is a template matter, not a logo
  matter; it belongs with the layout work in M11.

## Decisions for an ADR (Rushil writes these)

- A panel behind the logo instead of recolouring it or asking for variants first.
- Visibility measured by the share of logo area at 3:1, with a 75% bar, checked
  across every post size.
- Spec schema 2 for `LogoLayer.plate`, instead of adding chip layers to templates:
  the panel has to hug the logo as drawn, which a template cannot know.
