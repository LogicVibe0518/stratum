// One post open for editing: top bar, side panels, the context toolbar, the canvas and
// Ask Stratum, laid out as Canva lays them out.

import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  CloudOff,
  Download,
  History,
  Image,
  Layers,
  LayoutTemplate,
  MessageSquare,
  Palette,
  Redo2,
  Share2,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { download, getJSON, postJSON, request, sentence } from "../lib/api";
import { mod, shortcut, typing } from "../lib/keys";
import { navigate } from "../lib/router";
import { EditorSession } from "../lib/session";
import { ASPECTS, type Aspect, type LayerInfo, type Op, type PostDetail } from "../lib/types";
import { Button, IconButton } from "../ui/Button";
import { editorApi } from "./api";
import { Canvas } from "./Canvas";
import { AskPanel, BrandPanel, LayersPanel } from "./Panels";
import { DesignsPanel, HistoryPanel, PhotosPanel } from "./RailPanels";
import { shownLayers } from "./shown";
import { Toolbar } from "./Toolbar";

const SIZE_LABEL: Record<Aspect, string> = {
  "4:5": "Post",
  "1:1": "Square",
  "9:16": "Story",
  "16:9": "Banner",
};
const STATUS_LABEL = {
  draft: "Draft",
  review: "In review",
  approved: "Approved",
  archived: "Archived",
} as const;
const SIZES_HINT = "stratum.hint.sizes";

export function Editor({ postId }: { postId: string }) {
  const detail = useQuery({
    queryKey: ["post", postId],
    queryFn: () => getJSON<PostDetail>(`/api/posts/${postId}`),
  });
  const aspects = useMemo(
    () => ASPECTS.filter((one) => detail.data?.post.aspects.includes(one)),
    [detail.data?.post.aspects],
  );
  const [session, setSession] = useState<EditorSession | null>(null);

  useEffect(() => {
    if (session || aspects.length === 0) return;
    const opened = new EditorSession(editorApi, postId, aspects[0] ?? "4:5");
    setSession(opened);
    void opened.load();
  }, [aspects, postId, session]);

  if (detail.error) {
    return (
      <main className="center-page">
        <div className="card">
          <h1>Could not open this post</h1>
          <p className="muted">{sentence(detail.error)}</p>
          <Button variant="primary" onClick={() => navigate("/")}>
            Back to posts
          </Button>
        </div>
      </main>
    );
  }
  if (!detail.data || !session) return <EditorSkeleton />;
  return <Workspace detail={detail.data} session={session} aspects={aspects} />;
}

function EditorSkeleton() {
  return (
    <div className="editor" aria-busy>
      <div className="topbar" />
      <div className="rail" />
      <div className="stage">
        <div className="context-bar" />
        <div className="viewport">
          <div className="paper skeleton" style={{ width: 360, height: 450, boxShadow: "none" }} />
        </div>
      </div>
      <div className="ask" />
    </div>
  );
}

type LeftTab = "designs" | "photos" | "layers" | "brand" | "history";

