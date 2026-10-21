# apps/web - the editor

The poster editor: Vite + React + TypeScript, built into static files that the API serves at
`/app` (the page at `/` stays until this one does everything it does). Spec:
`claude/editor-v2-spec.md` in the project; conventions follow Canva, which the agency's
designers use every day.

## Run it

```bash
make api        # the API on :8000 (another terminal)
make web-dev    # the editor on http://localhost:5173/app, reloading as you edit
```

`make web` builds `dist/` so `make api` serves the editor itself at http://localhost:8000/app.
Sign in with the same account as the old page (the session is shared).

## How it works

- The canvas is the poster as SVG, drawn by the same renderer as the finished PNG
  (`GET /api/posts/{id}/canvas`). Fonts come from `/fonts` (the renderer's own files), photos
  from `/api/versions/{v}/assets/{sha}` with the person's token, kept per SHA-256 for the page.
- `lib/poster-svg.ts` cleans the SVG (DOMPurify, parsed as XML so `feDropShadow` keeps its
  case) and wraps each layer so a drag can move it before the server has drawn it.
- `lib/session.ts` is one open post: changes show at once, are saved in order through
  `/edit`, and the server's picture replaces the guess within a moment (`/canvas/try`, which
  saves nothing). Undo and redo name the version to bring back (`/restore`). A 409 means the
  poster moved on elsewhere: the editor reloads and says so.
- `lib/geometry.ts` is the canvas maths (hit testing, snapping, resizing), all pure and tested.
- Moving and resizing change the size on screen only (the `place` op); words, colours, fonts
  and effects change every size.

## Checks

`npm run check` (or `make web-check`): Prettier, TypeScript, Vitest, then the production build.
CI runs it as the `web` job.

## Dependencies

Pinned exactly, MIT / ISC / Apache-2.0 / BSD at runtime (DOMPurify under its Apache-2.0
option). Build-only exceptions are listed in the PR that added them.
