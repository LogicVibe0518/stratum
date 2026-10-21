// One post open in the editor: what the canvas shows, the changes on their way to the
// server, and undo/redo.
//
// Every change shows at once and is saved in the background, one at a time, in order.
// The server keeps every version (nothing is overwritten), so undo and redo name the
// version to bring back. Each change is checked against the version it was made on: if
// the poster moved on elsewhere, nothing is changed and the editor reloads (409).

import { ApiError, sentence } from "./api";
import type { Aspect, Box, CanvasView, ChangeResult, Op } from "./types";

/** The most ops one saved change carries (the API's limit). */
export const MAX_OPS = 12;

export interface Pointed {
  layer?: string;
  aspect?: Aspect;
}

export interface EditorApi {
  canvas(post: string, aspect: Aspect, signal?: AbortSignal): Promise<CanvasView>;
  tryOps(
    post: string,
    body: { version: number; aspect: Aspect; ops: Op[] },
    signal?: AbortSignal,
  ): Promise<CanvasView>;
  edit(post: string, body: { version: number; ops: Op[] }): Promise<ChangeResult>;
  restore(post: string, body: { version: number; to: number }): Promise<ChangeResult>;
  tweak(post: string, body: { message: string; pointed?: Pointed }): Promise<ChangeResult>;
}

type Job =
  | { kind: "edit"; seq: number; seqs: number[]; ops: Op[] }
  | { kind: "restore"; seq: number; step: -1 | 1 }
  | {
      kind: "tweak";
      seq: number;
      message: string;
      pointed?: Pointed;
      done: (result: ChangeResult) => void;
      fail: (error: unknown) => void;
    };

/** What a layer looks like on screen before the server has drawn it. */
export interface Override {
  box?: Box;
  hidden?: boolean;
}

export interface Snapshot {
  aspect: Aspect;
  view: CanvasView | null;
  /** The newest version saved on the server. */
  version: number | null;
  loading: boolean;
  /** A sentence when the poster could not be shown at all. */
  error: string | null;
  saving: boolean;
  /** When the last change was saved (ms since epoch), for "All changes saved". */
  savedAt: number | null;
  canUndo: boolean;
  canRedo: boolean;
  overrides: ReadonlyMap<string, Override>;
}

export interface Notice {
  kind: "error" | "info";
  message: string;
}

export class EditorSession {
  private readonly api: EditorApi;
  readonly post: string;
  private aspect: Aspect;
  private view: CanvasView | null = null;
  private viewSeq = 0;
  private base: { version: number; id: string } | null = null;
  private states: number[] = [];
  private pos = 0;
  private queue: Job[] = [];
  private running = false;
  private seq = 0;
  /** Every change up to this one has been saved, or refused. */
  private committed = 0;
  /** Edits not yet in the picture on screen, oldest first: what `overrides` is made of. */
  private log: { seq: number; ops: Op[] }[] = [];
  private loading = true;
  private error: string | null = null;
  private savedAt: number | null = null;
  private tries = 0;
  private epoch = 0;
  private loads: AbortController | null = null;
  private snapshot: Snapshot;
  private readonly listeners = new Set<() => void>();
  private readonly notices = new Set<(notice: Notice) => void>();

  constructor(api: EditorApi, post: string, aspect: Aspect) {
    this.api = api;
    this.post = post;
    this.aspect = aspect;
    this.snapshot = this.make();
  }

  // --- reading -------------------------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): Snapshot => this.snapshot;

  onNotice(listener: (notice: Notice) => void): () => void {
    this.notices.add(listener);
    return () => this.notices.delete(listener);
  }

  private notice(kind: Notice["kind"], message: string): void {
    for (const listener of this.notices) listener({ kind, message });
  }

  private changed(): void {
    this.snapshot = this.make();
    for (const listener of this.listeners) listener();
  }

  /** Where undo and redo would land once everything queued has run. */
  private future(): { pos: number; length: number } {
    let pos = this.pos;
    let length = this.states.length;
    for (const job of this.queue) {
      if (job.kind === "restore") pos += job.step;
      else {
        pos += 1;
        length = pos + 1;
      }
    }
    return { pos, length };
  }

