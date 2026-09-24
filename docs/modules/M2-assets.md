# M2 — Photo intake (assets)

**Goal:** turn any photo a tradie sends (dark, sideways, huge, the wrong extension)
into a clean, content-addressed asset the renderer can bind, and flag the ones a
human should query before they reach a poster.

## In scope
- `ingest(path, store_dir, source=...)` → `AssetMeta`, in `apps/worker/.../assets/`.
- Type check from magic bytes: JPEG, PNG, WebP accepted; HEIC refused by name.
- Limits: 25 MB per file, 8000 px per side (Pillow's own bomb guard set to 8000²).
- Normalise: EXIF orientation applied, sRGB, 8-bit, alpha kept only if used,
  rebuilt from raw pixels so no metadata survives.
- Variants: `full.png`, `web.png` (≤1080 px), `thumb.png` (≤512 px), `meta.json`.
- Quality metrics and advisory flags.
- `AssetStore`: `put`, `path_for`, `meta_for`; local directory layout.
- `stratum-render --asset LAYER=PATH` binds a photo into an image/cutout/logo layer.

## Out of scope
Background removal, number-plate blurring, GPU, cloud storage and the database
(M1). HEIC decoding (needs a licence decision on the plugin).

## Decisions
1. **Sniff, don't trust.** The extension is ignored; Pillow is only allowed the one
   decoder the magic bytes name, so a disguised file cannot reach a different parser.
2. **Reject before decoding.** Byte size from `stat`, dimensions from the header.
   A few-hundred-byte PNG claiming 50 000² pixels never allocates a pixel buffer.
3. **The id is the pixels.** `sha256(b"n1" : mode : WxH : raw pixels)`, not the file
   bytes, so the same image in two containers is one asset and encoder upgrades
   never change ids. `n1` is the normalisation version: bump it deliberately
   whenever normalised pixels would change for the same input.
4. **Privacy by reconstruction.** Output images are rebuilt from a pixel array, not
   copied, so EXIF (GPS, device), ICC, XMP and text chunks cannot leak through.
5. **Broken colour profiles degrade, not fail.** Fall back to a naive conversion and
   flag `colour_profile_ignored`. Only an undecodable image raises `CorruptImage`.
6. **Flags advise, never reject.** A human decides whether a dark photo is usable.
7. **Pixels immutable, sources grow.** Re-ingesting the same pixels with a new
   `source` adds it to `sources` (under a folder lock) instead of discarding it.
8. **Atomic, idempotent writes.** Files are staged in a temp folder and renamed into
   place; `meta.json` is written last. A second ingest writes nothing; a re-sent
   file skips decoding via the upload-hash index in `_sources/`.

## Quality thresholds
All three are measured on the web variant (≤1080 px), except size, which uses the
full image. They were chosen on synthetic images, since real client photos are not
allowed in the repo; recalibrate on the benchmark photo set.

| Flag | Rule | How it was chosen |
|---|---|---|
| `too_blurry` | variance of the 4-neighbour Laplacian < **100** | The common rule of thumb for this exact kernel (OpenCV `ksize=1`) on 0–255 grey. On a 16 px block test scene: sharp ≈ 390, 1 px Gaussian blur ≈ 25, 12 px blur ≈ 2.4. |
| `too_dark` | mean Rec.601 luma < **40** | Well-exposed photos sit around 100–130, dim but usable ones 50–90. Below 40, brightening mostly amplifies noise. |
| `too_small` | shorter side < **540 px** | Half the 1080 px canvas: below it even a half-width inset is upscaled. |

Known blind spots: JPEG noise inflates sharpness, so dark noisy photos read as
sharp; flat-colour logos read as blurry.

## Invariants (tested)
- File type is decided by content: a JPEG named `.png` is accepted, text named
  `.jpg` and HEIC are refused with a clear message.
- Over-limit files and headers are rejected before decode, including a bomb PNG.
- Output is upright, sRGB RGB/RGBA, and carries no EXIF or ICC in any variant.
- The same pixels always give the same id; the id changes with the normalisation version.
- Ingesting twice rewrites nothing; a new source is added, never lost.
- The caller's input file is never modified.
- A poster with a bound asset renders differently from one with the placeholder.

## Done when
`make check` is green and `stratum-render --asset bg=photo.jpg` renders the photo
in place of the grey placeholder.
