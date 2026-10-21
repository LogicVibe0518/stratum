// Pictures and fonts the canvas draws with, fetched once per page.
//
// A picture is named by its SHA-256, so the bytes behind a name never change: after the
// first fetch every version of every size reuses the same local copy, and moving a part
// downloads nothing. Fonts are the renderer's own files, so words break where they do
// in the finished poster.

import { getBlob } from "./api";
import type { Face } from "./types";

const pictures = new Map<string, Promise<string>>();

/** A local URL for one picture, fetched (with the person's token) the first time. */
export function pictureURL(versionId: string, sha: string): Promise<string> {
  let found = pictures.get(sha);
  if (!found) {
    found = getBlob(`/api/versions/${versionId}/assets/${sha}`).then(async (blob) => {
      const url = URL.createObjectURL(blob);
      // Decoded before the poster is swapped in, so it never flashes empty.
      const image = new Image();
      image.src = url;
      await image.decode().catch(() => undefined);
      return url;
    });
    found.catch(() => pictures.delete(sha)); // a failed fetch is tried again next time
    pictures.set(sha, found);
  }
  return found;
}

const faces = new Map<string, Promise<void>>();

/** Load the brand's fonts into the page. Resolves when every one can be drawn. */
export function loadFaces(wanted: Face[]): Promise<void> {
  return Promise.all(
    wanted.map((face) => {
      const key = `${face.family}|${face.weight}|${face.url}`;
      let loading = faces.get(key);
      if (!loading) {
        const font = new FontFace(face.family, `url(${face.url})`, { weight: String(face.weight) });
        loading = font.load().then((ready) => {
          document.fonts.add(ready);
        });
        loading.catch(() => faces.delete(key));
        faces.set(key, loading);
      }
      return loading.catch(() => undefined); // a missing font still shows the poster
    }),
  ).then(() => undefined);
}
