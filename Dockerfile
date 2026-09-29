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
