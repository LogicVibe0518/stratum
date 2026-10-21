// The editor's calls to the API (apps/api/src/stratum_api/main.py).

import { getJSON, postJSON } from "../lib/api";
import type { EditorApi } from "../lib/session";
import type { Aspect, CanvasView, ChangeResult } from "../lib/types";

const shape = (aspect: Aspect) => aspect.replace(":", "x");

export const editorApi: EditorApi = {
  canvas: (post, aspect, signal) =>
    getJSON<CanvasView>(`/api/posts/${post}/canvas?aspect=${shape(aspect)}`, signal),
  tryOps: (post, body, signal) => postJSON<CanvasView>(`/api/posts/${post}/canvas/try`, body, signal),
  edit: (post, body) => postJSON<ChangeResult>(`/api/posts/${post}/edit`, body),
  restore: (post, body) => postJSON<ChangeResult>(`/api/posts/${post}/restore`, body),
  tweak: (post, body) => postJSON<ChangeResult>(`/api/posts/${post}/tweak`, body),
};
