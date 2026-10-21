import type { CanvasView, PaletteColour } from "./types";

/** The brand's colours, each shown once: a kit often names one colour twice
 * ("primary" and "text" both navy), and two identical swatches only confuse. */
export function distinctColours(view: Pick<CanvasView, "palette">): PaletteColour[] {
  const seen = new Map<string, PaletteColour>();
  for (const colour of view.palette) {
    const key = colour.hex.toLowerCase();
    const known = seen.get(key);
    if (known) seen.set(key, { ...known, used_by: [...known.used_by, ...colour.used_by] });
    else seen.set(key, colour);
  }
  return [...seen.values()];
}
