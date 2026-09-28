.PHONY: setup sync check lint fmt type test live api

# Git hooks are shared by every worktree of a repo, and pre-commit writes the path of
# THIS checkout's Python into them. Installing from a worktree would point the main
# checkout's hooks at a folder that is deleted with the worktree, breaking every push.
setup:
	uv sync --all-packages
	@if [ "$$(git rev-parse --git-dir)" = "$$(git rev-parse --git-common-dir)" ]; then \
		uv run pre-commit install --install-hooks; \
	else \
		echo "In a worktree: git hooks belong to the main checkout, so they were left alone."; \
	fi

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
