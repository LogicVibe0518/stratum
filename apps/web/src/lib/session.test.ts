import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { type EditorApi, EditorSession } from "./session";
import type { Aspect, Box, CanvasView, ChangeResult, Op } from "./types";

/** A server in memory: versions are lists of ops; every call can be held and released. */
class FakeServer implements EditorApi {
  versions: Op[][] = [[]];
  calls: string[] = [];
  held: (() => void)[] = [];
  hold = false;
  refuse: ApiError | null = null;

  private async gate(): Promise<void> {
    if (!this.hold) return;
    await new Promise<void>((release) => this.held.push(release));
  }

  release(): void {
    const waiting = this.held;
    this.held = [];
    for (const release of waiting) release();
  }

  private view(aspect: Aspect, ops: Op[]): CanvasView {
    const version = this.versions.length;
    return {
      version,
      version_id: `v${version}`,
      aspect,
      canvas: { width: 1080, height: 1350 },
      layers: [],
      palette: [],
      fonts: [],
      svg: JSON.stringify(ops),
      faces: [],
    };
  }

  async canvas(_post: string, aspect: Aspect): Promise<CanvasView> {
    this.calls.push("canvas");
    await this.gate();
    return this.view(aspect, this.versions.at(-1) ?? []);
  }

  async tryOps(_post: string, body: { version: number; aspect: Aspect; ops: Op[] }): Promise<CanvasView> {
    this.calls.push(`try:${body.ops.length}`);
    await this.gate();
    if (body.version !== this.versions.length) throw new ApiError("moved on", 409);
    return this.view(body.aspect, [...(this.versions.at(-1) ?? []), ...body.ops]);
  }

  async edit(_post: string, body: { version: number; ops: Op[] }): Promise<ChangeResult> {
    this.calls.push(`edit:${body.ops.map((op) => op.op).join(",")}`);
    await this.gate();
    if (this.refuse) throw this.refuse;
    if (body.version !== this.versions.length) throw new ApiError("moved on", 409);
    this.versions.push([...(this.versions.at(-1) ?? []), ...body.ops]);
    return { outcome: "applied", version: this.versions.length, id: `v${this.versions.length}` };
  }

  async restore(_post: string, body: { version: number; to: number }): Promise<ChangeResult> {
    this.calls.push(`restore:${body.to}`);
    await this.gate();
    if (body.version !== this.versions.length) throw new ApiError("moved on", 409);
    this.versions.push(this.versions[body.to - 1] ?? []);
    return { outcome: "applied", version: this.versions.length, id: `v${this.versions.length}` };
  }

  async tweak(_post: string, body: { message: string }): Promise<ChangeResult> {
    this.calls.push(`tweak:${body.message}`);
    await this.gate();
    return { outcome: "needs_human", why: "Say which part." };
  }
}

const BOX: Box = { x: 0.1, y: 0.1, w: 0.5, h: 0.1, rotation: 0 };
const place = (x: number, aspect: Aspect = "4:5"): Op => ({
  op: "place",
  layer: "headline",
  aspect,
  box: { ...BOX, x },
});
const flip: Op = { op: "flip", layer: "bg" };

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
  await new Promise((done) => setTimeout(done, 0));
}

async function opened(server = new FakeServer()): Promise<{ server: FakeServer; session: EditorSession }> {
  const session = new EditorSession(server, "post", "4:5");
  await session.load();
  return { server, session };
}

