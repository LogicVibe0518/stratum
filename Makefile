.PHONY: setup sync check lint fmt type test live api

setup:
	uv sync --all-packages
	uv run pre-commit install --install-hooks

sync:
	uv sync --all-packages --quiet

lint: sync
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

type: sync
	uv run mypy

test: sync
	uv run pytest

check: lint type test

# Real provider calls that cost real money. Keys come from the environment or .env.
live: sync
	set -a; [ -f .env ] && . ./.env; set +a; STRATUM_LIVE=1 uv run pytest -m live -rs

api: sync
	uv run uvicorn stratum_api.main:app --reload --port 8000
