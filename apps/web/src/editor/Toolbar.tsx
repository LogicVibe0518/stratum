// The bar above the canvas, changing with what is selected - Canva's top toolbar.
// Words, colours, fonts and effects change every size; where a part sits and how big it
// is change the size on screen only.

import * as Dropdown from "@radix-ui/react-dropdown-menu";
import * as Popover from "@radix-ui/react-popover";
import * as Slider from "@radix-ui/react-slider";
import {
  AlignCenter,
  AlignHorizontalJustifyCenter,
  AlignHorizontalJustifyEnd,
  AlignHorizontalJustifyStart,
  AlignLeft,
  AlignRight,
  AlignVerticalJustifyCenter,
  AlignVerticalJustifyEnd,
  AlignVerticalJustifyStart,
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpToLine,
  Blend,
  CaseSensitive,
  ChevronDown,
  EyeOff,
  FlipHorizontal2,
  LayoutGrid,
  Lock,
  Minus,
  Pencil,
  Plus,
  SunDim,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { distinctColours } from "../lib/colours";
import { alignFrame, frameOf, isBackground, mapBox, scaleAbout, toBox } from "../lib/geometry";
import type { Aspect, CanvasView, LayerInfo, Op } from "../lib/types";
import { Button, Hint, IconButton } from "../ui/Button";

export interface ToolbarProps {
  layer: LayerInfo | undefined;
  view: CanvasView;
  aspect: Aspect;
  apply: (ops: Op[]) => void;
  preview: (ops: Op[]) => void;
  onEditText: () => void;
  onHide: () => void;
}

const SIZE_NAME: Record<Aspect, string> = {
  "4:5": "post",
  "1:1": "square",
  "9:16": "story",
  "16:9": "banner",
};

export function Toolbar({ layer, view, aspect, apply, preview, onEditText, onHide }: ToolbarProps) {
  if (!layer) {
    return (
      <div className="context-bar" role="toolbar" aria-label="Edit">
        <span className="hint">Click any part of the poster to change it. Double-click words to type.</span>
      </div>
    );
  }
  const locked = layer.locked;
  return (
    <div className="context-bar" role="toolbar" aria-label={`Edit ${layer.name}`}>
      {locked ? (
        <span className="hint" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <Lock size={14} /> {layer.name} is locked in this design.
        </span>
      ) : null}
      {!locked && layer.kind === "text" ? (
        <TextTools layer={layer} view={view} aspect={aspect} apply={apply} onEditText={onEditText} />
      ) : null}
      {!locked && layer.kind === "photo" ? (
        <PhotoTools layer={layer} apply={apply} preview={preview} />
      ) : null}
      {!locked && layer.kind === "shape" ? <ColourMenu layer={layer} view={view} apply={apply} /> : null}
      {!locked ? (
        <>
          <span className="sep" />
          <Transparency layer={layer} apply={apply} preview={preview} />
          <Position layer={layer} view={view} aspect={aspect} apply={apply} />
          <IconButton label="Hide" shortcut="Delete" onClick={onHide}>
            <EyeOff size={16} />
          </IconButton>
        </>
      ) : null}
      {isBackground(layer) && !locked ? <span className="hint">Background: it stays in place.</span> : null}
    </div>
  );
}

function TextTools({
  layer,
  view,
  aspect,
  apply,
  onEditText,
}: {
  layer: LayerInfo;
  view: CanvasView;
  aspect: Aspect;
  apply: (ops: Op[]) => void;
  onEditText: () => void;
}) {
  const family = view.fonts.find((one) => one.token === layer.font)?.family ?? "Font";
  const size = layer.size_px ?? 0;

  function scaleWords(k: number) {
    const frame = frameOf(layer);
    const centre = { x: frame.x + frame.w / 2, y: frame.y + frame.h / 2 };
    const box = mapBox(layer.px, frame, scaleAbout(frame, centre, k));
    apply([
      {
        op: "place",
        layer: layer.id,
        aspect,
        box: toBox(box, view.canvas, layer.box.rotation),
        text_size: Math.min(1, Math.max(0.005, (size * k) / view.canvas.height)),
      },
    ]);
  }

  return (
    <>
      <IconButton label="Edit words" shortcut="Enter" onClick={onEditText}>
        <Pencil size={16} />
      </IconButton>
      <Dropdown.Root>
        <Hint label="Font">
          <Dropdown.Trigger asChild>
            <Button
              variant="ghost"
              style={{ minWidth: 120, justifyContent: "space-between", fontFamily: family }}
            >
              {family} <ChevronDown size={14} />
            </Button>
          </Dropdown.Trigger>
        </Hint>
        <Dropdown.Portal>
          <Dropdown.Content className="float" sideOffset={6} align="start">
            <div className="menu-label">The brand's fonts</div>
            {view.fonts.map((font) => (
              <Dropdown.Item
                key={font.token}
                className="menu-item"
                style={{ fontFamily: font.family }}
                onSelect={() =>
                  font.token !== layer.font && apply([{ op: "set_font", layer: layer.id, font: font.token }])
                }
              >
                {font.family}
                <span className="end">
                  {font.token === layer.font ? "✓" : font.token.replace("font.", "").replace("_", " ")}
                </span>
              </Dropdown.Item>
            ))}
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
      <div style={{ display: "inline-flex", alignItems: "center" }} role="group" aria-label="Size">
        <IconButton
          label={`Smaller (this ${SIZE_NAME[aspect]})`}
          size="sm"
          onClick={() => scaleWords(1 / 1.1)}
        >
          <Minus size={14} />
        </IconButton>
        <Hint label="Size, in pixels of this size">
          <span
            style={{ minWidth: 34, textAlign: "center", fontVariantNumeric: "tabular-nums", fontSize: 13 }}
          >
            {Math.round(size)}
          </span>
        </Hint>
        <IconButton label={`Bigger (this ${SIZE_NAME[aspect]})`} size="sm" onClick={() => scaleWords(1.1)}>
          <Plus size={14} />
        </IconButton>
      </div>
      <ColourMenu layer={layer} view={view} apply={apply} />
      <span className="sep" />
      {(["left", "center", "right"] as const).map((align) => (
        <IconButton
          key={align}
          label={`Align ${align === "center" ? "centre" : align}`}
          aria-pressed={layer.align === align}
          onClick={() => layer.align !== align && apply([{ op: "set_align", layer: layer.id, align }])}
        >
          {align === "left" ? (
            <AlignLeft size={16} />
          ) : align === "center" ? (
            <AlignCenter size={16} />
          ) : (
            <AlignRight size={16} />
          )}
        </IconButton>
      ))}
      <IconButton
        label={layer.uppercase ? "Normal case" : "Capitals"}
        aria-pressed={Boolean(layer.uppercase)}
        onClick={() => apply([{ op: "set_case", layer: layer.id, upper: !layer.uppercase }])}
      >
        <CaseSensitive size={18} />
      </IconButton>
      <Dropdown.Root>
        <Hint label="Effect">
          <Dropdown.Trigger asChild>
            <Button variant="ghost">
              Effect <ChevronDown size={14} />
            </Button>
          </Dropdown.Trigger>
        </Hint>
        <Dropdown.Portal>
          <Dropdown.Content className="float" sideOffset={6} align="start">
            {(["none", "shadow", "outline"] as const).map((kind) => {
              const current = layer.effect?.kind ?? "none";
              return (
                <Dropdown.Item
                  key={kind}
                  className="menu-item"
                  onSelect={() => kind !== current && apply([{ op: "text_effect", layer: layer.id, kind }])}
                >
                  {kind === "none" ? "None" : kind === "shadow" ? "Shadow" : "Outline"}
                  <span className="end">{kind === current ? "✓" : ""}</span>
                </Dropdown.Item>
              );
            })}
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
    </>
  );
}

function ColourMenu({
  layer,
  view,
  apply,
}: {
  layer: LayerInfo;
  view: CanvasView;
  apply: (ops: Op[]) => void;
}) {
  const current = layer.colour;
  const same = (hex: string) => hex.toLowerCase() === current?.hex.toLowerCase();
  return (
    <Popover.Root>
      <Hint label={`Colour${current ? `: ${current.name}` : ""}`}>
        <Popover.Trigger asChild>
          <Button variant="ghost" icon aria-label="Colour">
            <span
              style={{
                width: 18,
                height: 18,
                borderRadius: "50%",
                background: current?.hex ?? "transparent",
                border: "1px solid var(--line-strong)",
              }}
            />
          </Button>
        </Popover.Trigger>
      </Hint>
      <Popover.Portal>
        <Popover.Content
          className="float"
          sideOffset={6}
          align="start"
          style={{ width: 240, padding: 12 }}
          // Focus stays on the poster: a tooltip popping up on the first swatch is noise.
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <div className="menu-label" style={{ padding: "0 0 8px" }}>
            Brand colours
          </div>
          <div className="swatches">
            {distinctColours(view).map((colour) => (
              <Hint key={colour.token} label={colour.name}>
                <button
                  type="button"
                  className="swatch"
                  style={{ background: colour.hex }}
                  aria-label={colour.name}
                  aria-pressed={same(colour.hex)}
                  onClick={() =>
                    !same(colour.hex) && apply([{ op: "set_color", layer: layer.id, color: colour.token }])
                  }
                />
              </Hint>
            ))}
          </div>
          <p className="subtle" style={{ fontSize: 12, marginTop: 10 }}>
            Only the client's own colours, so every post stays on brand.
          </p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function PhotoTools({
  layer,
  apply,
  preview,
}: {
  layer: LayerInfo;
  apply: (ops: Op[]) => void;
  preview: (ops: Op[]) => void;
}) {
  return (
    <>
      <SliderButton
        label="Darken"
        icon={<SunDim size={16} />}
        value={Math.round((layer.dim ?? 0) * 100)}
        min={0}
        max={80}
        unit="%"
        toOp={(value) => ({ op: "dim", layer: layer.id, amount: value / 100 })}
        apply={apply}
        preview={preview}
      />
      <IconButton label="Flip" onClick={() => apply([{ op: "flip", layer: layer.id }])}>
        <FlipHorizontal2 size={16} />
      </IconButton>
    </>
  );
}

function Transparency({
  layer,
  apply,
  preview,
}: {
  layer: LayerInfo;
  apply: (ops: Op[]) => void;
  preview: (ops: Op[]) => void;
}) {
  return (
    <SliderButton
      label="Transparency"
      icon={<Blend size={16} />}
      value={Math.round((layer.opacity ?? 1) * 100)}
      min={10}
      max={100}
      unit="%"
      toOp={(value) => ({ op: "set_opacity", layer: layer.id, opacity: value / 100 })}
      apply={apply}
      preview={preview}
    />
  );
}

/** A slider in a popover: the poster follows while dragging, saved on letting go. */
function SliderButton({
  label,
  icon,
  value,
  min,
  max,
  unit,
  toOp,
  apply,
  preview,
}: {
  label: string;
  icon: ReactNode;
  value: number;
  min: number;
  max: number;
  unit: string;
  toOp: (value: number) => Op;
  apply: (ops: Op[]) => void;
  preview: (ops: Op[]) => void;
}) {
  const [shown, setShown] = useState(value);
  const timer = useRef(0);
  // What the poster had before this slide: the previews change `value` as it moves.
  const before = useRef<number | null>(null);
  useEffect(() => setShown(value), [value]);

  /** Keep what the slider shows: on letting go, when the panel closes, and when the
   * selection moves on - the poster on screen never shows a change that was not kept. */
  function keep(next: number) {
    window.clearTimeout(timer.current);
    const from = before.current;
    before.current = null;
    if (from !== null && next !== from) apply([toOp(next)]);
  }
  const keepLater = useRef(keep);
  keepLater.current = keep;
  const last = useRef(shown);
  last.current = shown;
  useEffect(() => () => keepLater.current(last.current), []);

  return (
    <Popover.Root onOpenChange={(open) => !open && keep(shown)}>
      <Hint label={label}>
        <Popover.Trigger asChild>
          <Button variant="ghost" icon aria-label={label}>
            {icon}
          </Button>
        </Popover.Trigger>
      </Hint>
      <Popover.Portal>
        <Popover.Content
          className="float"
          sideOffset={6}
          align="start"
          style={{ width: 240, padding: 12, display: "grid", gap: 10 }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{label}</span>
            <span className="muted" style={{ fontVariantNumeric: "tabular-nums" }}>
              {shown}
              {unit}
            </span>
          </div>
          <Slider.Root
            className="slider"
            min={min}
            max={max}
            step={1}
            value={[shown]}
            onValueChange={([next]) => {
              if (next === undefined) return;
              before.current ??= value;
              setShown(next);
              window.clearTimeout(timer.current);
              timer.current = window.setTimeout(() => preview([toOp(next)]), 60);
            }}
            onValueCommit={([next]) => next !== undefined && keep(next)}
            aria-label={label}
          >
            <Slider.Track className="slider-track">
              <Slider.Range className="slider-range" />
            </Slider.Track>
            <Slider.Thumb className="slider-thumb" aria-label={label} />
          </Slider.Root>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Position({
  layer,
  view,
  aspect,
  apply,
}: {
  layer: LayerInfo;
  view: CanvasView;
  aspect: Aspect;
  apply: (ops: Op[]) => void;
}) {
  const background = isBackground(layer);

  function align(where: { x?: "left" | "center" | "right"; y?: "top" | "middle" | "bottom" }) {
    const frame = frameOf(layer);
    const box = mapBox(layer.px, frame, alignFrame(frame, where, aspect, view.canvas));
    apply([{ op: "place", layer: layer.id, aspect, box: toBox(box, view.canvas, layer.box.rotation) }]);
  }

  const order = (to: "front" | "back" | "forward" | "backward") =>
    apply([{ op: "reorder", layer: layer.id, to }]);

  return (
    <Popover.Root>
      <Hint label="Position: align and layer order">
        <Popover.Trigger asChild>
          <Button variant="ghost" icon aria-label="Position">
            <LayoutGrid size={16} />
          </Button>
        </Popover.Trigger>
      </Hint>
      <Popover.Portal>
        <Popover.Content
          className="float"
          sideOffset={6}
          align="start"
          style={{ width: 264, padding: 12, display: "grid", gap: 8 }}
        >
          <div className="menu-label" style={{ padding: 0 }}>
            Layer order
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            <Button size="sm" onClick={() => order("forward")}>
              <ArrowUp size={14} /> Forward
            </Button>
            <Button size="sm" onClick={() => order("backward")}>
              <ArrowDown size={14} /> Backward
            </Button>
            <Button size="sm" onClick={() => order("front")}>
              <ArrowUpToLine size={14} /> To front
            </Button>
            <Button size="sm" onClick={() => order("back")}>
              <ArrowDownToLine size={14} /> To back
            </Button>
          </div>
          {!background ? (
            <>
              <div className="menu-label" style={{ padding: "6px 0 0" }}>
                Align to page (this {SIZE_NAME[aspect]})
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                <Button size="sm" onClick={() => align({ y: "top" })}>
                  <AlignVerticalJustifyStart size={14} /> Top
                </Button>
                <Button size="sm" onClick={() => align({ x: "left" })}>
                  <AlignHorizontalJustifyStart size={14} /> Left
                </Button>
                <Button size="sm" onClick={() => align({ y: "middle" })}>
                  <AlignVerticalJustifyCenter size={14} /> Middle
                </Button>
                <Button size="sm" onClick={() => align({ x: "center" })}>
                  <AlignHorizontalJustifyCenter size={14} /> Centre
                </Button>
                <Button size="sm" onClick={() => align({ y: "bottom" })}>
                  <AlignVerticalJustifyEnd size={14} /> Bottom
                </Button>
                <Button size="sm" onClick={() => align({ x: "right" })}>
                  <AlignHorizontalJustifyEnd size={14} /> Right
                </Button>
              </div>
            </>
          ) : null}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
