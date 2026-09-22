# Stratum

Layer-first poster engine for small social media agencies. A brief, real client photos and a brand kit go in;
on-brand, fully editable posts come out in minutes. Client tweaks take seconds.

> Status: R0 in progress. Private until IP terms are settled.

## Why it's different
Image models can't be trusted with logos, brand colours or text, so Stratum never asks them to.
Every poster is a layered spec rendered by our own compositor; models only paint backgrounds.

## Quick start (macOS)
```bash
make setup      # uv sync + pre-commit
make check      # lint, types, tests
make api        # http://localhost:8000/healthz
```

## Docs
- `AGENTS.md` — architecture rules for humans and coding agents
- `docs/adr/` — decision records
- `benchmarks/v1/` — Client Lab benchmark (5 fictional clients)