describe("an editing session", () => {
  it("shows a change at once, saves it, and then shows the server's picture", async () => {
    const { server, session } = await opened();
    server.hold = true;
    session.apply([place(0.3)]);
    expect(session.getSnapshot().overrides.get("headline")?.box?.x).toBe(0.3);
    expect(session.getSnapshot().saving).toBe(true);
    server.release();
    await settle();
    server.release();
    await settle();
    server.release();
    await settle();
    const after = session.getSnapshot();
    expect(after.version).toBe(2);
    expect(after.saving).toBe(false);
    expect(after.overrides.size).toBe(0);
    expect(after.savedAt).not.toBeNull();
  });

  it("saves changes one at a time, merging those still waiting", async () => {
    const { server, session } = await opened();
    server.hold = true;
    session.apply([place(0.2)]);
    session.apply([place(0.3)]);
    session.apply([flip]);
    await settle();
    // The first is on its way; the second and third wait as one change.
    expect(server.calls.filter((call) => call.startsWith("edit"))).toEqual(["edit:place"]);
    for (let i = 0; i < 6; i += 1) {
      server.release();
      await settle();
    }
    expect(server.calls.filter((call) => call.startsWith("edit"))).toEqual(["edit:place", "edit:place,flip"]);
    expect(server.versions).toHaveLength(3);
  });

  it("previews only what the server has not saved: a flip is never sent twice", async () => {
    const { server, session } = await opened();
    session.apply([flip]);
    await settle();
    await settle();
    const tries = server.calls.filter((call) => call.startsWith("try"));
    expect(tries.every((call) => call === "try:1" || call === "try:0")).toBe(true);
    expect(server.versions.at(-1)).toEqual([flip]);
  });

  it("undoes a change not sent yet without asking the server", async () => {
    const { server, session } = await opened();
    server.hold = true;
    session.apply([place(0.2)]);
    session.apply([place(0.4)]);
    session.undo();
    expect(session.getSnapshot().overrides.get("headline")?.box?.x).toBe(0.2);
    for (let i = 0; i < 6; i += 1) {
      server.release();
      await settle();
    }
    expect(server.calls.filter((call) => call.startsWith("edit"))).toEqual(["edit:place"]);
  });

  it("undoes and redoes by naming the version to bring back", async () => {
    const { server, session } = await opened();
    session.apply([place(0.2)]);
    await settle();
    await settle();
    session.apply([place(0.3)]);
    await settle();
    await settle();
    expect(session.getSnapshot().version).toBe(3);
    session.undo();
    await settle();
    await settle();
    expect(server.calls).toContain("restore:2");
    expect(server.versions.at(-1)).toEqual([place(0.2)]);
    session.undo();
    await settle();
    await settle();
    expect(server.calls).toContain("restore:1");
    expect(session.getSnapshot().canUndo).toBe(false);
    session.redo();
    await settle();
    await settle();
    expect(server.versions.at(-1)).toEqual([place(0.2)]);
    expect(session.getSnapshot().canRedo).toBe(true);
    // A new change after undo drops what could be redone.
    session.apply([flip]);
    expect(session.getSnapshot().canRedo).toBe(false);
  });

  it("reloads when the poster moved on elsewhere, and says so", async () => {
    const { server, session } = await opened();
    const notices: string[] = [];
    session.onNotice((notice) => notices.push(notice.message));
    server.versions.push([flip]); // someone else's change
    session.apply([place(0.5)]);
    await settle();
    await settle();
    await settle();
    expect(notices.some((said) => said.includes("changed somewhere else"))).toBe(true);
    expect(session.getSnapshot().overrides.size).toBe(0);
    expect(session.getSnapshot().version).toBe(2);
  });

  it("puts a refused change back and says why", async () => {
    const { server, session } = await opened();
    const notices: string[] = [];
    session.onNotice((notice) => notices.push(notice.message));
    server.refuse = new ApiError("That colour isn't one of the brand's.", 422);
    session.apply([place(0.5)]);
    await settle();
    await settle();
    expect(notices).toContain("That colour isn't one of the brand's.");
    expect(session.getSnapshot().overrides.size).toBe(0);
    expect(session.getSnapshot().version).toBe(1);
  });

  it("keeps each size's own moves to that size", async () => {
    const { server, session } = await opened();
    server.hold = true;
    session.apply([place(0.7, "9:16")]);
    expect(session.getSnapshot().overrides.size).toBe(0);
  });

  it("answers the chat in order with everything else", async () => {
    const { server, session } = await opened();
    session.apply([place(0.2)]);
    const answer = await session.ask("make it pop");
    expect(answer.why).toBe("Say which part.");
    const order = server.calls.filter((call) => call.startsWith("edit") || call.startsWith("tweak"));
    expect(order).toEqual(["edit:place", "tweak:make it pop"]);
  });

  it("runs another endpoint's change in order, and undoes it like an edit", async () => {
    const { server, session } = await opened();
    session.apply([place(0.2)]);
    // An option picked: the server writes version 3 from the version it was made on.
    const picked = await session.call(async (version) => {
      server.calls.push(`pick@${version}`);
      server.versions.push([flip]);
      return { outcome: "applied", version: server.versions.length, id: `v${server.versions.length}` };
    });
    await settle();
    expect(picked.version).toBe(3);
    expect(server.calls.filter((call) => !call.startsWith("try") && call !== "canvas")).toEqual([
      "edit:place",
      "pick@2",
    ]);
    expect(session.getSnapshot().version).toBe(3);
    session.undo();
    await settle();
    await settle();
    expect(server.calls).toContain("restore:2");
  });

  it("does not count picking what is already shown as a step", async () => {
    const { session } = await opened();
    const same = await session.call(async (version) => ({ outcome: "applied", version, id: `v${version}` }));
    await settle();
    expect(same.version).toBe(1);
    expect(session.getSnapshot().canUndo).toBe(false);
  });
});
