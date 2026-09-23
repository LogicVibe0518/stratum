# M4 — Layer spec and templates

**Goal:** one typed, versioned data structure that describes a poster, plus the only
vocabulary allowed to change it.

## In scope
- `PosterSpec`: canvas, ordered layers, template + brand-kit versions, schema version.
- Layer kinds: image, cutout, logo, shape, text (discriminated union).
- Edit ops: set_text, set_color, move, scale, swap_asset, set_visible, needs_human.
- Schema migration: `upgrade()` + `load()`, with a registry for future versions.
- Three templates as data: offer (4:5), before/after (4:5), testimonial (1:1).

## Out of scope
Rendering (M5), fonts and palettes themselves (M3 brand kit), persistence (M1).

## Decisions
1. **Fractions, not pixels.** Geometry is 0–1 of the canvas, so one spec renders at
   1:1, 4:5 and 9:16 with no re-layout. Pixels appear only at render time.
2. **Brand tokens, not raw values.** `palette.primary`, `font.display`. A raw hex is a
   validation error, so no tweak or model can introduce an off-brand colour.
3. **Immutable specs.** Ops return a new spec; version history and undo come free.
4. **Strict models.** `extra="forbid"` and `frozen=True`: unknown fields fail loudly
   rather than being silently dropped on a round trip.
5. **Ops are the only mutation path**, shared by the editor, typed tweaks and voice.

## Invariants (tested)
- Layer ids are unique; stacking order is list order.
- Any sequence of valid ops leaves a valid spec (Hypothesis property test).
- Wrong layer type for an op raises `OpError`, never silently no-ops.
- A locked layer cannot be changed by an op.
- A spec from a newer schema is refused, not guessed at.
- Every shipped template validates against the current schema.

## Done when
`make check` is green and a template loads into a `PosterSpec`. M5 then renders it.
