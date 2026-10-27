import { describe, expect, it } from "vitest";
import {
  alignFrame,
  band,
  frameOf,
  isBackground,
  keepOn,
  layerAt,
  lockAxis,
  mapBox,
  photoAt,
  resize,
  snapMove,
  snapResize,
  snapTargets,
  toBox,
  transformBetween,
} from "./geometry";
import type { LayerInfo } from "./types";

const POST = { width: 1080, height: 1350 };
const STORY = { width: 1080, height: 1920 };

function layer(over: Partial<LayerInfo> & Pick<LayerInfo, "id" | "px">): LayerInfo {
  const { px } = over;
  return {
    name: over.id,
    kind: "shape",
    visible: true,
    locked: false,
    box: { x: px.x / 1080, y: px.y / 1350, w: px.w / 1080, h: px.h / 1350, rotation: 0 },
    opacity: 1,
    order: 0,
    ...over,
  };
}

const bg = layer({ id: "bg", kind: "photo", px: { x: 0, y: 0, w: 1080, h: 1350 }, order: 0 });
const panel = layer({ id: "panel", px: { x: 0, y: 800, w: 1080, h: 550 }, order: 1 });
const headline = layer({
  id: "headline",
  kind: "text",
  px: { x: 80, y: 820, w: 920, h: 300 },
  ink: { x: 80, y: 900, w: 700, h: 140 },
  order: 2,
});
const logo = layer({ id: "logo", kind: "logo", px: { x: 80, y: 80, w: 270, h: 88 }, order: 3 });
const LAYERS = [bg, panel, headline, logo];

describe("what is under the pointer", () => {
  it("frames words by their column and the lines actually drawn", () => {
    expect(frameOf(headline)).toEqual({ x: 80, y: 900, w: 920, h: 140 });
    expect(frameOf(logo)).toEqual(logo.px);
  });

  it("picks the topmost part, and words over the panel they sit on", () => {
    expect(layerAt(LAYERS, { x: 100, y: 950 })?.id).toBe("headline");
    expect(layerAt(LAYERS, { x: 100, y: 1200 })?.id).toBe("panel");
    expect(layerAt(LAYERS, { x: 600, y: 400 })?.id).toBe("bg");
    // Inside the text box but above the drawn lines: the panel, not the words.
    expect(layerAt(LAYERS, { x: 100, y: 840 })?.id).toBe("panel");
  });

  it("skips hidden parts", () => {
    const hidden = { ...logo, visible: false };
    expect(layerAt([bg, hidden], { x: 100, y: 100 })?.id).toBe("bg");
  });

  it("knows a backdrop", () => {
    expect(isBackground(bg)).toBe(true);
    expect(isBackground(panel)).toBe(false);
  });
});

describe("snapping", () => {
  it("uses the server's margins, and a story's safe band", () => {
    const post = band("4:5", POST);
    expect(post.x).toBeCloseTo(0.074 * 1080);
    expect(post.y).toBeCloseTo(0.074 * 1080);
    const story = band("9:16", STORY);
    expect(story.y).toBeCloseTo(0.14 * 1920);
    expect(story.y + story.h).toBeCloseTo(0.8 * 1920);
  });

  it("pulls a part's centre onto the poster's centre", () => {
    const targets = snapTargets(LAYERS, "logo", "4:5", POST);
    const moved = { x: 400, y: 80, w: 270, h: 88 }; // centre at 535, 5 px off 540
    const snap = snapMove(moved, targets, 6);
    expect(snap.dx).toBe(5);
    expect(snap.guides).toContainEqual({ axis: "x", at: 540 });
  });

  it("lines a part up with another part's edge", () => {
    const targets = snapTargets(LAYERS, "logo", "4:5", POST);
    const moved = { x: 84, y: 300, w: 270, h: 88 }; // left edge 4 px off the headline's 80
    expect(snapMove(moved, targets, 6).dx).toBe(-4);
  });

  it("leaves a part alone when nothing is near", () => {
    const targets = snapTargets([bg], "logo", "4:5", POST);
    const snap = snapMove({ x: 300, y: 300, w: 100, h: 100 }, targets, 6);
    expect(snap).toEqual({ dx: 0, dy: 0, guides: [] });
  });

  it("never snaps to the part being moved, or to a backdrop's edges twice", () => {
    expect(snapTargets(LAYERS, "headline", "4:5", POST).ys).not.toContain(900);
    const targets = snapTargets([bg, logo], "logo", "4:5", POST);
    expect(targets.xs.filter((x) => x === 1080)).toHaveLength(1);
  });

  it("locks to one axis with shift", () => {
    expect(lockAxis(10, 3)).toEqual({ dx: 10, dy: 0 });
    expect(lockAxis(-2, 7)).toEqual({ dx: 0, dy: 7 });
  });

  it("stops words at the poster's edge", () => {
    expect(keepOn({ x: 80, y: 80, w: 270, h: 88 }, -200, 0, POST)).toEqual({ dx: -80, dy: 0 });
    expect(keepOn({ x: 80, y: 80, w: 270, h: 88 }, 0, 2000, POST).dy).toBe(1350 - 80 - 88);
  });
});

