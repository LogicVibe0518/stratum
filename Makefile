.PHONY: setup sync check lint fmt type test live api notebook design-sheet

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

# The critic (M12a) is trained by a person, step by step, in this notebook. Opens
# JupyterLab in the browser; jupyter, pandas and matplotlib are fetched for this run
# only and are not project dependencies.
notebook: sync
	uv run --with jupyterlab --with pandas --with matplotlib jupyter lab ml/critic/walkthrough.ipynb

# A contact sheet of design-space candidates for one fictional client.
design-sheet: sync
	uv run python -m stratum_worker.design.preview --client kowalski-plumbing --out design-sheet.png
