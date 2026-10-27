// What the API sends and takes. Kept by hand next to apps/api/src/stratum_api/main.py and
// stratum_worker/edit/inspect.py; the API tests pin the server side of each shape.

export type Aspect = "4:5" | "1:1" | "9:16" | "16:9";
export const ASPECTS: readonly Aspect[] = ["4:5", "1:1", "9:16", "16:9"];

/** Where a layer sits, as fractions of one size of the poster. Origin top-left. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number;
}

/** A rectangle in the poster's own pixels (one size). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type LayerKind = "text" | "photo" | "logo" | "shape" | "cutout";

export interface Colour {
  token: string;
  hex: string;
  name: string;
}

export interface LayerInfo {
  id: string;
  name: string;
  kind: LayerKind;
  visible: boolean;
  locked: boolean;
  box: Box;
  px: Rect;
  opacity: number;
  order: number;
  // text
  text?: string;
  colour?: Colour;
  align?: "left" | "center" | "right";
  font?: string;
  uppercase?: boolean;
  effect?: { kind: "shadow" | "outline"; color: string | null; strength: number } | null;
  size_px?: number;
  lines?: string[];
  ink?: Rect;
  // shape
  shape?: string;
  // photo
  asset?: string | null;
  fit?: "cover" | "contain";
  dim?: number;
  flip?: boolean;
  crop?: { x: number; y: number; zoom: number };
}

export interface Face {
  family: string;
  weight: number;
  url: string;
}

export interface PaletteColour extends Colour {
  used_by: string[];
}

export interface CanvasView {
  version: number;
  version_id: string;
  aspect: Aspect;
  canvas: { width: number; height: number };
  layers: LayerInfo[];
  palette: PaletteColour[];
  fonts: { token: string; family: string }[];
  svg: string;
  faces: Face[];
}

export interface Client {
  id: string;
  name: string;
  slug: string;
}

export interface PostRow {
  id: string;
  slug: string;
  brief: string;
  template_id: string;
  status: "draft" | "review" | "approved" | "archived";
  cost_usd: number | null;
  created_at: string;
  client_id: string;
}

/** Something the checks found on the newest version (stratum.flags). */
export interface Flag {
  id: string;
  code: string;
  message: string;
  severity: "blocker" | "warning";
}

export interface PostDetail {
  post: PostRow & { aspects: Aspect[] };
  client: Client;
  versions: { id: string; version: number; note: string | null; created_at: string }[];
  latest: { id: string; version: number } | null;
  renders: { aspect: Aspect; url: string }[];
  flags: Flag[];
  edits: { message: string; outcome: string; latency_ms: number; created_at: string }[];
  /** The three options of a "best three" draft, in the order shown; empty otherwise. */
  options: DesignOption[];
  /** What this post has cost in model calls (main.post_cost). */
  cost?: PostCost;
}

export interface PostCost {
  draft_usd: number;
  changes_usd: number;
  total_usd: number;
  /** Plain-English changes a model read (applied or not). */
  paid_changes: number;
  /** Changes made by hand or with the instant buttons: no model, no cost. */
  free_changes: number;
  /** The agency's AI allowance per day. */
  daily_cap_usd: number;
}

export interface DesignOption {
  id: string;
  name: string;
  position: number;
  design_id: string;
  title: string;
  /** "/previews/options/<id>/{shape}.webp": {shape} is "4x5", "1x1", ... */
  preview: string;
}

/** One of the client's files (GET /api/clients/{id}/assets). `url` is signed for an hour. */
export interface Asset {
  id: string;
  sha256: string;
  kind: "photo" | "logo" | string;
  width: number | null;
  height: number | null;
  url: string;
  photo_type: string | null;
}

/** A version saved by POST /options/{id}/pick: the version row. */
export interface SavedVersion {
  id: string;
  version: number;
  note: string | null;
}

/** A version saved by /edit, /restore or /tweak, or a change that was not made. */
export interface ChangeResult {
  outcome: "applied" | "needs_human" | "failed";
  version?: number;
  id?: string;
  why?: string;
  note?: string;
  unable?: string;
  question?: string;
  choices?: { label: string; kind: "tweak" | "brand" }[];
  /** "Which photo?": the client's photos to pick from, and the photo part to put it in. */
  photos?: { sha256: string; probability: number | null }[];
  layer?: string | null;
  seconds?: number;
}

/** A change the editor sends: one of the server's edit ops (stratum_spec.ops). */
export type Op =
  | { op: "place"; layer: string; aspect: Aspect; box: Box; text_size?: number; max_lines?: number }
  | { op: "set_visible"; layer: string; visible: boolean }
  | { op: "reorder"; layer: string; to: "front" | "back" | "forward" | "backward" }
  | { op: "set_text"; layer: string; text: string }
  | { op: "set_color"; layer: string; color: string }
  | { op: "set_align"; layer: string; align: "left" | "center" | "right" }
  | { op: "set_font"; layer: string; font: string }
  | { op: "set_case"; layer: string; upper: boolean }
  | { op: "text_effect"; layer: string; kind: "shadow" | "outline" | "none" }
  | { op: "set_opacity"; layer: string; opacity: number }
  | { op: "dim"; layer: string; amount: number }
  | { op: "flip"; layer: string }
  | { op: "swap_asset"; layer: string; asset: string };
