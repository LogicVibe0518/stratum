# M10a — What real clients send: web images, AVIF, small photos, no photos

**Status:** written by Claude, 29 Sep 2026, from the first test on the live site. No
migration, no auth or token code.

## What went wrong on the live site

Rushil made a fictional meditation centre, used a photo as its logo, uploaded three
pictures downloaded from an image search, and asked for a before/after post.

1. **An `.avif` file gave "Internal Server Error".** Intake refused the format with a
   typed error, but the API did not catch it, so the person got a 500 and a
   temporary file name in the log.
2. **Both JPEGs were marked "too small" and silently skipped.** Web images are often
   around 500 px. The draft then said the design needed real photos, even though two
   had just been uploaded.
3. **With no photo, a generated background ignores what the post is about.** The
   prompt said "a {trade} work setting", so a meditation retreat would have got a
   generic room.

## What changed

- **AVIF is accepted.** Pillow 12 decodes it natively (libavif, BSD). iPhone HEIC is
  still refused, because its decoder is LGPL, but now with a sentence: "IMG_0042.HEIC
  is an iPhone HEIC photo. Export it as JPEG, then upload that."
- **Upload errors are sentences, never a 500.** Each one names the file the person
  chose: wrong type is a 415, too large a 413, damaged a 422.
- **The best photo available is used.** Good photos come first, sharpest first. If
  there aren't enough, small, soft or dark photos are used next, with a `photo_weak`
  flag that says which and asks for a better one. Only images under 320 px on the
  shorter side are never used; that's a thumbnail, not a photo.
- **A generated background is painted for the post.** The prompt now uses the
  business name, its trade and the brief. Numbers, prices, dates and currency signs
  are removed first, so the model isn't invited to paint text. One background per
  post (about $0.006) replaces reusing the client's last one.
- **Before/after is never invented, and the message says why.** For example: "This
  design shows real work, so it needs 2 real photos (before, after) and has 0 usable.
  Upload them, or pick a design that can use a generated background." A generated
  "after" picture would show work that was never done.

## Five things a reviewer should be able to repeat back

1. **A real photo beats an invented one**, even an imperfect one, and the draft says
   when it used a weak one.
2. **Only a background is ever invented.** The before and after photos and the logo
   are always the client's own.
3. **The image prompt carries no figures.** Prices and dates belong on the poster as
   text layers, never painted into the picture.
4. **Every upload refusal is a 4xx with the file's own name**, and says what to do
   next.
5. **AVIF yes, HEIC not yet**, because of the licence, and the page says exactly
   that.

## Tests

`test_real_photos.py` (7) and new cases in `test_brand_api.py`:

- a PDF, a HEIC and a damaged PNG each get their sentence and status;
- AVIF upload works;
- small photos fill a before/after post and raise the `photo_weak` flag;
- good photos win over weak ones;
- thumbnails are skipped;
- a before/after post with no photos refuses;
- a generated background comes from a figure-free prompt.

## Still to do (M10 proper)

- Crop around the subject, so a person's head is never cut off.
- Fix exposure and tilt automatically.
- Let the designer pick which photo goes in which slot.
- Upscale small photos properly instead of stretching them.
