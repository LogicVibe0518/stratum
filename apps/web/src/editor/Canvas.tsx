// The canvas: the poster drawn as SVG in the page, and everything a hand does to it.
// Canva's conventions throughout: hover outlines, click to select, drag to move with
// snapping guides, handles to resize, double-click words to type, arrows to nudge.

import { Minus, Plus } from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { loadFaces, pictureURL } from "../lib/assets";
import {
  CORNERS,
  type Guide,
  type Handle,
  isBackground,
  keepOn,
  layerAt,
  lockAxis,
  MIN_SIZE_PX,
  mapBox,
  moveRect,
  type Point,
  resize,
  SIDES,
  SNAP_PX,
  sameRect,
  snapMove,
  snapResize,
  snapTargets,
  type Targets,
  toBox,
  transformBetween,
  frameOf,
} from "../lib/geometry";
import { typing, mod } from "../lib/keys";
import { attachImages, prepare, type PreparedPoster } from "../lib/poster-svg";
import type { Override } from "../lib/session";
import { sentence } from "../lib/api";
import type { Aspect, CanvasView, LayerInfo, Op, Rect } from "../lib/types";
import { IconButton } from "../ui/Button";
import { type Draft, shownLayers, textShift, uniform } from "./shown";

const CURSOR: Record<Handle, string> = {
  nw: "nwse-resize",
  se: "nwse-resize",
  ne: "nesw-resize",
  sw: "nesw-resize",
  n: "ns-resize",
  s: "ns-resize",
  e: "ew-resize",
  w: "ew-resize",
};
const ZOOMS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const NUDGE_SAVE_MS = 400;
const DRAG_START_PX = 3;

let posters = 0;

const clampSize = (size: number) => Math.min(1, Math.max(0.005, size));

/** Which handles a part gets: words a column and a size, a logo only its size. */
export function handlesFor(layer: LayerInfo): Handle[] {
  // A backdrop covers the poster: like Canva's background, it is not dragged or sized.
  if (layer.locked || layer.box.rotation !== 0 || isBackground(layer)) return [];
  if (layer.kind === "logo") return [...CORNERS];
  if (layer.kind === "text") return [...CORNERS, "e", "w"];
  return [...CORNERS, ...SIDES];
}

interface Gesture {
  kind: "move" | "resize";
  layer: LayerInfo;
  handle?: Handle;
  start: Point;
  box: Rect;
  frame: Rect;
  targets: Targets;
  moved: boolean;
  pointer: number;
}

interface Live {
  kind: Gesture["kind"];
  frame: Rect;
  guides: Guide[];
  label: string;
}

export interface CanvasProps {
  view: CanvasView;
  overrides: ReadonlyMap<string, Override>;
  aspect: Aspect;
  selected: string | null;
  onSelect: (id: string | null) => void;
  editing: string | null;
  onEditing: (id: string | null) => void;
  apply: (ops: Op[]) => void;
}

