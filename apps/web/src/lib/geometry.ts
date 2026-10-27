// The canvas maths: what is under the pointer, where a dragged part snaps, how a handle
// resizes it, and the box the server is sent. Pure functions, all in the poster's own
// pixels for one size (the API's `px`), so they are tested without a browser.

import type { Aspect, Box, LayerInfo, Rect } from "./types";

/** Same margin the server aligns to (stratum_spec.ops ALIGN_MARGIN and the story band). */
export const ALIGN_MARGIN = 0.074;
export const STORY_SAFE_TOP = 0.14;
export const STORY_SAFE_BOTTOM = 0.8;
/** Snap when within this many SCREEN pixels: Canva and Figma use about this. */
export const SNAP_PX = 6;
/** Nothing is resized smaller than this many screen pixels. */
export const MIN_SIZE_PX = 12;

export interface Point {
  x: number;
  y: number;
}

export interface CanvasSize {
  width: number;
  height: number;
}

export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
export const CORNERS: readonly Handle[] = ["nw", "ne", "se", "sw"];
export const SIDES: readonly Handle[] = ["n", "e", "s", "w"];

/** A line a dragged part snapped to, drawn across the poster while it holds. */
export interface Guide {
  axis: "x" | "y";
  at: number;
}

/**
 * The rectangle a person sees and grabs. Words: the text column's width, and the height
 * of the lines actually drawn (the box can be much taller: the words sit in its middle).
 * Everything else: its box.
 */
export function frameOf(layer: LayerInfo): Rect {
  if (layer.kind === "text" && layer.ink && layer.ink.h > 0) {
    return { x: layer.px.x, y: layer.ink.y, w: layer.px.w, h: layer.ink.h };
  }
  return { ...layer.px };
}

/** Covers the whole poster: a backdrop, which is selected but never dragged by accident. */
export function isBackground(layer: LayerInfo): boolean {
  const { x, y, w, h } = layer.box;
  return x <= 0.001 && y <= 0.001 && x + w >= 0.999 && y + h >= 0.999;
}

export function contains(rect: Rect, point: Point, slack = 0): boolean {
  return (
    point.x >= rect.x - slack &&
    point.x <= rect.x + rect.w + slack &&
    point.y >= rect.y - slack &&
    point.y <= rect.y + rect.h + slack
  );
}

/** The topmost shown layer under a point: the one a click selects. */
export function layerAt(layers: LayerInfo[], point: Point, slack = 0): LayerInfo | null {
  const shown = layers.filter((one) => one.visible).sort((a, b) => b.order - a.order);
  // Words and logos first when the point is on them, even under a panel drawn later:
  // nobody means "the see-through band" when they click a headline.
  const small = shown.find(
    (one) => (one.kind === "text" || one.kind === "logo") && contains(frameOf(one), point, slack),
  );
  return small ?? shown.find((one) => contains(frameOf(one), point, slack)) ?? null;
}

/** The topmost shown photo under a point: where a photo dragged from the panel lands. */
export function photoAt(layers: LayerInfo[], point: Point): LayerInfo | null {
  const photos = layers
    .filter((one) => one.visible && !one.locked && one.kind === "photo")
    .sort((a, b) => b.order - a.order);
  return photos.find((one) => contains(frameOf(one), point)) ?? null;
}

/** The band a part is aligned to: the designs' margin, and a story's safe area. */
export function band(aspect: Aspect, canvas: CanvasSize): Rect {
  const { width, height } = canvas;
  const margin = ALIGN_MARGIN * Math.min(width, height);
  let top = margin;
  let bottom = height - margin;
  if (aspect === "9:16") {
    top = Math.max(top, STORY_SAFE_TOP * height);
    bottom = Math.min(bottom, STORY_SAFE_BOTTOM * height);
  }
  return { x: margin, y: top, w: width - 2 * margin, h: bottom - top };
}

export interface Targets {
  xs: number[];
  ys: number[];
}

/** Every line a part may snap to: the poster's edges and centre, its margins, and the
 * edges and centres of every other part that is shown. */
export function snapTargets(
  layers: LayerInfo[],
  moving: string,
  aspect: Aspect,
  canvas: CanvasSize,
): Targets {
  const { width, height } = canvas;
  const margin = band(aspect, canvas);
  const xs = [0, width / 2, width, margin.x, margin.x + margin.w];
  const ys = [0, height / 2, height, margin.y, margin.y + margin.h];
  for (const one of layers) {
    if (one.id === moving || !one.visible || isBackground(one)) continue;
    const f = frameOf(one);
    xs.push(f.x, f.x + f.w / 2, f.x + f.w);
    ys.push(f.y, f.y + f.h / 2, f.y + f.h);
  }
  return { xs, ys };
}

