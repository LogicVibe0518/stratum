# C2 — Deploy: one image, two services on Railway

**Branch:** `chore/deploy` · **Who:** Cursor · **Reviewer:** Claude · **Needs:** M8 merged

## Paste this into Cursor

```
Read AGENTS.md, then docs/briefs/C2-deploy.md, and implement it exactly.
Create only: Dockerfile, .dockerignore, infra/railway/api.json,
infra/railway/worker.json, and replace infra/README.md. Change no Python.
Then build and run the image locally with the commands in the brief and paste
their output into your final report. Run `make check` at the end.
```

## Why this module exists

The owner has to open a real link, not watch a laptop. And the API and the worker
must run as **two services with separate secrets**: that is what turns the
`.env.worker` split from hygiene into an actual boundary. The API's environment never
contains the service-role key or the JWT secret; the worker's does.

## The image: `Dockerfile` (use verbatim)

```dockerfile
# One image for both services; Railway gives each its own start command.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"

# Dependencies first, so a code change does not reinstall numpy and Pillow.
COPY pyproject.toml uv.lock ./
COPY packages/spec/pyproject.toml packages/spec/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --all-packages --frozen --no-dev --no-install-workspace

COPY . .
RUN uv sync --all-packages --frozen --no-dev \
 && useradd --create-home app && chown -R app /app
USER app

# The API by default; the worker service overrides this (infra/railway/worker.json).
CMD uvicorn stratum_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers
```

## `.dockerignore` (use verbatim)

```
.git
.venv
**/__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
.stratum
out
renders
data
clients/*/out
ml
benchmarks
.env
.env.*
!.env.example
*.pem
*.key
```

## Railway config: `infra/railway/api.json` and `infra/railway/worker.json`

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "healthcheckPath": "/healthz",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5
  }
}
```

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "startCommand": "stratum-queue --factory stratum_worker.queue.identity:from_env --concurrency 4",
    "restartPolicyType": "ALWAYS"
  }
}
```

## `infra/README.md` (write this content, in plain words)

Explain, for Rushil setting it up in the Railway dashboard:

1. New project, "Deploy from GitHub repo", pick `LogicVibe0518/stratum`, region
   **EU West** (closest to the Supabase project in London).
2. Two services from the same repo. In each service's Settings, set "Config file path"
   to `infra/railway/api.json` or `infra/railway/worker.json`.
3. Variables. API: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OPENROUTER_API_KEY`,
   `OPENAI_API_KEY`, `FAL_KEY`, and the `LANGFUSE_*` keys if used. Worker: the same,
   **plus** `SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_JWT_SECRET`. Never give the API
   those two.
4. API service: Networking, "Generate domain". That URL is what the owner opens.
5. Check: `https://<domain>/healthz` shows `"status": "ok"` and every task has a
   provider. The worker's logs show it polling with no errors.
6. Supabase: move the project to Pro before the owner logs in, so it never pauses.

## Local check (put the output in your report)

Run from the repo root with OrbStack or Docker Desktop running:

```
docker build -t stratum .
docker run --rm -p 8000:8000 --env-file .env stratum &
sleep 8 && curl -s localhost:8000/healthz
docker run --rm --env-file .env --env-file .env.worker stratum \
  python -m stratum_worker.queue.identity --check <your user id>
```

Docker's `--env-file` keeps quotes literally, so if a value in `.env` is quoted, pass
that variable with `-e NAME=value` instead. Then stop the API container. The first command must build without errors, `/healthz`
must answer, and the identity check must print `ok:`.

## Acceptance (Claude checks)

- [ ] The image builds from a clean clone and contains no `.env` file
      (`docker run --rm stratum ls -a /app` shows none).
- [ ] It runs as a non-root user.
- [ ] The worker config starts `stratum-queue` with `identity:from_env`.
- [ ] `infra/README.md` says which variables go to which service, and that the API
      never gets the service key or the JWT secret.
- [ ] No Python changed; `make check` passes.

## Do not

- Do not put any secret in any file in the repo, including examples with real values.
- Do not add a Procfile, docker-compose, Kubernetes or a second Dockerfile.
- Do not change the API's port handling in Python; `$PORT` is handled in `CMD`.