export function Canvas({
  view,
  overrides,
  aspect,
  selected,
  onSelect,
  editing,
  onEditing,
  apply,
}: CanvasProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const paperRef = useRef<HTMLDivElement>(null);
  const posterRef = useRef<HTMLDivElement>(null);
  const drawn = useRef<PreparedPoster | null>(null);
  const [drawnCount, setDrawnCount] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [room, setRoom] = useState({ width: 800, height: 600 });
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  const [hover, setHover] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const latest = useRef<Rect | null>(null);
  const frameRequest = useRef(0);
  const nudge = useRef<{ layer: string; box: Rect; timer: number } | null>(null);
  const { width, height } = view.canvas;

  // --- size on screen ------------------------------------------------------------------

  useLayoutEffect(() => {
    const node = viewportRef.current;
    if (!node) return;
    const watch = new ResizeObserver(([entry]) => {
      if (entry) setRoom({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    watch.observe(node);
    return () => watch.disconnect();
  }, []);

  const fit = Math.max(0.05, Math.min((room.width - 96) / width, (room.height - 96) / height));
  const scale = zoom === "fit" ? fit : zoom;

  const zoomBy = useCallback(
    (direction: 1 | -1) => {
      setZoom((now) => {
        const current = now === "fit" ? fit : now;
        const next =
          direction > 0
            ? ZOOMS.find((one) => one > current + 0.001)
            : [...ZOOMS].reverse().find((one) => one < current - 0.001);
        return next ?? current;
      });
    },
    [fit],
  );

  useEffect(() => {
    const node = viewportRef.current;
    if (!node) return;
    // Pinch on a trackpad, or Ctrl + wheel: zoom, as in every editor.
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((now) => {
        const current = now === "fit" ? fit : now;
        return Math.min(4, Math.max(0.1, current * Math.exp(-event.deltaY * 0.01)));
      });
    };
    node.addEventListener("wheel", wheel, { passive: false });
    return () => node.removeEventListener("wheel", wheel);
  }, [fit]);

  // --- the poster ----------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    const scope = `p${++posters}-`;
    (async () => {
      const poster = prepare(view.svg, scope);
      await Promise.all([
        loadFaces(view.faces),
        attachImages(poster, (sha) => pictureURL(view.version_id, sha)),
      ]);
      const holder = posterRef.current;
      if (cancelled || !holder) return;
      holder.replaceChildren(poster.root);
      drawn.current = poster;
      setProblem(null);
      setDrawnCount((n) => n + 1);
    })().catch((failure: unknown) => {
      if (!cancelled) setProblem(sentence(failure));
    });
    return () => {
      cancelled = true;
    };
    // Faces by value: every response is a new array with the same fonts in it.
  }, [view.svg, view.version_id, JSON.stringify(view.faces)]);

  const shown = useMemo(
    () => shownLayers(view.layers, overrides, view.canvas, draft),
    [view.layers, overrides, view.canvas, draft],
  );
  const byId = useMemo(() => new Map(shown.map((one) => [one.id, one])), [shown]);

  // Each layer drawn where it is now: a transform on its wrapper until the server draws it.
  useLayoutEffect(() => {
    const poster = drawn.current;
    if (!poster) return;
    for (const layer of view.layers) {
      const wrapper = poster.layers.get(layer.id);
      const now = byId.get(layer.id);
      if (!wrapper || !now) continue;
      wrapper.style.visibility = !now.visible || editing === layer.id ? "hidden" : "";
      if (sameRect(now.px, layer.px)) wrapper.removeAttribute("transform");
      else if (layer.kind === "text" && !uniform(layer.px, now.px)) {
        const { dx, dy } = textShift(layer.px, now.px, layer.align);
        wrapper.setAttribute("transform", `translate(${dx} ${dy})`);
      } else wrapper.setAttribute("transform", transformBetween(layer.px, now.px));
    }
  }, [drawnCount, view.layers, byId, editing]);

  // A selected part that is gone (another size, another version) is no longer selected.
  useEffect(() => {
    if (selected && !byId.has(selected)) onSelect(null);
  }, [byId, selected, onSelect]);

  // --- pointer -------------------------------------------------------------------------

  const toPoster = useCallback(
    (event: { clientX: number; clientY: number }): Point => {
      const box = paperRef.current?.getBoundingClientRect();
      if (!box) return { x: 0, y: 0 };
      return { x: (event.clientX - box.left) / scale, y: (event.clientY - box.top) / scale };
    },
    [scale],
  );

  const keepsOn = (layer: LayerInfo) => layer.kind === "text" || layer.kind === "logo";

  const step = useCallback(
    (event: { clientX: number; clientY: number; shiftKey: boolean; altKey: boolean }) => {
      const now = gesture.current;
      if (!now) return;
      const point = toPoster(event);
      const within = event.altKey ? -1 : SNAP_PX / scale;
      let frame: Rect;
      let guides: Guide[] = [];
      if (now.kind === "move") {
        let dx = point.x - now.start.x;
        let dy = point.y - now.start.y;
        if (!now.moved && Math.hypot(dx, dy) * scale < DRAG_START_PX) return;
        now.moved = true;
        if (event.shiftKey) ({ dx, dy } = lockAxis(dx, dy));
        // Shift keeps the part on one line: it snaps along that line only.
        const alongX = !event.shiftKey || dx !== 0;
        const alongY = !event.shiftKey || dy !== 0;
        const snap = snapMove(moveRect(now.frame, dx, dy), now.targets, within);
        if (alongX) dx += snap.dx;
        if (alongY) dy += snap.dy;
        guides = snap.guides.filter((one) => (one.axis === "x" ? alongX : alongY));
        if (keepsOn(now.layer)) ({ dx, dy } = keepOn(now.box, dx, dy, view.canvas));
        frame = moveRect(now.frame, dx, dy);
      } else {
        now.moved = true;
        const handle = now.handle ?? "se";
        const free = event.shiftKey && (now.layer.kind === "photo" || now.layer.kind === "shape");
        const keepRatio = handle.length === 2 && !free;
        const raw = resize(now.frame, handle, point, { keepRatio, min: MIN_SIZE_PX / scale });
        const snapped = snapResize(now.frame, raw, handle, now.targets, within, keepRatio);
        frame = snapped.rect;
        guides = snapped.guides;
      }
      const box = mapBox(now.box, now.frame, frame);
      latest.current = box;
      setDraft({ layer: now.layer.id, box });
      setLive({
        kind: now.kind,
        frame,
        guides,
        label:
          now.kind === "resize"
            ? `${Math.round(frame.w)} × ${Math.round(frame.h)}`
            : `${Math.round(frame.x)}, ${Math.round(frame.y)}`,
      });
    },
    [toPoster, scale, view.canvas],
  );

  function begin(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || editing) return;
    const target = event.target as Element;
    const handle = target.getAttribute("data-handle") as Handle | null;
    const point = toPoster(event);
    let layer = handle && selected ? byId.get(selected) : layerAt(shown, point, 2 / scale);
    if (!layer) {
      onSelect(null);
      return;
    }
    if (!handle) onSelect(layer.id);
    if (layer.locked || (!handle && isBackground(layer))) return;
    layer = byId.get(layer.id) ?? layer;
    paperRef.current?.setPointerCapture(event.pointerId);
    latest.current = null;
    gesture.current = {
      kind: handle ? "resize" : "move",
      layer,
      handle: handle ?? undefined,
      start: point,
      box: layer.px,
      frame: frameOf(layer),
      targets: snapTargets(shown, layer.id, aspect, view.canvas),
      moved: false,
      pointer: event.pointerId,
    };
    event.preventDefault();
  }

  function track(event: ReactPointerEvent<HTMLDivElement>) {
    if (gesture.current) {
      const { clientX, clientY, shiftKey, altKey } = event;
      cancelAnimationFrame(frameRequest.current);
      frameRequest.current = requestAnimationFrame(() => step({ clientX, clientY, shiftKey, altKey }));
      return;
    }
    if (editing) return;
    const over = layerAt(shown, toPoster(event), 2 / scale);
    setHover(over?.id ?? null);
  }

  function end(event: ReactPointerEvent<HTMLDivElement>) {
    const now = gesture.current;
    if (!now || event.pointerId !== now.pointer) return;
    cancelAnimationFrame(frameRequest.current);
    if (event.type === "pointerup") step(event); // where the hand let go, not a frame before
    gesture.current = null;
    paperRef.current?.releasePointerCapture(event.pointerId);
    setLive(null);
    const box = latest.current;
    latest.current = null;
    if (!now.moved || !box || event.type !== "pointerup") {
      setDraft(null);
      return;
    }
    apply([placeOp(now, box)]);
    setDraft(null);
  }

  /** The change a gesture makes: where the part is now, and for words, their size. */
  function placeOp(now: Gesture, moved: Rect): Op {
    const layer = now.layer;
    const original = view.layers.find((one) => one.id === layer.id) ?? layer;
    let box = moved;
    const op: Extract<Op, { op: "place" }> = {
      op: "place",
      layer: layer.id,
      aspect,
      box: toBox(box, view.canvas, layer.box.rotation),
    };
    if (now.kind !== "resize" || layer.kind !== "text" || !original.size_px) return op;
    if ((now.handle ?? "").length === 2) {
      // A corner: the words grow or shrink with the box, as in every editor.
      op.text_size = clampSize((original.size_px * box.w) / original.px.w / height);
      return op;
    }
    // A side: the words keep their size and re-flow. The column takes as many lines as
    // they now need, growing down from the top line, instead of shrinking the words.
    const lines = Math.max(original.lines?.length ?? 1, 1);
    const top = now.frame.y;
    const lineHeight = now.frame.h / lines;
    const needed = Math.min(12, Math.max(1, Math.ceil(((lines * now.frame.w) / box.w) * 1.08)));
    box = { x: box.x, w: box.w, y: top, h: Math.min(height - top, needed * lineHeight * 1.02) };
    op.box = toBox(box, view.canvas, layer.box.rotation);
    op.text_size = clampSize(original.size_px / height);
    op.max_lines = Math.max(needed, lines);
    return op;
  }

  function doubleClick(event: React.MouseEvent<HTMLDivElement>) {
    const layer = layerAt(shown, toPoster(event), 2 / scale);
    if (layer?.kind === "text" && !layer.locked) {
      onSelect(layer.id);
      onEditing(layer.id);
    }
  }

  // --- keys: arrows nudge, Esc lets go, zoom ------------------------------------------

  const saveNudge = useCallback(() => {
    const held = nudge.current;
    if (!held) return;
    nudge.current = null;
    const layer = view.layers.find((one) => one.id === held.layer);
    if (!layer) return;
    apply([{ op: "place", layer: layer.id, aspect, box: toBox(held.box, view.canvas, layer.box.rotation) }]);
    setDraft(null);
  }, [apply, aspect, view.canvas, view.layers]);

  useEffect(() => () => saveNudge(), [saveNudge]);

  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (event.defaultPrevented || typing(event.target)) return;
      if (mod(event) && (event.key === "=" || event.key === "+")) {
        event.preventDefault();
        zoomBy(1);
        return;
      }
      if (mod(event) && event.key === "-") {
        event.preventDefault();
        zoomBy(-1);
        return;
      }
      if (mod(event) && event.key === "0") {
        event.preventDefault();
        setZoom("fit");
        return;
      }
      const arrows: Record<string, [number, number]> = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      };
      const arrow = arrows[event.key];
      if (!arrow || !selected || mod(event) || event.altKey) return;
      const layer = byId.get(selected);
      if (!layer || layer.locked || isBackground(layer)) return;
      event.preventDefault();
      const size = event.shiftKey ? 10 : 1;
      const from = nudge.current?.layer === layer.id ? nudge.current.box : layer.px;
      let dx = arrow[0] * size;
      let dy = arrow[1] * size;
      if (keepsOn(layer)) ({ dx, dy } = keepOn(from, dx, dy, view.canvas));
      const box = moveRect(from, dx, dy);
      if (nudge.current) window.clearTimeout(nudge.current.timer);
      nudge.current = { layer: layer.id, box, timer: window.setTimeout(saveNudge, NUDGE_SAVE_MS) };
      setDraft({ layer: layer.id, box });
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [selected, byId, view.canvas, saveNudge, zoomBy]);

  // --- drawing the overlay ------------------------------------------------------------

  const chosen = selected ? byId.get(selected) : undefined;
  const over = hover && hover !== selected && !live ? byId.get(hover) : undefined;
  const hovered = over && !isBackground(over) ? over : undefined;
  const handleSize = 9 / scale;
  const sideLong = 16 / scale;
  const frame = live?.frame ?? (chosen ? frameOf(chosen) : null);

  return (
    <div className="viewport" ref={viewportRef}>
      <div
        ref={paperRef}
        className="paper"
        style={{ width: width * scale, height: height * scale }}
        onPointerDown={begin}
        onPointerMove={track}
        onPointerUp={end}
        onPointerCancel={end}
        onPointerLeave={() => setHover(null)}
        onDoubleClick={doubleClick}
        role="application"
        aria-label="Poster canvas"
      >
        <div className="poster" ref={posterRef} />
        <svg
          className="overlay"
          viewBox={`0 0 ${width} ${height}`}
          width={width * scale}
          height={height * scale}
        >
          {hovered && hovered.visible ? <OutlineRect rect={frameOf(hovered)} className="hover" /> : null}
          {live?.guides.map((guide, index) =>
            guide.axis === "x" ? (
              <line
                key={index}
                className="guide"
                x1={guide.at}
                x2={guide.at}
                y1={0}
                y2={height}
                vectorEffect="non-scaling-stroke"
              />
            ) : (
              <line
                key={index}
                className="guide"
                y1={guide.at}
                y2={guide.at}
                x1={0}
                x2={width}
                vectorEffect="non-scaling-stroke"
              />
            ),
          )}
          {chosen && frame && editing !== chosen.id ? (
            <g
              transform={
                chosen.box.rotation
                  ? `rotate(${chosen.box.rotation} ${frame.x + frame.w / 2} ${frame.y + frame.h / 2})`
                  : undefined
              }
            >
              {chosen.kind === "text" && live ? <OutlineRect rect={chosen.px} className="column" /> : null}
              <OutlineRect rect={frame} className="selection" dashed={!chosen.visible} />
              {chosen.visible && live?.kind !== "move"
                ? handlesFor(chosen).map((handle) => {
                    const at = handlePoint(frame, handle);
                    const side = handle.length === 1;
                    const w = side
                      ? handle === "n" || handle === "s"
                        ? sideLong
                        : handleSize * 0.7
                      : handleSize;
                    const h = side
                      ? handle === "e" || handle === "w"
                        ? sideLong
                        : handleSize * 0.7
                      : handleSize;
                    return (
                      <rect
                        key={handle}
                        data-handle={handle}
                        className={side ? "handle-side" : "handle"}
                        x={at.x - w / 2}
                        y={at.y - h / 2}
                        width={w}
                        height={h}
                        rx={side ? Math.min(w, h) / 2 : 2 / scale}
                        vectorEffect="non-scaling-stroke"
                        style={{ cursor: CURSOR[handle] }}
                      />
                    );
                  })
                : null}
              {live ? <Tag frame={frame} scale={scale} text={live.label} /> : null}
            </g>
          ) : null}
        </svg>
        {editing && byId.get(editing) ? (
          <TextEditor
            layer={byId.get(editing) as LayerInfo}
            poster={drawn.current}
            scale={scale}
            onDone={(text) => {
              const layer = byId.get(editing);
              onEditing(null);
              if (layer && text !== null && text.trim() && text.trim() !== layer.text) {
                apply([{ op: "set_text", layer: layer.id, text: text.trim().slice(0, 500) }]);
              }
            }}
          />
        ) : null}
      </div>
      {problem ? <div className="stage-message">{problem}</div> : null}
      <div className="zoom" role="group" aria-label="Zoom">
        <IconButton label="Zoom out" shortcut={modLabel("-")} size="sm" onClick={() => zoomBy(-1)}>
          <Minus size={14} />
        </IconButton>
        <button
          type="button"
          className="btn btn-ghost btn-sm level"
          onClick={() => setZoom("fit")}
          title="Fit to screen"
        >
          {Math.round(scale * 100)}%
        </button>
        <IconButton label="Zoom in" shortcut={modLabel("+")} size="sm" onClick={() => zoomBy(1)}>
          <Plus size={14} />
        </IconButton>
      </div>
    </div>
  );
}