function nearest(values: number[], targets: number[], within: number): { delta: number; at: number } | null {
  let best: { delta: number; at: number } | null = null;
  for (const value of values) {
    for (const target of targets) {
      const delta = target - value;
      if (Math.abs(delta) <= within && (best === null || Math.abs(delta) < Math.abs(best.delta))) {
        best = { delta, at: target };
      }
    }
  }
  return best;
}

/** A moved frame pulled onto the nearest guide on each axis, within `within` pixels. */
export function snapMove(
  frame: Rect,
  targets: Targets,
  within: number,
): { dx: number; dy: number; guides: Guide[] } {
  const guides: Guide[] = [];
  const x = nearest([frame.x, frame.x + frame.w / 2, frame.x + frame.w], targets.xs, within);
  const y = nearest([frame.y, frame.y + frame.h / 2, frame.y + frame.h], targets.ys, within);
  if (x) guides.push({ axis: "x", at: x.at });
  if (y) guides.push({ axis: "y", at: y.at });
  return { dx: x?.delta ?? 0, dy: y?.delta ?? 0, guides };
}

/** Holding Shift while dragging: only the axis moved further so far. */
export function lockAxis(dx: number, dy: number): { dx: number; dy: number } {
  return Math.abs(dx) >= Math.abs(dy) ? { dx, dy: 0 } : { dx: 0, dy };
}

export function moveRect(rect: Rect, dx: number, dy: number): Rect {
  return { ...rect, x: rect.x + dx, y: rect.y + dy };
}

/** How far a box may move and still stay wholly on the poster (words and logos only). */
export function keepOn(box: Rect, dx: number, dy: number, canvas: CanvasSize): { dx: number; dy: number } {
  const clamp = (value: number, low: number, high: number) =>
    low > high ? value : Math.min(high, Math.max(low, value));
  return {
    dx: clamp(dx, -box.x, canvas.width - box.x - box.w),
    dy: clamp(dy, -box.y, canvas.height - box.y - box.h),
  };
}

function opposite(rect: Rect, handle: Handle): Point {
  const right = rect.x + rect.w;
  const bottom = rect.y + rect.h;
  return {
    x: handle.includes("w") ? right : rect.x,
    y: handle.includes("n") ? bottom : rect.y,
  };
}

function corner(rect: Rect, handle: Handle): Point {
  const far = opposite(rect, handle);
  return {
    x: far.x === rect.x ? rect.x + rect.w : rect.x,
    y: far.y === rect.y ? rect.y + rect.h : rect.y,
  };
}

function fromPoints(a: Point, b: Point): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(a.x - b.x),
    h: Math.abs(a.y - b.y),
  };
}

/**
 * A frame resized by one handle. Corners keep the shape and pin the opposite corner
 * (`keepRatio`); without it a corner moves freely. Sides move one edge. Nothing turns
 * inside out or gets smaller than `min`.
 */
export function resize(
  start: Rect,
  handle: Handle,
  pointer: Point,
  options: { keepRatio: boolean; min: number },
): Rect {
  const { min } = options;
  if (handle.length === 2) {
    const fixed = opposite(start, handle);
    const grabbed = corner(start, handle);
    const sx = Math.sign(grabbed.x - fixed.x) || 1;
    const sy = Math.sign(grabbed.y - fixed.y) || 1;
    if (options.keepRatio) {
      // The pointer projected onto the diagonal: the scale that follows the hand.
      const vx = grabbed.x - fixed.x;
      const vy = grabbed.y - fixed.y;
      const along = ((pointer.x - fixed.x) * vx + (pointer.y - fixed.y) * vy) / (vx * vx + vy * vy);
      const k = Math.max(along, min / Math.min(start.w, start.h));
      return scaleAbout(start, fixed, k);
    }
    const x = sx > 0 ? Math.max(pointer.x, fixed.x + min) : Math.min(pointer.x, fixed.x - min);
    const y = sy > 0 ? Math.max(pointer.y, fixed.y + min) : Math.min(pointer.y, fixed.y - min);
    return fromPoints(fixed, { x, y });
  }
  const right = start.x + start.w;
  const bottom = start.y + start.h;
  switch (handle) {
    case "e":
      return { ...start, w: Math.max(min, pointer.x - start.x) };
    case "w": {
      const x = Math.min(pointer.x, right - min);
      return { ...start, x, w: right - x };
    }
    case "s":
      return { ...start, h: Math.max(min, pointer.y - start.y) };
    default: {
      const y = Math.min(pointer.y, bottom - min);
      return { ...start, y, h: bottom - y };
    }
  }
}

export function scaleAbout(rect: Rect, fixed: Point, k: number): Rect {
  return {
    x: fixed.x + (rect.x - fixed.x) * k,
    y: fixed.y + (rect.y - fixed.y) * k,
    w: rect.w * k,
    h: rect.h * k,
  };
}

