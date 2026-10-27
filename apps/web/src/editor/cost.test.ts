import { describe, expect, it } from "vitest";
import { money } from "./Cost";

describe("what a post cost, in words", () => {
  it("shows fractions of a cent to four places and dollars to two", () => {
    expect(money(0)).toBe("$0");
    expect(money(0.000517)).toBe("$0.0005");
    expect(money(0.0021)).toBe("$0.0021");
    expect(money(0.42)).toBe("$0.42");
    expect(money(5)).toBe("$5.00");
  });
});
