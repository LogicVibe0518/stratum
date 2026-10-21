// The layers as they are on screen right now: the server's measurements, with the changes
// still on their way (overrides) and the one under the hand (a drag) laid over them.

import { fromBox, mapBox } from "../lib/geometry";
import type { Override } from "../lib/session";
import type { LayerInfo, Rect } from "../lib/types";

/** A part being dragged, resized or nudged: where it is this frame, in poster pixels. */
export interface Draft {
  layer: string;
  box: Rect;
}

export function shownLayers(
  layers: LayerInfo[],
  overrides: ReadonlyMap<string, Override>,
  canvas: { width: number; height: number },
  draft: Draft | null,
): LayerInfo[] {
  return layers.map((layer) => {
    const override = overrides.get(layer.id);
    let px = layer.px;
    if (draft?.layer === layer.id) px = draft.box;
    else if (override?.box) px = fromBox(override.box, canvas);
    const visible = override?.hidden === undefined ? layer.visible : !override.hidden;
    if (px === layer.px && visible === layer.visible) return layer;
    return {
      ...layer,
      px,
      visible,
      ink: layer.ink ? mapBox(layer.ink, layer.px, px) : layer.ink,
    };
  });
}

/** Words re-flow when their column changes width; until the server has drawn them, they
 * only move (stretching letters would look broken). Everything else stretches. */
export function textShift(drawn: Rect, now: Rect, align: LayerInfo["align"]): { dx: number; dy: number } {
  const dw = now.w - drawn.w;
  const along = align === "center" ? dw / 2 : align === "right" ? dw : 0;
  return { dx: now.x - drawn.x + along, dy: now.y - drawn.y + (now.h - drawn.h) / 2 };
}

export function uniform(a: Rect, b: Rect): boolean {
  if (a.w <= 0 || a.h <= 0) return true;
  return Math.abs(b.w / a.w - b.h / a.h) < 0.002;
}
