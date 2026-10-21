// The poster as the canvas draws it: the server's own SVG (the same renderer as the
// finished PNG), cleaned, its ids made unique, each layer wrapped so the editor can move
// it, and its pictures fetched with the person's token.

import DOMPurify from "dompurify";

const SVG_NS = "http://www.w3.org/2000/svg";
const ASSET = /^\/api\/versions\/[0-9a-f-]+\/assets\/([0-9a-f]{64})$/;

export interface PreparedPoster {
  root: SVGSVGElement;
  /** Each layer's wrapper: its transform is the editor's, never the server's. */
  layers: Map<string, SVGGElement>;
  /** Every picture, by the SHA-256 the server named it with. */
  images: { element: SVGImageElement; sha: string }[];
}

/**
 * Ids inside the SVG (masks, clip paths, filters) are global to the page. Two posters
 * on screen at once - the old one fading out, the new one in - would draw through each
 * other's masks, so every id gets this poster's own prefix.
 */
export function scopeIds(markup: string, scope: string): string {
  return markup
    .replace(/\bid="([^"]+)"/g, (_, id: string) => `id="${scope}${id}"`)
    .replace(/url\(#([^)]+)\)/g, (_, id: string) => `url(#${scope}${id})`);
}

// The renderer writes SVG with camelCase names (feDropShadow, viewBox). DOMPurify parses
// as XML here, so nothing is lower-cased on the way through - an HTML parser turns
// feDropShadow into fedropshadow in some browsers and the shadow silently goes - and
// its lists are lower-case, so the renderer's camelCase vocabulary is named here.
const CAMEL_TAGS = [
  "clipPath",
  "linearGradient",
  "feDropShadow",
  "feGaussianBlur",
  "feColorMatrix",
  "feComponentTransfer",
  "feFuncR",
  "feFuncG",
  "feFuncB",
];
const CAMEL_ATTRS = [
  "viewBox",
  "preserveAspectRatio",
  "gradientUnits",
  "maskUnits",
  "filterUnits",
  "stdDeviation",
];

const POLICY = {
  PARSER_MEDIA_TYPE: "application/xhtml+xml" as const,
  USE_PROFILES: { svg: true, svgFilters: true },
  ADD_TAGS: CAMEL_TAGS,
  ADD_ATTR: [...CAMEL_ATTRS, "paint-order", "mask", "color-interpolation-filters"],
};

/** The SVG with anything that is not a drawing taken out: scripts, event handlers,
 * HTML inside it, links to other pages. */
export function clean(markup: string): string {
  return DOMPurify.sanitize(markup, POLICY);
}

export function prepare(markup: string, scope: string): PreparedPoster {
  const parsed = new DOMParser().parseFromString(clean(scopeIds(markup, scope)), "image/svg+xml");
  const found = parsed.documentElement;
  if (found.namespaceURI !== SVG_NS || found.nodeName !== "svg") {
    throw new Error("The poster could not be drawn here. Reload the page.");
  }
  const root = document.importNode(found, true) as unknown as SVGSVGElement;
  root.removeAttribute("width");
  root.removeAttribute("height");
  root.setAttribute("preserveAspectRatio", "none");
  root.setAttribute("aria-hidden", "true");
  root.style.display = "block";
  root.style.width = "100%";
  root.style.height = "100%";

  const layers = new Map<string, SVGGElement>();
  for (const child of Array.from(root.children)) {
    const id = child.getAttribute("id");
    if (child.nodeName !== "g" || !id?.startsWith(scope)) continue;
    const wrapper = document.createElementNS(SVG_NS, "g");
    const layer = id.slice(scope.length);
    wrapper.setAttribute("data-layer", layer);
    root.insertBefore(wrapper, child);
    wrapper.append(child);
    layers.set(layer, wrapper);
  }

  const images: PreparedPoster["images"] = [];
  for (const element of Array.from(root.querySelectorAll("image"))) {
    const href = element.getAttribute("href") ?? element.getAttribute("xlink:href") ?? "";
    const sha = ASSET.exec(href)?.[1];
    // Not one of ours: drawn as nothing rather than fetched from anywhere.
    element.removeAttribute("href");
    element.removeAttribute("xlink:href");
    if (sha) images.push({ element: element as SVGImageElement, sha });
  }
  return { root, layers, images };
}

/** Point every picture at its local copy, and wait until they are decoded. */
export async function attachImages(
  poster: PreparedPoster,
  urlFor: (sha: string) => Promise<string>,
): Promise<void> {
  await Promise.all(
    poster.images.map(async ({ element, sha }) => {
      const url = await urlFor(sha);
      element.setAttribute("href", url);
    }),
  );
}
