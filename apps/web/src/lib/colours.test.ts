import { describe, expect, it } from "vitest";
import { distinctColours } from "./colours";

const colour = (token: string, hex: string, used_by: string[] = []) => ({ token, hex, name: token, used_by });

describe("brand colours", () => {
  it("shows a colour the kit names twice once, with everything that uses it", () => {
    const shown = distinctColours({
      palette: [
        colour("palette.primary", "#12264A", ["panel"]),
        colour("palette.accent", "#ff6a00"),
        colour("palette.text", "#12264a", ["headline", "offer"]),
      ],
    });
    expect(shown.map((one) => one.token)).toEqual(["palette.primary", "palette.accent"]);
    expect(shown[0]?.used_by).toEqual(["panel", "headline", "offer"]);
  });
});