  private make(): Snapshot {
    const { pos, length } = this.future();
    return {
      aspect: this.aspect,
      view: this.view,
      version: this.base?.version ?? null,
      loading: this.loading,
      error: this.error,
      saving: this.running || this.queue.length > 0,
      savedAt: this.savedAt,
      canUndo: pos > 0,
      canRedo: pos < length - 1,
      overrides: this.overridesFor(this.aspect),
    };
  }

  private overridesFor(aspect: Aspect): Map<string, Override> {
    const out = new Map<string, Override>();
    for (const entry of this.log) {
      for (const op of entry.ops) {
        if (op.op === "place" && op.aspect === aspect) {
          out.set(op.layer, { ...out.get(op.layer), box: op.box });
        } else if (op.op === "set_visible") {
          out.set(op.layer, { ...out.get(op.layer), hidden: !op.visible });
        }
      }
    }
    return out;
  }

  // --- loading -------------------------------------------------------------------------

  /** Show the newest version of this size. */
  async load(): Promise<void> {
    this.loads?.abort();
    const loads = new AbortController();
    this.loads = loads;
    const covered = this.committed;
    const epoch = this.epoch;
    this.loading = this.view === null || this.view.aspect !== this.aspect;
    this.changed();
    try {
      const view = await this.api.canvas(this.post, this.aspect, loads.signal);
      if (loads.signal.aborted || epoch !== this.epoch) return;
      this.show(view, covered);
      if (this.base === null || view.version >= this.base.version) {
        if (this.base === null) {
          this.states = [view.version];
          this.pos = 0;
        }
        this.base = { version: view.version, id: view.version_id };
      }
      this.error = null;
    } catch (error) {
      if (loads.signal.aborted) return;
      this.error = sentence(error);
    } finally {
      if (this.loads === loads) {
        this.loads = null;
        this.loading = false;
        this.changed();
      }
    }
  }

  setAspect(aspect: Aspect): void {
    if (aspect === this.aspect) return;
    this.aspect = aspect;
    this.changed();
    void this.load().then(() => this.preview());
  }

  private show(view: CanvasView, covered: number): void {
    if (view.aspect !== this.aspect) return;
    this.view = view;
    this.viewSeq = Math.max(this.viewSeq, covered);
    this.log = this.log.filter((entry) => entry.seq > this.viewSeq);
  }

  // --- changing ------------------------------------------------------------------------

  /** Make a change: shown now, saved in the background. */
  apply(ops: Op[]): void {
    if (ops.length === 0) return;
    const seq = ++this.seq;
    this.log.push({ seq, ops });
    const last = this.queue.at(-1);
    const waiting = last && !(this.running && this.queue.length === 1);
    if (last?.kind === "edit" && waiting && last.ops.length + ops.length <= MAX_OPS) {
      // Not sent yet: one saved change instead of two. A part placed twice in the
      // same size keeps only where it ended up.
      const merged = [...last.ops];
      for (const op of ops) {
        const same = merged.findIndex(
          (one) =>
            one.op === "place" && op.op === "place" && one.layer === op.layer && one.aspect === op.aspect,
        );
        if (same >= 0) merged.splice(same, 1);
        merged.push(op);
      }
      last.ops = merged;
      last.seq = seq;
      last.seqs.push(seq);
    } else {
      this.queue.push({ kind: "edit", seq, seqs: [seq], ops });
    }
    this.changed();
    void this.preview();
    void this.pump();
  }

  /** Ask Stratum: the chat, saved in order with everything else. */
  ask(message: string, pointed?: Pointed): Promise<ChangeResult> {
    return new Promise((done, fail) => {
      this.queue.push({ kind: "tweak", seq: ++this.seq, message, pointed, done, fail });
      this.changed();
      void this.pump();
    });
  }

