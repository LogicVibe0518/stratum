// Two screens and a base path: a router library would be more code than this.

import { useSyncExternalStore } from "react";

export const BASE = "/app";
/** The first page (clients, uploads, new posts) until the editor does all of it. */
export const CLASSIC = "/classic";

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

window.addEventListener("popstate", emit);

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function path(): string {
  const here = window.location.pathname;
  return here.startsWith(BASE) ? here.slice(BASE.length) || "/" : "/";
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, path);
}

export function navigate(to: string, replace = false): void {
  const url = `${BASE}${to}`;
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  emit();
}

export type Route = { screen: "posts" } | { screen: "editor"; post: string } | { screen: "missing" };

export function route(at: string): Route {
  if (at === "/" || at === "") return { screen: "posts" };
  const editor = /^\/posts\/([0-9a-f-]{36})\/?$/.exec(at);
  if (editor?.[1]) return { screen: "editor", post: editor[1] };
  return { screen: "missing" };
}
