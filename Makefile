.PHONY: setup check lint fmt type test api

setup:
	uv sync --all-packages
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

type:
	uv run mypy

test:
	uv run pytest

check: lint type test

api:
	uv run uvicorn stratum_api.main:app --reload --port 8000
