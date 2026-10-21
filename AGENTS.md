# AGENTS.md — rules for every coding agent (Claude Code, Cursor, Codex, GPT)

Read this whole file before changing anything. If a request conflicts with these rules, stop and ask.

## What Stratum is
Stratum turns a client's brief, real photos and brand kit into on-brand social posts in minutes,
and handles client tweaks in seconds. Built for small agencies serving blue-collar SMBs (UK/US).
Core idea: **a poster is data, not pixels.** Every poster is a layered JSON spec; our own
compositor renders it. AI models may generate a *background*, never a finished poster.

## Architecture rules (non-negotiable)
1. **Poster = layer spec.** Layers: background, subject cutout, shapes, logo, text. Never bake text or logos into generated images.
2. **Logos are always the client's original file.** Never generated, traced or recoloured by a model.
3. **Real product pixels are sacred.** "Generate around, paste the truth back": model output goes behind; original product pixels go on top via mask.
4. **Slow commands go through the Postgres job queue (`stratum_worker/queue`); reads go straight to Postgres.** No slow or multi-step work inside an HTTP request. DBOS is not used: the lease queue covers what it was wanted for.
5. **Every AI call goes through the task gateway** (`caption.generate`, `edit.parse`, `background.create`, ...). Application code never imports a vendor SDK directly.
6. **Model output is data, never code or markup.** Validate every model output with Pydantic; edit ops must pass the allowlist + template slot schema.
7. **Immutable versions.** Poster versions are never mutated; edits create a new version.
8. **Versioned spec schema.** Every spec has `schema_version`; changes ship with an upgrader + test.
9. **Idempotency keys on every command endpoint.**
10. **Tenancy everywhere.** Every row has `agency_id`; RLS on every table; tenant comes from the verified token, never the request body. The queue worker is the one process that holds credentials able to cross agencies: the service-role key, used for exactly two statements - claim a job, settle a job - in `stratum_worker/queue/ledger.py`; and the JWT secret, used only in `stratum_worker/queue/identity.py` to sign a token for the job's requester that expires with the job's lease. All work on a job runs through `factory(job)`, a client acting as that person, so the database refuses a cross-tenant write even if the code asks for one. `jobs.requested_by` is stamped by the database from `auth.uid()`; never mint a credential from a value a user can write. The API holds neither key and drops the service-role key from its environment at import if it finds it.
11. **Content addressing.** Assets and renders are stored by SHA-256; render cache key = hash(spec, template version, renderer version, fonts).

## Never
- Never commit secrets, `.env`, real client data, or real business names/logos/photos.
- Never write or change RLS policies, migrations, auth or token code without flagging it clearly in the PR description for line-by-line human review.
- Never add a dependency outside the allowed licences: MIT, MIT-CMU, Apache-2.0, BSD, ISC, OFL (fonts). GPL, AGPL, LGPL and non-commercial licences (e.g. CC BY-NC) need a written decision first.
- Never let a model publish, send, or approve anything. A human click is required.
- Never skip or weaken a test to make CI pass.
- Never edit files under `benchmarks/v1/` — create `v2` instead.

## Stack
Python 3.12 · uv workspace · FastAPI · Pydantic v2 · Postgres (Supabase) with a lease-based job queue · resvg + HarfBuzz (uharfbuzz) + Pillow ·
One-file web UI served by FastAPI (`apps/api/static`) for the pilot, replaced step by step by the editor in `apps/web` (Vite + React + TypeScript, Radix, built to static files the API serves at `/app`) · pytest + Hypothesis · Vitest · ruff · mypy (strict on packages/) ·
Langfuse (AI traces) · Sentry · OpenTelemetry.

## Repo map
- `packages/spec` — the layer spec + edit-op schema (source of truth). Pure Python, no I/O.
- `apps/api` — FastAPI: auth, commands (start workflows), reads.
- `apps/worker` — the job queue, compositor, image ops, AI gateway calls.
- `apps/web` — the editor (Vite + React + TypeScript): the poster canvas, served by the API at `/app`.
- `ml/` — command-model data, training, evals.
- `benchmarks/` — frozen Client Lab benchmark (5 fictional clients). Read-only per version.
- `docs/adr/` — architecture decision records. **Written by the human owner, not by agents.**
- `infra/` — local dev + deploy config.

## Commands
- `make setup` — install everything (`uv sync --all-packages`, pre-commit install)
- `make check` — ruff + format check + mypy + tests (must pass before any PR)
- `make test` — pytest
- `make api` — run the API locally

## How to work on a module
1. Read the module spec in the issue/PR description and the relevant ADR.
2. Write or update tests first (including failure cases).
3. Implement the smallest change that passes. Prefer boring code.
4. Run `make check`.
5. In the PR description: what changed, why, risks, and a 5-bullet explanation a reviewer can repeat back.
One module per branch/PR. Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).

## Style
- Type hints everywhere; no `Any` in `packages/`.
- Small pure functions; side effects at the edges.
- Errors: raise typed exceptions; never swallow.
- Logs: structured JSON, include `agency_id`, `workflow_id`, never secrets or raw client images.

## UI rules (apps/web)
Monochrome chrome: black, white and greys. One accent colour, only for focus states and the voice orb.
Red/amber/green only for status. The client's poster is the only colourful thing on screen.
Light and dark mode. Typography-led layout; no gradients, no decorative illustrations.