describe("resizing", () => {
  const start = { x: 100, y: 100, w: 200, h: 100 };

  it("keeps the shape from a corner, with the opposite corner pinned", () => {
    const out = resize(start, "se", { x: 500, y: 200 }, { keepRatio: true, min: 10 });
    expect(out.x).toBe(100);
    expect(out.y).toBe(100);
    expect(out.w / out.h).toBeCloseTo(2);
    const nw = resize(start, "nw", { x: 0, y: 50 }, { keepRatio: true, min: 10 });
    expect(nw.x + nw.w).toBeCloseTo(300);
    expect(nw.y + nw.h).toBeCloseTo(200);
    expect(nw.w / nw.h).toBeCloseTo(2);
  });

  it("never turns inside out or below the smallest size", () => {
    const out = resize(start, "se", { x: -500, y: -500 }, { keepRatio: true, min: 10 });
    expect(out.h).toBeCloseTo(10);
    const side = resize(start, "w", { x: 900, y: 0 }, { keepRatio: false, min: 10 });
    expect(side).toEqual({ x: 290, y: 100, w: 10, h: 100 });
  });

  it("moves one edge from a side", () => {
    expect(resize(start, "e", { x: 400, y: 999 }, { keepRatio: false, min: 10 })).toEqual({
      ...start,
      w: 300,
    });
    expect(resize(start, "n", { x: 0, y: 50 }, { keepRatio: false, min: 10 })).toEqual({
      ...start,
      y: 50,
      h: 150,
    });
  });

  it("snaps the edge being dragged", () => {
    const targets = { xs: [403], ys: [] };
    const next = resize(start, "e", { x: 400, y: 0 }, { keepRatio: false, min: 10 });
    const snapped = snapResize(start, next, "e", targets, 6, false);
    expect(snapped.rect.w).toBe(303);
    expect(snapped.guides).toEqual([{ axis: "x", at: 403 }]);
  });

  it("snaps a corner and keeps the shape", () => {
    const targets = { xs: [505], ys: [] };
    const next = resize(start, "se", { x: 500, y: 300 }, { keepRatio: true, min: 10 });
    const snapped = snapResize(start, next, "se", targets, 6, true);
    expect(snapped.rect.x + snapped.rect.w).toBeCloseTo(505);
    expect(snapped.rect.w / snapped.rect.h).toBeCloseTo(2);
  });
});

describe("boxes", () => {
  it("moves the text box with its frame, and scales it with a corner", () => {
    const frame = frameOf(headline);
    const moved = mapBox(headline.px, frame, { ...frame, x: frame.x + 10, y: frame.y - 20 });
    expect(moved).toEqual({ x: 90, y: 800, w: 920, h: 300 });
    const bigger = mapBox(headline.px, frame, { x: 80, y: 900, w: 1840, h: 280 });
    expect(bigger).toEqual({ x: 80, y: 740, w: 1840, h: 600 });
  });

  it("widens only the column from a side", () => {
    const frame = frameOf(headline);
    const wider = mapBox(headline.px, frame, { ...frame, w: frame.w - 100 });
    expect(wider).toEqual({ x: 80, y: 820, w: 820, h: 300 });
  });

  it("aligns to the margin like the server does", () => {
    const left = alignFrame({ x: 300, y: 300, w: 100, h: 50 }, { x: "left" }, "4:5", POST);
    expect(left.x).toBeCloseTo(0.074 * 1080);
    const middle = alignFrame({ x: 0, y: 0, w: 100, h: 50 }, { x: "center", y: "middle" }, "4:5", POST);
    expect(middle.x).toBe(490);
    expect(middle.y + 25).toBeCloseTo(675);
  });

  it("sends fractions, rounded", () => {
    expect(toBox({ x: 108, y: 135, w: 540, h: 675 }, POST)).toEqual({
      x: 0.1,
      y: 0.1,
      w: 0.5,
      h: 0.5,
      rotation: 0,
    });
    expect(toBox({ x: 1 / 3, y: 0, w: 1, h: 1 }, { width: 1, height: 1 }).x).toBe(0.33333);
  });

  it("draws a part somewhere else with one matrix", () => {
    expect(transformBetween({ x: 0, y: 0, w: 10, h: 10 }, { x: 5, y: 5, w: 20, h: 20 })).toBe(
      "matrix(2 0 0 2 5 5)",
    );
  });
});

describe("dropping a photo from the panel", () => {
  const right = layer({ id: "photo_b", kind: "photo", px: { x: 540, y: 0, w: 540, h: 700 }, order: 2 });
  const fixed = layer({
    id: "photo_c",
    kind: "photo",
    locked: true,
    px: { x: 0, y: 0, w: 540, h: 700 },
    order: 3,
  });

  it("lands on the topmost photo under the pointer, never on words or a locked photo", () => {
    const layers = [...LAYERS, right, fixed];
    expect(photoAt(layers, { x: 800, y: 300 })?.id).toBe("photo_b");
    expect(photoAt(layers, { x: 200, y: 300 })?.id).toBe("bg"); // the locked one is skipped
    expect(photoAt([panel, headline], { x: 200, y: 900 })).toBeNull();
  });
});
