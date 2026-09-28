"""/healthz tells the truth about AI providers, whatever machine the tests run on.

It used to read the provider keys from the environment, so it passed on a laptop
with a .env and failed in CI, which has none: every pull request showed a red X
for a reason unrelated to the change. The router is now built from an explicit
set of keys for each case.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from stratum_api import main
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import build_router

# Placeholders: nothing is called, the router only needs to know a provider exists.
KEYS = {"OPENROUTER_API_KEY": "test", "FAL_KEY": "test", "OPENAI_API_KEY": "test"}


def health(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> dict[str, object]:
    monkeypatch.setattr(main, "_gateway", Gateway(router=build_router(load_config(), env)))
    res = TestClient(main.app).get("/healthz")
    assert res.status_code == 200
    body: dict[str, object] = res.json()
    return body


def test_healthz_is_ok_when_every_ai_task_has_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    body = health(monkeypatch, KEYS)
    assert body["status"] == "ok"
    assert body["unserved_tasks"] == []


def test_healthz_names_what_is_missing_instead_of_pretending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = health(monkeypatch, {})
    assert body["status"] == "degraded"
    assert body["unserved_tasks"]
