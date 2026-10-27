# Stratum

A poster engine for small social-media agencies. A client's brief, their real photos and their brand
kit go in; on-brand, fully editable posts come out in every size within minutes. Client changes
("make the logo bigger", "use the photo of the van") take seconds, in plain English or by hand on a
canvas that works like Canva.

> Status: in a pilot with a small digital agency. The repository is private until IP terms are
> settled; a live demo is available on request.

## Why it is built this way

Image models cannot be trusted with logos, brand colours, prices or text, so Stratum never asks them
to. **A poster is data, not pixels**: every poster is a layered JSON spec (background, photos, cut-out,
shapes, logo, words) that Stratum's own compositor renders. Models only ever do three narrow jobs, and
their output is checked before it touches a poster:

| Job | What the model returns | What checks it |
|---|---|---|
| Write the copy | headline, offer line, call to action, caption | the brief's figures and dates must appear; no invented claims |
| Read a change ("shorter headline") | a list of edit ops, never markup | an op allowlist, the design's slots, brand colours and fonts, and a fact guard: new words may not drop a price, %, date or phone number unasked |
| Paint a background (optional) | an image behind everything | logos and real product pixels always go on top, untouched |

## What it does

- **Drafts three different designs** from one brief: many layouts are filled with the same words and
  photos, a learned critic ranks them, and three different families are shown (with a little
  exploration, and every probability logged so the ranking can be learned from the designer's picks).
- **Understands the client's photos**: faces (so words never sit on one), the main subject and how
  busy each region is, to frame each photo for each size.
- **Edits like Canva** (React, at `/app`): drag, resize and type on the poster; Designs, Photos (click
  or drag to replace), Layers, Brand and History panels; undo/redo; every size has its own layout.
- **Plain-English changes** through the same validated ops as the canvas, with answers that ask back
  ("which photo?") instead of guessing.
- **Client review links**: one link, no account; the client comments on the poster and approves.
- **Exports**: every size with the caption as a zip, or a `.pptx` that opens in Canva as editable layers.

## Architecture

```
browser ── FastAPI (apps/api) ── reads ──► Postgres (Supabase, row-level security)
               │                                  ▲
               └─ slow commands ─► job queue ─────┤ lease-based, in Postgres
                                       │          │
                                 worker (apps/worker): compositor, photo models,
                                 AI gateway (budgets, fallback, circuit breaker)
```

- **One source of truth**: `packages/spec` holds the layer spec and edit ops (versioned schema with
  upgraders). Versions are immutable; an edit is a new version, so undo is "bring back version n".
- **Tenancy in the database, not the code**: every row has `agency_id` and RLS. The worker signs a
  short-lived token as the person who asked for each job, so even a bug cannot write across agencies.
  The public API never holds a key that can.
- **Every AI call through one gateway**: per-task models and token limits, per-agency daily budgets
  checked before a call, provider fallback, a circuit breaker, and traces in Langfuse. A plain-English
  edit costs about $0.0005; instant changes and canvas edits cost nothing.
- **Our own renderer**: SVG drawn with resvg, text shaped with HarfBuzz, so line breaks match exactly
  between the editor, the PNG and the Canva export. Renders are content-addressed and cached.

## Quality and safety

- About 1,500 automated tests (pytest + Hypothesis, Vitest), `ruff`, strict `mypy`, `gitleaks`, and a
  frozen benchmark of five fictional clients (`benchmarks/v1`).
- Live findings become regression tests: e.g. "picking another option kept the first draft's words"
  and "an edit dropped the 30% off" are both tests now.
- **Incident, Oct 2026**: the hosting provider disclosed that an attacker had read environment
  variables across many projects. In response every key was rotated, the legacy shared JWT secret
  was replaced by an asymmetric signing key, keys were split per environment with spend limits, and
  the database and provider usage were reviewed: no writes in the attack window; reads could not be
  ruled out, so client data was treated as exposed. Only one service held privileged keys, which
  kept the rotation small. Runbook: `docs/runbooks/rotate-secrets.md`.

## Stack

Python 3.12, uv workspace, FastAPI, Pydantic v2, Postgres (Supabase), resvg + HarfBuzz + Pillow,
ONNX Runtime (SigLIP, YuNet, U²-Net), React 19 + TypeScript + Vite + Radix, Railway.

## Running it

```bash
make setup      # uv sync + pre-commit
make check      # lint, types, tests
make web        # build the editor
make api        # http://localhost:8000 (old page) and /app (editor)
```

Copy `.env.example` to `.env` (and `.env.worker` for the queue worker) first.

## Docs

- `AGENTS.md`: the architecture rules every contributor and coding agent follows
- `docs/adr/`: decision records · `docs/modules/`: one note per module · `docs/runbooks/`

## How it was built

Designed and built by Rushil Shandil, with AI coding assistants (Claude, Cursor) working to the rules
in `AGENTS.md`: tests first, one module per pull request, and every auth or database-policy change
reviewed line by line by a human.