function modLabel(key: string): string {
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? `⌘${key}` : `Ctrl+${key}`;
}

function handlePoint(rect: Rect, handle: Handle): Point {
  const x = handle.includes("w") ? rect.x : handle.includes("e") ? rect.x + rect.w : rect.x + rect.w / 2;
  const y = handle.includes("n") ? rect.y : handle.includes("s") ? rect.y + rect.h : rect.y + rect.h / 2;
  return { x, y };
}

function OutlineRect({
  rect,
  className,
  dashed = false,
}: {
  rect: Rect;
  className: string;
  dashed?: boolean;
}) {
  return (
    <rect
      className={className}
      x={rect.x}
      y={rect.y}
      width={Math.max(rect.w, 0)}
      height={Math.max(rect.h, 0)}
      vectorEffect="non-scaling-stroke"
      strokeDasharray={dashed ? "6 4" : undefined}
    />
  );
}

/** The size or position while a part is being moved, under it, as Figma shows it. */
function Tag({ frame, scale, text }: { frame: Rect; scale: number; text: string }) {
  const h = 22 / scale;
  const w = (text.length * 7.2 + 16) / scale;
  const x = frame.x + frame.w / 2 - w / 2;
  const y = frame.y + frame.h + 8 / scale;
  return (
    <g transform={`translate(${x} ${y}) scale(${1 / scale})`}>
      <rect className="tag-bg" width={w * scale} height={h * scale} rx={6} />
      <text className="tag" x={(w * scale) / 2} y={15} textAnchor="middle">
        {text}
      </text>
    </g>
  );
}

