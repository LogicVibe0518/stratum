// The editor puts nothing from the server into the page as markup, except the poster's
// own SVG, which goes through poster-svg.ts (DOMPurify, then the XML parser).

import { describe, expect, it } from "vitest";
import page from "../index.html?raw";

const ALL = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const FILES = Object.entries(ALL);

describe("the editor's page", () => {
  it("reads every source file", () => {
    expect(FILES.length).toBeGreaterThan(10);
    expect(ALL["./lib/poster-svg.ts"]).toBeDefined();
  });

  it("never writes server strings as HTML", () => {
    for (const [file, code] of FILES) {
      expect(code, file).not.toContain("dangerouslySetInnerHTML");
      expect(code, file).not.toMatch(/\.(inner|outer)HTML\s*=/);
      expect(code, file).not.toContain("insertAdjacentHTML");
      expect(code, file).not.toMatch(/\beval\(|new Function\(/);
    }
  });

  it("cleans the poster before it reaches the page", () => {
    expect(ALL["./lib/poster-svg.ts"]).toContain("DOMPurify.sanitize");
    expect(ALL["./lib/poster-svg.ts"]).toContain('"image/svg+xml"');
    expect(ALL["./editor/Canvas.tsx"]).toContain("prepare(view.svg");
  });

  it("never uses browser dialogs", () => {
    for (const [file, code] of FILES) expect(code, file).not.toMatch(/\b(alert|confirm|prompt)\(/);
  });

  it("loads nothing from other sites but the interface font", () => {
    const hosts = [...page.matchAll(/https:\/\/([a-z0-9.-]+)/g)].map((match) => match[1]);
    expect(new Set(hosts)).toEqual(new Set(["fonts.googleapis.com", "fonts.gstatic.com"]));
    for (const [file, code] of FILES) {
      // The SVG namespace is a name, not a place anything is fetched from.
      expect(code.replaceAll("http://www.w3.org/2000/svg", ""), file).not.toMatch(/https?:\/\//);
    }
  });
});