  undo(): void {
    const last = this.queue.at(-1);
    const waiting = last && !(this.running && this.queue.length === 1);
    if (last?.kind === "edit" && waiting) {
      // Not sent yet: simply never send it.
      this.queue.pop();
      this.dropped(last);
      this.changed();
      void this.preview();
      return;
    }
    if (!this.snapshot.canUndo) return;
    this.queue.push({ kind: "restore", seq: ++this.seq, step: -1 });
    this.changed();
    void this.pump();
  }

  redo(): void {
    if (!this.snapshot.canRedo) return;
    this.queue.push({ kind: "restore", seq: ++this.seq, step: 1 });
    this.changed();
    void this.pump();
  }

  /**
   * The picture for everything not yet saved, drawn by the server without saving it, so
   * words re-flow and photos re-frame within a moment of letting go. `extra` is a change
   * still being made (a slider being dragged, words being typed): shown, never saved.
   */
  async preview(extra: Op[] = []): Promise<void> {
    if (!this.base || this.queue.some((job) => job.kind !== "edit")) return;
    // Only what the server has not saved yet: a flip sent twice is no flip at all.
    const waiting = this.queue.flatMap((job) => (job.kind === "edit" ? job.ops : []));
    const ops = [...waiting, ...extra];
    if (ops.length === 0 || ops.length > MAX_OPS) return;
    const asked = ++this.tries;
    const epoch = this.epoch;
    const covered = extra.length ? this.viewSeq : this.seq;
    try {
      const view = await this.api.tryOps(this.post, {
        version: this.base.version,
        aspect: this.aspect,
        ops,
      });
      if (asked !== this.tries || epoch !== this.epoch) return; // a newer picture is coming
      this.show(view, covered);
      this.changed();
    } catch {
      // The save that follows says what was wrong, if anything was.
    }
  }

  private async pump(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.changed();
    while (this.queue.length > 0) {
      const job = this.queue[0];
      if (!job || !this.base) break;
      try {
        await this.run(job, this.base.version);
      } catch (error) {
        this.failed(job, error);
        if (error instanceof ApiError && error.status === 409) {
          this.queue.shift();
          this.committed = this.seq;
          await this.reload();
          continue;
        }
      }
      this.queue.shift();
      this.committed = Math.max(this.committed, job.seq);
      this.changed();
    }
    this.running = false;
    this.changed();
    await this.load();
  }

  private async run(job: Job, version: number): Promise<void> {
    let result: ChangeResult;
    if (job.kind === "edit") {
      result = await this.api.edit(this.post, { version, ops: job.ops });
    } else if (job.kind === "tweak") {
      result = await this.api.tweak(this.post, { message: job.message, pointed: job.pointed });
    } else {
      const to = this.states[this.pos + job.step];
      if (to === undefined) return;
      result = await this.api.restore(this.post, { version, to });
    }
    if (result.outcome === "applied" && result.version !== undefined && result.id !== undefined) {
      this.base = { version: result.version, id: result.id };
      if (job.kind === "restore") {
        this.pos += job.step;
      } else {
        this.states = [...this.states.slice(0, this.pos + 1), result.version];
        this.pos = this.states.length - 1;
      }
      this.savedAt = Date.now();
    } else {
      this.dropped(job);
      if (job.kind === "edit") this.notice("error", result.why ?? "That change could not be made.");
    }
    if (job.kind === "tweak") job.done(result);
  }

  private dropped(job: Job): void {
    const gone = new Set(job.kind === "edit" ? job.seqs : [job.seq]);
    this.log = this.log.filter((entry) => !gone.has(entry.seq));
  }

  private failed(job: Job, error: unknown): void {
    this.dropped(job);
    if (job.kind === "tweak") job.fail(error);
    if (error instanceof ApiError && error.status === 409) return;
    if (job.kind !== "tweak") this.notice("error", sentence(error));
  }

  /** The poster changed somewhere else: drop what was waiting and show the newest. */
  private async reload(): Promise<void> {
    this.epoch += 1;
    for (const job of this.queue) {
      this.dropped(job);
      if (job.kind === "tweak") job.fail(new Error("The poster changed somewhere else."));
    }
    this.queue = [];
    this.log = [];
    this.base = null;
    this.notice("info", "This poster was changed somewhere else, so the newest version is shown.");
    await this.load();
  }
}
