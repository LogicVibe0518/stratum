import { describe, expect, it } from "vitest";
import { fileSlug, postName } from "./api";

describe("post names", () => {
  it("reads a slug as words, with a capital", () => {
    expect(postName("black-friday-deep-clean")).toBe("Black friday deep clean");
  });
  it("hides a demo visitor's suffix from people and file names", () => {
    expect(postName("black-friday-deep-clean--a1b2c3d4e5f6")).toBe("Black friday deep clean");
    expect(fileSlug("winter--a1b2c3d4e5f6")).toBe("winter");
  });
  it("leaves an ordinary slug alone", () => {
    expect(fileSlug("winter-2")).toBe("winter-2");
  });
});
