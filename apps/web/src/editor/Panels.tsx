// The side panels: Layers and Brand on the left (Canva's side panel), Ask Stratum on the right.

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Award,
  Eye,
  EyeOff,
  Image,
  Lock,
  OctagonAlert,
  PanelRightClose,
  Scissors,
  SendHorizontal,
  Square,
  Type,
  X,
} from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { sentence } from "../lib/api";
import { distinctColours } from "../lib/colours";
import type { EditorSession } from "../lib/session";
import type { Aspect, CanvasView, ChangeResult, Flag, LayerInfo, LayerKind, Op } from "../lib/types";
import { Button, IconButton } from "../ui/Button";

const ICONS: Record<LayerKind, typeof Type> = {
  text: Type,
  photo: Image,
  logo: Award,
  shape: Square,
  cutout: Scissors,
};

export const named = (name: string) => name.charAt(0).toUpperCase() + name.slice(1);

const KIND: Record<LayerKind, string> = {
  text: "Words",
  photo: "Photo",
  logo: "Logo",
  shape: "Shape",
  cutout: "Cut-out",
};

export function LayersPanel({
  layers,
  selected,
  onSelect,
  apply,
}: {
  layers: LayerInfo[];
  selected: string | null;
  onSelect: (id: string) => void;
  apply: (ops: Op[]) => void;
}) {
  const top = [...layers].sort((a, b) => b.order - a.order);
  return (
    <div className="panel" role="listbox" aria-label="Layers">
      <div className="panel-head">
        <h2>Layers</h2>
        <span className="subtle" style={{ fontSize: 12 }}>
          Top first
        </span>
      </div>
      {top.map((layer) => {
        const Icon = ICONS[layer.kind] ?? Square;
        return (
          <div
            key={layer.id}
            className="layer-row"
            role="option"
            aria-selected={selected === layer.id}
            data-hidden={!layer.visible}
            tabIndex={-1}
            onClick={() => onSelect(layer.id)}
          >
            <Icon size={16} aria-hidden />
            <span className="label">
              <span>{layer.kind === "text" && layer.text ? layer.text : named(layer.name)}</span>
              <span>{layer.kind === "text" ? named(layer.name) : KIND[layer.kind]}</span>
            </span>
            <span className="tools">
              {layer.locked ? (
                <span className="btn btn-sm btn-icon" aria-label="Locked" title="Locked in this design">
                  <Lock size={14} />
                </span>
              ) : (
                <IconButton
                  label={layer.visible ? "Hide" : "Show"}
                  size="sm"
                  side="right"
                  onClick={(event) => {
                    event.stopPropagation();
                    apply([{ op: "set_visible", layer: layer.id, visible: !layer.visible }]);
                  }}
                >
                  {layer.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                </IconButton>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function BrandPanel({ view }: { view: CanvasView }) {
  return (
    <div className="panel">
      <h2>Brand colours</h2>
      <div style={{ display: "grid", gap: 8 }}>
        {distinctColours(view).map((colour) => (
          <div key={colour.token} style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span
              style={{
                width: 28,
                height: 28,
                borderRadius: 8,
                background: colour.hex,
                border: "1px solid var(--line-strong)",
                flex: "none",
              }}
            />
            <span style={{ display: "grid", lineHeight: 1.25, minWidth: 0 }}>
              <span style={{ textTransform: "capitalize" }}>{colour.name}</span>
              <span className="subtle" style={{ fontSize: 12 }}>
                {colour.used_by.length ? `Used by ${colour.used_by.join(", ")}` : colour.hex}
              </span>
            </span>
          </div>
        ))}
      </div>
      <h2 style={{ marginTop: 8 }}>Brand fonts</h2>
      <div style={{ display: "grid", gap: 6 }}>
        {view.fonts.map((font) => (
          <div key={font.token} style={{ fontFamily: font.family, fontSize: 18 }}>
            {font.family}
            <span className="subtle" style={{ fontFamily: "var(--font)", fontSize: 12, marginLeft: 8 }}>
              {font.token.replace("font.", "").replace("_", " ")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface Message {
  id: number;
  from: "me" | "them";
  text: string;
  bad?: boolean;
  detail?: string;
  choices?: ChangeResult["choices"];
}

function suggestions(layer: LayerInfo | undefined): string[] {
  if (!layer) return ["Bigger headline", "Darker photo", "Use another photo"];
  if (layer.kind === "photo") return ["Darker", "Use another photo", "Zoom in"];
  if (layer.kind === "text") return ["Bigger", "Smaller", "Centre it"];
  return ["Bigger", "Smaller"];
}

export function AskPanel({
  session,
  postId,
  layer,
  aspect,
  flags,
  onClose,
}: {
  session: EditorSession;
  postId: string;
  layer: LayerInfo | undefined;
  aspect: Aspect;
  flags: Flag[];
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pointing, setPointing] = useState(true);
  const log = useRef<HTMLDivElement>(null);
  const counter = useRef(0);
  const queries = useQueryClient();

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => setPointing(true), [layer?.id]);

  async function send(message: string) {
    const said = message.trim();
    if (!said || busy) return;
    const pointed = layer && pointing ? { layer: layer.id, aspect } : undefined;
    setMessages((now) => [...now, { id: ++counter.current, from: "me", text: said }]);
    setText("");
    setBusy(true);
    try {
      const result = await session.ask(said, pointed);
      const applied = result.outcome === "applied";
      const answer =
        result.question ??
        (applied
          ? result.note || "Done."
          : result.why || "That could not be done. Try saying it another way.");
      setMessages((now) => [
        ...now,
        {
          id: ++counter.current,
          from: "them",
          text: answer,
          bad: result.outcome === "failed",
          detail: applied
            ? [result.unable, result.seconds ? `${result.seconds.toFixed(1)} s` : ""]
                .filter(Boolean)
                .join(" · ")
            : undefined,
          choices: result.choices,
        },
      ]);
      if (applied) void queries.invalidateQueries({ queryKey: ["post", postId] });
    } catch (failure) {
      setMessages((now) => [
        ...now,
        { id: ++counter.current, from: "them", text: sentence(failure), bad: true },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(text);
  }

  return (
    <aside className="ask" aria-label="Ask Stratum">
      <div className="ask-head">
        <h2>Ask Stratum</h2>
        <IconButton label="Close" onClick={onClose} side="left">
          <PanelRightClose size={16} />
        </IconButton>
      </div>
      <div className="ask-log" ref={log} aria-live="polite">
        {flags.map((flag) => (
          <div key={flag.id} className="flag" data-severity={flag.severity}>
            {flag.severity === "blocker" ? <OctagonAlert size={16} /> : <AlertTriangle size={16} />}
            <span>{flag.message}</span>
          </div>
        ))}
        {messages.length === 0 ? (
          <p className="muted" style={{ fontSize: 13 }}>
            Say what to change, the way you would tell a designer: “make the headline bigger”, “use a photo of
            the van”. Select a part first to talk about just that part.
          </p>
        ) : null}
        {messages.map((message) => (
          <div key={message.id} className={`bubble ${message.from}${message.bad ? " bad" : ""}`}>
            {message.text}
            {message.detail ? <small>{message.detail}</small> : null}
            {message.choices?.length ? (
              <div className="choices">
                {message.choices
                  .filter((choice) => choice.kind === "tweak")
                  .map((choice) => (
                    <button
                      key={choice.label}
                      type="button"
                      className="chip"
                      onClick={() => void send(choice.label)}
                    >
                      {choice.label}
                    </button>
                  ))}
              </div>
            ) : null}
          </div>
        ))}
        {busy ? (
          <div className="bubble them" aria-label="Working">
            <span className="spinner" style={{ display: "inline-block", verticalAlign: "middle" }} />
          </div>
        ) : null}
      </div>
      <form className="ask-form" onSubmit={submit}>
        {layer && pointing ? (
          <span className="pointing">
            About: {named(layer.name)}
            <button type="button" aria-label="Talk about the whole poster" onClick={() => setPointing(false)}>
              <X size={12} />
            </button>
          </span>
        ) : null}
        <div className="row">
          {suggestions(pointing ? layer : undefined).map((one) => (
            <button key={one} type="button" className="chip" disabled={busy} onClick={() => void send(one)}>
              {one}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "end" }}>
          <textarea
            className="textarea"
            rows={2}
            value={text}
            placeholder="Make the headline bigger…"
            aria-label="Ask Stratum"
            maxLength={1000}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(text);
              }
            }}
          />
          <Button variant="primary" icon type="submit" aria-label="Send" busy={busy} disabled={!text.trim()}>
            <SendHorizontal size={16} />
          </Button>
        </div>
      </form>
    </aside>
  );
}
