# M14 — Export to Canva: every layer stays editable

**Status:** written by Claude, 29 Sep 2026. No migration, no auth or token code.

## Why

The agency's team already finishes work in Canva, and the owner's bottleneck is the
whole loop, not the first draft. A flat PNG makes them start again the moment a
client says "make the phone number bigger". Stratum's posters are data, so the
export can hand over what an image model cannot: each headline, shape, photo and the
logo as its own object. Canva imports PowerPoint files with objects intact, so the
export is a `.pptx`, one slide per poster size.

## What changed

- `apps/worker/src/stratum_worker/export/pptx.py` — `export_pptx(spec, kit, ...)`:
  one PowerPoint object per visible layer, in stacking order, named after the layer.

  | Layer | Becomes |
  | --- | --- |
  | image | a picture cropped exactly as the poster crops it (`cover`) or fitted whole (`contain`); a blur is baked into the pixels; a dim is a separate black rectangle that can be deleted |
  | cutout | a picture, never cropped, transparency kept |
  | logo | the client's original file, never cropped or recoloured |
  | shape | a native rectangle, rounded rectangle or oval in the brand colour, with its opacity |
  | text | a text box at the renderer's size and line breaks, in the brand font and colour |

- `apps/worker/src/stratum_worker/render/layout.py` — the geometry and text fitting,
  moved out of the SVG writer so both outputs use the same code. The SVG output is
  byte-identical before and after: 228 template × brand kit × size combinations
  compared.
- `service.bundle` ("Download all") now adds a `.pptx` per size and an
  `OPEN-IN-CANVA.txt` note beside the PNGs and the caption. `service.export_deck`
  makes one size on its own.
- `GET /api/posts/{id}/canva.pptx?aspect=4:5` — one size, as a download.
- Dependency: `python-pptx` 1.0.2 (MIT), which brings `lxml` (BSD-3) and
  `XlsxWriter` (BSD-2). All are on the allowed-licence list.

## Five things a reviewer should be able to repeat back

1. **One layout, two outputs.** The renderer and the export read size, line breaks
   and position from `place_text`, so the file a designer opens cannot drift from the
   poster the client approved.
2. **Pixels map at 96 dpi.** 1 px = 9,525 EMU = 0.75 pt, which is how Canva converts
   a PowerPoint page back to pixels. A 1080 × 1350 post imports at 1080 × 1350.
3. **What the client saw is what arrives.** Photos are cropped with the same maths as
   the renderer, and logos are never cropped. LibreOffice renders of four templates
   matched our own renders to within a few levels per channel.
4. **Nothing is added.** New PowerPoint shapes carry theme styling (outline, shadow);
   it is stripped, so no importer draws a shadow that isn't in the poster.
5. **Files stay small.** Opaque photos become JPEGs and are stored at no more than
   twice their shown size. Transparent logos and cutouts stay PNG.

## Known limits

- Letter spacing is not exported. The renderer doesn't use it either.
- A blur is baked into the photo, so the designer can't un-blur it. The original
  photo is still in the client's assets.
- Fonts are referenced by name (Anton, Barlow), not embedded. Both are in Canva's
  library. A brand font that isn't would be substituted by Canva.
- This file is a hand-off, not a round trip. Changes made in Canva don't come back
  into Stratum.

## Verify it

"Download all" on any post, then in Canva: Create a design → Import file → the
`-4x5.pptx`. Click the headline: it is text. Drag the logo: it is the client's file.