function Workspace({
  detail,
  session,
  aspects,
}: {
  detail: PostDetail;
  session: EditorSession;
  aspects: Aspect[];
}) {
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const queries = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // Canva opens on its design panel: here the three options, when there are three.
  const [left, setLeft] = useState<LeftTab | null>(detail.options.length ? "designs" : "layers");
  const [asking, setAsking] = useState(true);
  const { view, aspect } = snapshot;

  // What the server said that a person needs to know: a refusal, a reload.
  useEffect(
    () =>
      session.onNotice((notice) => {
        if (notice.kind === "error") toast.error(notice.message);
        else toast(notice.message);
      }),
    [session],
  );

  // Leaving while a change is still on its way would lose it: the browser asks first.
  useEffect(() => {
    if (!snapshot.saving) return;
    const stay = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", stay);
    return () => window.removeEventListener("beforeunload", stay);
  }, [snapshot.saving]);

  // A new version can change the checks' warnings and the thumbnails elsewhere.
  useEffect(() => {
    if (snapshot.version !== null) void queries.invalidateQueries({ queryKey: ["post", detail.post.id] });
  }, [snapshot.version, queries, detail.post.id]);

  const layers = useMemo(
    () => (view ? shownLayers(view.layers, snapshot.overrides, view.canvas, null) : []),
    [view, snapshot.overrides],
  );
  const chosen = layers.find((one) => one.id === selected);

  const apply = useCallback(
    (ops: Op[]) => {
      session.apply(ops);
      if (ops.some((op) => op.op === "place")) {
        let told = false;
        try {
          told = localStorage.getItem(SIZES_HINT) === "1";
          localStorage.setItem(SIZES_HINT, "1");
        } catch {
          told = true;
        }
        if (!told) {
          toast(`Moved in the ${SIZE_LABEL[session.getSnapshot().aspect].toLowerCase()} only`, {
            description: "Each size keeps its own layout. Words, colours and fonts change every size.",
          });
        }
      }
    },
    [session],
  );

  const preview = useCallback((ops: Op[]) => void session.preview(ops), [session]);

  const hide = useCallback(
    (layer: LayerInfo) => {
      if (layer.locked) return;
      apply([{ op: "set_visible", layer: layer.id, visible: false }]);
      setSelected(null);
      toast(`${layer.name.charAt(0).toUpperCase()}${layer.name.slice(1)} hidden`, {
        action: { label: "Undo", onClick: () => session.undo() },
      });
    },
    [apply, session],
  );

  // --- shortcuts -----------------------------------------------------------------------
  useEffect(() => {
    function key(event: KeyboardEvent) {
      // A menu, popover or field that already used the key keeps it (Radix marks Escape).
      if (event.defaultPrevented || typing(event.target)) return;
      const k = event.key.toLowerCase();
      if (mod(event) && k === "z") {
        event.preventDefault();
        if (event.shiftKey) session.redo();
        else session.undo();
        return;
      }
      if (mod(event) && k === "y") {
        event.preventDefault();
        session.redo();
        return;
      }
      if (event.key === "Escape") {
        setSelected(null);
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && chosen) {
        event.preventDefault();
        hide(chosen);
        return;
      }
      if (event.key === "Enter" && chosen?.kind === "text" && !chosen.locked) {
        event.preventDefault();
        setEditing(chosen.id);
        return;
      }
      if (event.key === "Tab" && layers.length) {
        // Through the parts from the top, as in every layout tool.
        event.preventDefault();
        const order = [...layers].filter((one) => one.visible).sort((a, b) => b.order - a.order);
        const at = order.findIndex((one) => one.id === selected);
        const next = order[(at + (event.shiftKey ? -1 : 1) + order.length) % order.length];
        if (next) setSelected(next.id);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [session, chosen, hide, layers, selected]);

  const changeAspect = useCallback(
    (next: Aspect) => {
      setEditing(null);
      session.setAspect(next);
    },
    [session],
  );

  return (
    <div className="editor">
      <TopBar
        detail={detail}
        aspects={aspects}
        aspect={aspect}
        onAspect={changeAspect}
        snapshot={snapshot}
        session={session}
      />
      <nav className="rail" aria-label="Panels">
        <div className="rail-tabs" role="tablist">
          {RAIL.map(({ tab, label, Icon }) => (
            <RailTab
              key={tab}
              icon={<Icon size={18} />}
              label={label}
              active={left === tab}
              onClick={() => setLeft(left === tab ? null : tab)}
            />
          ))}
        </div>
        {left === "designs" ? <DesignsPanel detail={detail} session={session} aspect={aspect} /> : null}
        {left === "photos" && view ? (
          <PhotosPanel clientId={detail.client.id} layers={layers} selected={selected} apply={apply} />
        ) : null}
        {left === "layers" && view ? (
          <LayersPanel layers={layers} selected={selected} onSelect={setSelected} apply={apply} />
        ) : null}
        {left === "brand" && view ? <BrandPanel view={view} /> : null}
        {left === "history" ? (
          <HistoryPanel detail={detail} session={session} aspect={aspect} current={snapshot.version} />
        ) : null}
      </nav>
      <section className="stage" aria-label="Poster">
        {view ? (
          <Toolbar
            layer={chosen}
            view={view}
            aspect={aspect}
            apply={apply}
            preview={preview}
            onEditText={() => chosen && setEditing(chosen.id)}
            onHide={() => chosen && hide(chosen)}
          />
        ) : (
          <div className="context-bar" />
        )}
        {view ? (
          <Canvas
            view={view}
            overrides={snapshot.overrides}
            aspect={aspect}
            selected={selected}
            onSelect={setSelected}
            editing={editing}
            onEditing={setEditing}
            apply={apply}
          />
        ) : (
          <div className="viewport">
            {snapshot.error ? (
              <div className="stage-message">
                <div style={{ display: "grid", gap: 12, justifyItems: "center" }}>
                  <p>{snapshot.error}</p>
                  <Button onClick={() => void session.load()}>Try again</Button>
                </div>
              </div>
            ) : (
              <div className="paper skeleton" style={{ width: 360, height: 450, boxShadow: "none" }} />
            )}
          </div>
        )}
      </section>
      {asking ? (
        <AskPanel
          session={session}
          postId={detail.post.id}
          clientId={detail.client.id}
          layer={chosen}
          aspect={aspect}
          flags={detail.flags}
          onClose={() => setAsking(false)}
        />
      ) : (
        <div className="collapsed-ask">
          <IconButton label="Ask Stratum" side="left" onClick={() => setAsking(true)}>
            <MessageSquare size={18} />
          </IconButton>
        </div>
      )}
    </div>
  );
}

const RAIL: { tab: LeftTab; label: string; Icon: typeof Layers }[] = [
  { tab: "designs", label: "Designs", Icon: LayoutTemplate },
  { tab: "photos", label: "Photos", Icon: Image },
  { tab: "layers", label: "Layers", Icon: Layers },
  { tab: "brand", label: "Brand", Icon: Palette },
  { tab: "history", label: "History", Icon: History },
];

function RailTab({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className="rail-tab" role="tab" aria-selected={active} onClick={onClick}>
      {icon}
      {label}
    </button>
  );
}

function TopBar({
  detail,
  aspects,
  aspect,
  onAspect,
  snapshot,
  session,
}: {
  detail: PostDetail;
  aspects: Aspect[];
  aspect: Aspect;
  onAspect: (aspect: Aspect) => void;
  snapshot: ReturnType<EditorSession["getSnapshot"]>;
  session: EditorSession;
}) {
  const queries = useQueryClient();
  const [status, setStatus] = useState(detail.post.status);
  const title = detail.post.slug.replace(/-/g, " ");
  useEffect(() => setStatus(detail.post.status), [detail.post.status]);

  async function changeStatus(next: typeof status) {
    const before = status;
    setStatus(next);
    try {
      await request(`/api/posts/${detail.post.id}/status`, { method: "POST", form: { status: next } });
      void queries.invalidateQueries({ queryKey: ["posts"] });
    } catch (failure) {
      setStatus(before);
      toast.error(sentence(failure));
    }
  }

  const [sharing, setSharing] = useState(false);

  // One link for this post (reviews.create_review): the client sees it frozen at this
  // version, comments on it, and approves or asks for changes. No account needed.
  async function share() {
    setSharing(true);
    try {
      const made = await postJSON<{ path: string; expires_at: string }>("/api/reviews", {
        client_id: detail.client.id,
        post_ids: [detail.post.id],
      });
      const link = `${window.location.origin}${made.path}`;
      let copied = false;
      try {
        await navigator.clipboard.writeText(link);
        copied = true;
      } catch {
        // a browser that refuses the clipboard still shows the link
      }
      setStatus("review");
      void queries.invalidateQueries({ queryKey: ["posts"] });
      const until = new Date(made.expires_at).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
      });
      toast(copied ? "Review link copied" : "Review link ready", {
        description: `${link} - open until ${until}.`,
        duration: 12000,
      });
    } catch (failure) {
      toast.error(sentence(failure));
    } finally {
      setSharing(false);
    }
  }

  async function save(path: string, name: string) {
    try {
      await download(path, name);
    } catch (failure) {
      toast.error(sentence(failure));
    }
  }

  return (
    <header className="topbar">
      <IconButton label="All posts" onClick={() => navigate("/")}>
        <ArrowLeft size={18} />
      </IconButton>
      <div className="name">
        <strong>{title.charAt(0).toUpperCase() + title.slice(1)}</strong>
        <span>{detail.client.name}</span>
      </div>
      <span className="sep" />
      <div className="sizes" role="tablist" aria-label="Sizes">
        {aspects.map((one) => {
          const [w, h] = one.split(":").map(Number) as [number, number];
          const scale = 14 / Math.max(w, h);
          return (
            <button
              key={one}
              type="button"
              role="tab"
              aria-selected={one === aspect}
              onClick={() => onAspect(one)}
              title={`${SIZE_LABEL[one]} ${one}`}
            >
              <span className="shape" style={{ width: w * scale, height: h * scale }} aria-hidden />
              {SIZE_LABEL[one]}
            </button>
          );
        })}
      </div>
      <span className="sep" />
      <IconButton
        label="Undo"
        shortcut={shortcut("Mod+Z")}
        disabled={!snapshot.canUndo}
        onClick={() => session.undo()}
      >
        <Undo2 size={18} />
      </IconButton>
      <IconButton
        label="Redo"
        shortcut={shortcut("Mod+Shift+Z")}
        disabled={!snapshot.canRedo}
        onClick={() => session.redo()}
      >
        <Redo2 size={18} />
      </IconButton>
      <span className="saved" role="status" aria-live="polite">
        {snapshot.saving ? (
          <>
            <span className="spinner" style={{ width: 12, height: 12 }} /> Saving…
          </>
        ) : snapshot.error && snapshot.view ? (
          <>
            <CloudOff size={14} /> Not connected
          </>
        ) : snapshot.savedAt ? (
          <>
            <Check size={14} /> All changes saved
          </>
        ) : null}
      </span>
      <span className="grow" />
      <Dropdown.Root>
        <Dropdown.Trigger asChild>
          <Button variant="ghost">
            <span className="status-dot" data-status={status} /> {STATUS_LABEL[status]}{" "}
            <ChevronDown size={14} />
          </Button>
        </Dropdown.Trigger>
        <Dropdown.Portal>
          <Dropdown.Content className="float" sideOffset={6} align="end">
            {(["draft", "review", "approved", "archived"] as const).map((one) => (
              <Dropdown.Item
                key={one}
                className="menu-item"
                onSelect={() => one !== status && void changeStatus(one)}
              >
                <span className="status-dot" data-status={one} />
                {STATUS_LABEL[one]}
                <span className="end">{one === status ? "✓" : ""}</span>
              </Dropdown.Item>
            ))}
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
      <Button variant="secondary" busy={sharing} disabled={snapshot.saving} onClick={() => void share()}>
        <Share2 size={16} /> Share for review
      </Button>
      <Dropdown.Root>
        <Dropdown.Trigger asChild>
          <Button variant="primary" disabled={snapshot.saving}>
            <Download size={16} /> Download
          </Button>
        </Dropdown.Trigger>
        <Dropdown.Portal>
          <Dropdown.Content className="float" sideOffset={6} align="end" style={{ minWidth: 260 }}>
            <Dropdown.Item
              className="menu-item"
              onSelect={() => void save(`/api/posts/${detail.post.id}/download`, `${detail.post.slug}.zip`)}
            >
              Every size, with the caption <span className="end">.zip</span>
            </Dropdown.Item>
            <Dropdown.Item
              className="menu-item"
              onSelect={() =>
                void save(
                  `/api/posts/${detail.post.id}/canva.pptx?aspect=${aspect}`,
                  `${detail.post.slug}-${aspect.replace(":", "x")}.pptx`,
                )
              }
            >
              Open in Canva ({SIZE_LABEL[aspect].toLowerCase()}) <span className="end">.pptx</span>
            </Dropdown.Item>
          </Dropdown.Content>
        </Dropdown.Portal>
      </Dropdown.Root>
    </header>
  );
}
