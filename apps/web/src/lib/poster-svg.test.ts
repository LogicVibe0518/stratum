// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { attachImages, clean, prepare, scopeIds } from "./poster-svg";

// What GET /api/posts/{id}/canvas sent for a panel design with a shadow, a fade and a
// warmer photo (apps/api/tests/test_canvas_api.py makes the same kind of poster).
const SERVER_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1080" height="1350" viewBox="0 0 1080 1350"><g id="bg"><defs><linearGradient id="fade-bg-bottom-g" gradientUnits="userSpaceOnUse" x1="0.00" y1="548.10" x2="0.00" y2="783.00"><stop offset="0.000" stop-color="#ffffff" stop-opacity="1.0000"/><stop offset="0.125" stop-color="#ffffff" stop-opacity="0.9570"/><stop offset="0.250" stop-color="#ffffff" stop-opacity="0.8438"/><stop offset="0.375" stop-color="#ffffff" stop-opacity="0.6836"/><stop offset="0.500" stop-color="#ffffff" stop-opacity="0.5000"/><stop offset="0.625" stop-color="#ffffff" stop-opacity="0.3164"/><stop offset="0.750" stop-color="#ffffff" stop-opacity="0.1562"/><stop offset="0.875" stop-color="#ffffff" stop-opacity="0.0430"/><stop offset="1.000" stop-color="#ffffff" stop-opacity="0.0000"/></linearGradient><mask id="fade-bg-bottom" maskUnits="userSpaceOnUse" x="0.00" y="0.00" width="1080.00" height="783.00"><rect x="0.00" y="0.00" width="1080.00" height="783.00" fill="url(#fade-bg-bottom-g)"/></mask></defs><g mask="url(#fade-bg-bottom)"><defs><clipPath id="crop-bg"><rect x="0.00" y="0.00" width="1080.00" height="783.00"/></clipPath></defs><g clip-path="url(#crop-bg)"><defs><filter id="adjust-bg" filterUnits="userSpaceOnUse" x="0.00" y="0.00" width="1080.00" height="783.00" color-interpolation-filters="sRGB"><feColorMatrix type="saturate" values="1.0000"/><feComponentTransfer><feFuncR type="linear" slope="1.0300" intercept="0.0000"/><feFuncG type="linear" slope="1.0000" intercept="0.0000"/><feFuncB type="linear" slope="0.9700" intercept="0.0000"/></feComponentTransfer></filter></defs><g filter="url(#adjust-bg)"><image x="-81.00" y="-384.75" width="1242.00" height="1552.50" href="/api/versions/e3200189-cb72-46fc-8111-3fc888984c40/assets/c676220a0628478421550416273be33ff795d4f83b2f5bc0c9c39df8466284e7" preserveAspectRatio="none"/></g></g></g></g><g id="panel"><rect x="0.00" y="783.00" width="1080.00" height="567.00" rx="0.00" fill="#f2f4f7"/></g><g id="headline"><defs><filter id="shadow-headline" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="3.64" stdDeviation="3.64" flood-color="#ffffff" flood-opacity="0.600"/></filter></defs><text font-family="Anton" font-size="91.07" font-weight="400" fill="#12264a" text-anchor="start" filter="url(#shadow-headline)"><tspan x="79.92" y="1081.40">BOILER SERVICE, SORTED</tspan></text></g><g id="offer"><text font-family="Barlow" font-size="41.04" font-weight="400" fill="#12264a" text-anchor="start"><tspan x="79.92" y="1174.57">20% off every service booked this month</tspan></text></g><g id="cta"><text font-family="Barlow" font-size="33.48" font-weight="700" fill="#12264a" text-anchor="start"><tspan x="79.92" y="1259.54">Call 0161 496 0000</tspan></text></g><g id="logo"><rect x="110.13" y="862.92" width="209.10" height="88.28" rx="17.66" fill="#12264a" fill-opacity="0.9200"/><image x="124.85" y="877.63" width="179.67" height="58.85" href="/api/versions/e3200189-cb72-46fc-8111-3fc888984c40/assets/52d364038a9d59bf93e2a3da3c89687cc7e575828ce80d324b091604d8eebe66" preserveAspectRatio="xMidYMid meet"/></g></svg>';

const PHOTO = "c676220a0628478421550416273be33ff795d4f83b2f5bc0c9c39df8466284e7";
const LOGO = "52d364038a9d59bf93e2a3da3c89687cc7e575828ce80d324b091604d8eebe66";

describe("the poster on the canvas", () => {
  it("wraps every layer so the editor can move it", () => {
    const poster = prepare(SERVER_SVG, "p1-");
    expect([...poster.layers.keys()]).toEqual(["bg", "panel", "headline", "offer", "cta", "logo"]);
    const headline = poster.layers.get("headline");
    expect(headline?.firstElementChild?.getAttribute("id")).toBe("p1-headline");
  });

  it("keeps the masks, filters and fonts the renderer drew with", () => {
    const poster = prepare(SERVER_SVG, "p1-");
    const markup = poster.root.outerHTML;
    expect(markup).toContain('mask="url(#p1-fade-bg-bottom)"');
    expect(markup).toContain('filter="url(#p1-shadow-headline)"');
    // camelCase survives: an HTML parser would have written fedropshadow, which draws nothing.
    for (const name of ["feDropShadow", "clipPath", "linearGradient", "stdDeviation", "viewBox"]) {
      expect(markup).toContain(name);
    }
    expect(markup).toContain('font-family="Anton"');
    expect(poster.root.querySelector("#p1-fade-bg-bottom")).not.toBeNull();
  });

  it("gives each poster its own ids, so two never draw through each other", () => {
    const scoped = scopeIds('<g id="bg"><rect mask="url(#m)"/></g><mask id="m"/>', "p7-");
    expect(scoped).toBe('<g id="p7-bg"><rect mask="url(#p7-m)"/></g><mask id="p7-m"/>');
  });

  it("knows every picture by its SHA-256 and fetches nothing by itself", async () => {
    const poster = prepare(SERVER_SVG, "p1-");
    expect(poster.images.map((one) => one.sha)).toEqual([PHOTO, LOGO]);
    expect(poster.root.querySelector("image[href]")).toBeNull();
    await attachImages(poster, async (sha) => `blob:local/${sha.slice(0, 4)}`);
    expect(poster.images[0]?.element.getAttribute("href")).toBe("blob:local/c676");
  });

  it("drops anything that is not a drawing", () => {
    const hostile =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script>' +
      '<g id="x" onclick="alert(2)"><image href="https://evil.example/a.png"/></g>' +
      "<foreignObject><div>hi</div></foreignObject></svg>";
    const cleaned = clean(hostile);
    expect(cleaned).not.toContain("script");
    expect(cleaned).not.toContain("onclick");
    expect(cleaned).not.toContain("foreignObject");
    const poster = prepare(hostile, "p2-");
    expect(poster.images).toEqual([]);
    expect(poster.root.querySelector("image[href]")).toBeNull();
  });

  it("refuses something that is not an SVG at all", () => {
    expect(() => prepare("<div>no</div>", "p3-")).toThrow("could not be drawn");
  });
});
