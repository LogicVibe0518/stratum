// The rest of the side panel, as Canva has it: Designs (the three options), Photos (the
// client's own, click or drag one onto the poster) and History (every version, any of
// them brought back). Each change goes through the session, so undo covers it too.

import { useQueryClient } from "@tanstack/react-query";
import { Check, ImageOff, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { postJSON, sentence } from "../lib/api";
import { PHOTO_DRAG, previewURL } from "../lib/assets";
import type { EditorSession } from "../lib/session";
import type {
  Aspect,
  ChangeResult,
  DesignOption,
  LayerInfo,
  Op,
  PostDetail,
  SavedVersion,
} from "../lib/types";
import { IconButton } from "../ui/Button";
import { named } from "./Panels";
import { photoTarget, usePhotos } from "./photos";

const shape = (aspect: Aspect) => aspect.replace(":", "x");
const ratio = (aspect: Aspect) => aspect.replace(":", " / ");
// The newest versions with a picture; older ones are listed in words.
const PICTURED = 12;

/** A picture drawn by the server, fetched with the person's token once it is on screen. */
function Preview({ path, alt, aspect }: { path: string; alt: string; aspect: Aspect }) {
  const box = useRef<HTMLSpanElement>(null);
  const [seen, setSeen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = box.current;
    if (!element || seen) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const watch = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, [seen]);

  useEffect(() => {
    if (!seen) return;
    let live = true;
    setUrl(null);
    setFailed(false);
    previewURL(path).then(
      (found) => live && setUrl(found),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [path, seen]);

  return (
    <span ref={box} className="thumb" style={{ aspectRatio: ratio(aspect) }}>
      {url ? (
        <img src={url} alt={alt} draggable={false} />
      ) : failed ? (
        <ImageOff size={16} aria-label="No picture" />
      ) : (
        <span className="skeleton" />
      )}
    </span>
  );
}

// --- Designs ------------------------------------------------------------------------------

export function DesignsPanel({
  detail,
  session,
  aspect,
}: {
  detail: PostDetail;
  session: EditorSession;
  aspect: Aspect;
}) {
  const queries = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const current = detail.post.template_id;

  async function pick(option: DesignOption) {
    if (busy || option.design_id === current) return;
    setBusy(option.id);
    try {
      const result = await session.call(async () => {
        const saved = await postJSON<SavedVersion>(
          `/api/posts/${detail.post.id}/options/${option.id}/pick`,
          {},
        );
        return { outcome: "applied", version: saved.version, id: saved.id, note: saved.note ?? undefined };
      });
      const kept = result.note?.split(", kept ")[1];
      toast(`Option ${option.name}: ${option.title}`, {
        description: kept ? `Kept ${kept}.` : undefined,
        action: { label: "Undo", onClick: () => session.undo() },
      });
      void queries.invalidateQueries({ queryKey: ["post", detail.post.id] });
    } catch (failure) {
      toast.error(sentence(failure));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel" aria-label="Designs">
      <h2>Designs</h2>
      {detail.options.length === 0 ? (
        <p className="muted small">
          This post was made from one chosen design. Posts made with “Best three” show their three options
          here.
        </p>
      ) : (
        <>
          <p className="muted small">
            Three different layouts of the same words and photos. Your changes come along.
          </p>
          <div className="option-list">
            {detail.options.map((option) => {
              const shown = option.design_id === current;
              return (
                <button
                  key={option.id}
                  type="button"
                  className="option-card"
                  aria-pressed={shown}
                  aria-label={`Option ${option.name}: ${option.title}${shown ? ", on the poster" : ""}`}
                  disabled={busy !== null}
                  onClick={() => void pick(option)}
                >
                  <Preview path={option.preview.replace("{shape}", shape(aspect))} alt="" aspect={aspect} />
                  <span className="caption">
                    <b>{option.name}</b> {option.title}
                    {shown ? (
                      <span className="badge">
                        <Check size={12} /> On the poster
                      </span>
                    ) : busy === option.id ? (
                      <span className="spinner" style={{ width: 12, height: 12 }} />
                    ) : null}
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// --- Photos -------------------------------------------------------------------------------

export function PhotosPanel({
  clientId,
  layers,
  selected,
  apply,
}: {
  clientId: string;
  layers: LayerInfo[];
  selected: string | null;
  apply: (ops: Op[]) => void;
}) {
  const photos = usePhotos(clientId);
  const target = photoTarget(layers, selected);
  const slots = layers.filter((one) => one.kind === "photo" && one.visible && !one.locked).length;
  const used = new Set(layers.map((one) => one.asset).filter(Boolean));
  const hint = target
    ? `Click a photo to put it in the ${target.name.replace(/_/g, " ")}, or drag it onto any photo on the poster.`
    : slots
      ? "Select a photo on the poster, then click one here - or drag one onto it."
      : "This design has no photo to replace.";

  return (
    <div className="panel" aria-label="Photos">
      <h2>Photos</h2>
      <p className="muted small">{hint}</p>
      {photos.isPending ? (
        <div className="photo-grid">
          {Array.from({ length: 6 }, (_, index) => (
            <span key={index} className="thumb skeleton" style={{ aspectRatio: "1" }} />
          ))}
        </div>
      ) : photos.error ? (
        <p className="muted small">{sentence(photos.error)}</p>
      ) : photos.data?.length ? (
        <div className="photo-grid">
          {photos.data.map((photo) => {
            const on = used.has(photo.sha256);
            return (
              <button
                key={photo.sha256}
                type="button"
                className="photo-tile"
                draggable
                aria-label={`${photo.photo_type ? named(photo.photo_type) : "Photo"}${on ? ", on the poster" : ""}`}
                title={photo.photo_type ? named(photo.photo_type) : undefined}
                disabled={!target && !slots}
                onDragStart={(event) => {
                  event.dataTransfer.setData(PHOTO_DRAG, photo.sha256);
                  event.dataTransfer.effectAllowed = "copy";
                }}
                onClick={() => {
                  if (!target || target.asset === photo.sha256) return;
                  apply([{ op: "swap_asset", layer: target.id, asset: photo.sha256 }]);
                }}
              >
                <img src={photo.url} alt="" loading="lazy" draggable={false} />
                {on ? (
                  <span className="badge on">
                    <Check size={12} /> On the poster
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="muted small">
          No photos yet. <a href="/">Upload them on the client’s page</a>.
        </p>
      )}
    </div>
  );
}

// --- History ------------------------------------------------------------------------------

export function ago(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function HistoryPanel({
  detail,
  session,
  aspect,
  current,
}: {
  detail: PostDetail;
  session: EditorSession;
  aspect: Aspect;
  current: number | null;
}) {
  const queries = useQueryClient();
  const [busy, setBusy] = useState<number | null>(null);

  async function bringBack(to: number) {
    if (busy !== null) return;
    setBusy(to);
    try {
      const result = await session.call((version) =>
        postJSON<ChangeResult>(`/api/posts/${detail.post.id}/restore`, { version, to }),
      );
      if (result.outcome === "applied") {
        toast(`Back to version ${to}`, { action: { label: "Undo", onClick: () => session.undo() } });
      } else {
        toast.error(result.why ?? "That version could not be brought back.");
      }
      void queries.invalidateQueries({ queryKey: ["post", detail.post.id] });
    } catch (failure) {
      toast.error(sentence(failure));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel" aria-label="History">
      <h2>History</h2>
      <p className="muted small">Every change is a new version; nothing is ever lost.</p>
      <ol className="history-list">
        {detail.versions.map((one, index) => {
          const now = one.version === current;
          return (
            <li key={one.id} className="history-row" data-current={now}>
              {index < PICTURED ? (
                <Preview path={`/previews/${one.id}/${shape(aspect)}.webp`} alt="" aspect={aspect} />
              ) : null}
              <span className="label">
                <b>Version {one.version}</b>
                <span className="note">{one.note ? one.note.slice(0, 120) : "No note"}</span>
                <span className="subtle">{ago(one.created_at)}</span>
              </span>
              {now ? (
                <span className="badge">
                  <Check size={12} /> Now
                </span>
              ) : (
                <IconButton
                  label={`Bring back version ${one.version}`}
                  size="sm"
                  side="left"
                  busy={busy === one.version}
                  disabled={busy !== null}
                  onClick={() => void bringBack(one.version)}
                >
                  <RotateCcw size={14} />
                </IconButton>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
