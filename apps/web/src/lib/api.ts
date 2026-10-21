// Talking to the API: Supabase's own sign-in, then every request carries the person's
// token and row-level security decides what they see. The session lives under the same
// key as the old page's, so signing in on either signs in on both.

const SESSION_KEY = "stratum.session";

export interface Session {
  access_token: string;
  refresh_token: string;
  expires_at?: number;
  user?: { email?: string };
}

interface Config {
  url: string;
  key: string;
}

/** A refusal the server explained in a sentence ("The poster is on version 4 now..."). */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export class SignedOut extends Error {
  constructor() {
    super("Please sign in again.");
  }
}

let config: Promise<Config> | null = null;
let session: Session | null = readSession();
let refreshing: Promise<Session | null> | null = null;
const listeners = new Set<() => void>();

function readSession(): Session | null {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) ?? "null") as Session | null;
  } catch {
    return null; // a private window can refuse storage
  }
}

function keep(next: Session | null): void {
  session = next;
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(next));
  } catch {
    // signing in still works for this tab
  }
  for (const listener of listeners) listener();
}

export function currentSession(): Session | null {
  return session;
}

export function onSessionChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getConfig(): Promise<Config> {
  config ??= fetch("/api/config").then(async (r) => {
    if (!r.ok) throw new ApiError("The server is not answering. Try again in a minute.", r.status);
    return (await r.json()) as Config;
  });
  return config;
}

async function auth(path: string, body: unknown): Promise<Session> {
  const conf = await getConfig();
  const r = await fetch(`${conf.url}/auth/v1/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: conf.key },
    body: JSON.stringify(body),
  });
  const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok) {
    const said = data.msg ?? data.error_description ?? data.message;
    throw new ApiError(typeof said === "string" ? said : "Sign-in failed.", r.status);
  }
  return data as unknown as Session;
}

export async function signIn(email: string, password: string): Promise<void> {
  keep(await auth("token?grant_type=password", { email, password }));
}

export function signOut(): void {
  keep(null);
}

/** The session, refreshed a minute before it runs out (or now, when `force`). */
async function fresh(force = false): Promise<Session | null> {
  if (!session) return null;
  const soon = (session.expires_at ?? 0) * 1000 - Date.now() < 60_000;
  if (!force && !soon) return session;
  refreshing ??= (async () => {
    try {
      keep(await auth("token?grant_type=refresh_token", { refresh_token: session?.refresh_token }));
    } catch {
      keep(null);
    }
    refreshing = null;
    return session;
  })();
  return refreshing;
}

async function errorText(r: Response): Promise<string> {
  const text = await r.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const said = body.detail ?? body.message;
    if (typeof said === "string") return said;
    if (Array.isArray(said)) return "That change is outside what the editor allows.";
  } catch {
    // plain text: the server's own sentence
  }
  return text || r.statusText || "Something went wrong.";
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT";
  json?: unknown;
  form?: Record<string, string>;
  signal?: AbortSignal;
}

/** One authorised request. Retries once with a fresh token on 401. */
export async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  for (const retry of [false, true]) {
    const current = await fresh(retry);
    if (!current) throw new SignedOut();
    const headers: Record<string, string> = { Authorization: `Bearer ${current.access_token}` };
    let body: BodyInit | undefined;
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.json);
    } else if (options.form) {
      body = new URLSearchParams(options.form);
    }
    const r = await fetch(path, {
      method: options.method ?? (body ? "POST" : "GET"),
      headers,
      body,
      signal: options.signal,
    });
    if (r.status === 401 && !retry) continue;
    if (r.status === 401) {
      keep(null);
      throw new SignedOut();
    }
    if (!r.ok) throw new ApiError(await errorText(r), r.status);
    return r;
  }
  throw new SignedOut();
}

export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await (await request(path, { signal })).json()) as T;
}

export async function postJSON<T>(path: string, json: unknown, signal?: AbortSignal): Promise<T> {
  return (await (await request(path, { method: "POST", json, signal })).json()) as T;
}

export async function getBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  return (await request(path, { signal })).blob();
}

/** Save a file the server makes (a zip, a PowerPoint) under the name it gives. */
export async function download(path: string, fallback: string): Promise<void> {
  const r = await request(path);
  const named = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "");
  const url = URL.createObjectURL(await r.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = named?.[1] ?? fallback;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The sentence to show for anything that went wrong. */
export function sentence(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Try again.";
}