/** Words typed in place, in the brand's font at the size they are drawn. */
function TextEditor({
  layer,
  poster,
  scale,
  onDone,
}: {
  layer: LayerInfo;
  poster: PreparedPoster | null;
  scale: number;
  onDone: (text: string | null) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  const drawnText = poster?.layers.get(layer.id)?.querySelector("text");
  const family = drawnText?.getAttribute("font-family") ?? "inherit";
  const weight = drawnText?.getAttribute("font-weight") ?? "400";
  const size = (layer.size_px ?? 40) * scale;
  const lines = Math.max(layer.lines?.length ?? 1, 1);
  const lineHeight = layer.ink ? (layer.ink.h / lines) * scale : size * 1.15;
  const frame = frameOf(layer);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.focus();
    node.select();
  }, []);

  function grow() {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }

  useLayoutEffect(grow, []);

  function finish(text: string | null) {
    if (done.current) return;
    done.current = true;
    onDone(text);
  }

  function key(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      finish(null);
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      finish(event.currentTarget.value);
    }
  }

  return (
    <textarea
      ref={ref}
      className="text-editor"
      aria-label={`Words: ${layer.name}`}
      defaultValue={layer.text ?? ""}
      maxLength={500}
      spellCheck
      onInput={grow}
      onKeyDown={key}
      onBlur={(event) => finish(event.currentTarget.value)}
      onPointerDown={(event) => event.stopPropagation()}
      style={{
        left: frame.x * scale,
        top: frame.y * scale,
        width: frame.w * scale,
        minHeight: frame.h * scale,
        font: `${weight} ${size}px/${lineHeight}px ${family}`,
        color: layer.colour?.hex ?? "inherit",
        textAlign: layer.align ?? "left",
        textTransform: layer.uppercase ? "uppercase" : "none",
      }}
    />
  );
}
