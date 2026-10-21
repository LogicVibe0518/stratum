// Shortcut labels as each platform writes them: ⌘Z on a Mac, Ctrl+Z elsewhere.

export const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function shortcut(keys: string): string {
  if (MAC) return keys.replace("Mod+", "⌘").replace("Shift+", "⇧").replace("Alt+", "⌥");
  return keys.replace("Mod+", "Ctrl+");
}

/** Cmd on a Mac, Ctrl elsewhere. */
export function mod(event: KeyboardEvent | React.KeyboardEvent): boolean {
  return MAC ? event.metaKey : event.ctrlKey;
}

/** Typing in a field: the editor's single-key shortcuts stay out of the way. */
export function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}