/** A resized frame pulled onto a guide: the edges the handle moves, nothing else. */
export function snapResize(
  start: Rect,
  next: Rect,
  handle: Handle,
  targets: Targets,
  within: number,
  keepRatio: boolean,
): { rect: Rect; guides: Guide[] } {
  const movesX = handle.includes("e") ? "right" : handle.includes("w") ? "left" : null;
  const movesY = handle.includes("s") ? "bottom" : handle.includes("n") ? "top" : null;
  const xEdge = movesX === "right" ? next.x + next.w : next.x;
  const yEdge = movesY === "bottom" ? next.y + next.h : next.y;
  const x = movesX ? nearest([xEdge], targets.xs, within) : null;
  const y = movesY ? nearest([yEdge], targets.ys, within) : null;
  if (keepRatio && handle.length === 2) {
    const fixed = opposite(start, handle);
    const useX = x && (!y || Math.abs(x.delta) <= Math.abs(y.delta));
    if (useX && x) {
      const k = Math.abs(x.at - fixed.x) / start.w;
      return { rect: scaleAbout(start, fixed, k), guides: [{ axis: "x", at: x.at }] };
    }
    if (y) {
      const k = Math.abs(y.at - fixed.y) / start.h;
      return { rect: scaleAbout(start, fixed, k), guides: [{ axis: "y", at: y.at }] };
    }
    return { rect: next, guides: [] };
  }
  const rect = { ...next };
  const guides: Guide[] = [];
  if (x) {
    if (movesX === "right") rect.w += x.delta;
    else {
      rect.x += x.delta;
      rect.w -= x.delta;
    }
    guides.push({ axis: "x", at: x.at });
  }
  if (y) {
    if (movesY === "bottom") rect.h += y.delta;
    else {
      rect.y += y.delta;
      rect.h -= y.delta;
    }
    guides.push({ axis: "y", at: y.at });
  }
  return { rect, guides };
}

/**
 * Where the layer's box goes when its frame goes from `before` to `after`: the same
 * stretch and shift on each axis. For words resized by a side, the frame IS the column,
 * so the column follows the hand; by a corner, everything scales together.
 */
export function mapBox(box: Rect, before: Rect, after: Rect): Rect {
  const sx = before.w > 0 ? after.w / before.w : 1;
  const sy = before.h > 0 ? after.h / before.h : 1;
  return {
    x: after.x + (box.x - before.x) * sx,
    y: after.y + (box.y - before.y) * sy,
    w: box.w * sx,
    h: box.h * sy,
  };
}

/** The frame put against the margin (or in the middle) of this size. */
export function alignFrame(
  frame: Rect,
  where: { x?: "left" | "center" | "right"; y?: "top" | "middle" | "bottom" },
  aspect: Aspect,
  canvas: CanvasSize,
): Rect {
  const room = band(aspect, canvas);
  let { x, y } = frame;
  if (where.x === "left") x = room.x;
  if (where.x === "center") x = (canvas.width - frame.w) / 2;
  if (where.x === "right") x = room.x + room.w - frame.w;
  if (where.y === "top") y = room.y;
  if (where.y === "middle") y = room.y + (room.h - frame.h) / 2;
  if (where.y === "bottom") y = room.y + room.h - frame.h;
  return { ...frame, x, y };
}

const round = (value: number) => Math.round(value * 100_000) / 100_000;

/** A box in this size's pixels as the fractions the server keeps. */
export function toBox(rect: Rect, canvas: CanvasSize, rotation = 0): Box {
  return {
    x: round(rect.x / canvas.width),
    y: round(rect.y / canvas.height),
    w: round(rect.w / canvas.width),
    h: round(rect.h / canvas.height),
    rotation,
  };
}

export function fromBox(box: Box, canvas: CanvasSize): Rect {
  return {
    x: box.x * canvas.width,
    y: box.y * canvas.height,
    w: box.w * canvas.width,
    h: box.h * canvas.height,
  };
}

/** The SVG transform that draws something made for `from` at `to` instead. */
export function transformBetween(from: Rect, to: Rect): string {
  const sx = from.w > 0 ? to.w / from.w : 1;
  const sy = from.h > 0 ? to.h / from.h : 1;
  const tx = to.x - from.x * sx;
  const ty = to.y - from.y * sy;
  return `matrix(${sx} 0 0 ${sy} ${tx} ${ty})`;
}

export function sameRect(a: Rect, b: Rect, within = 0.01): boolean {
  return (
    Math.abs(a.x - b.x) <= within &&
    Math.abs(a.y - b.y) <= within &&
    Math.abs(a.w - b.w) <= within &&
    Math.abs(a.h - b.h) <= within
  );
}
