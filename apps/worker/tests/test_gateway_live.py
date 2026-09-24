"""Real provider calls. Costs real money (well under a cent). Run with `make live`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from stratum_worker.gateway import Budget, CaptionRequest, CaptionResult, Gateway, TaskName
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.providers.registry import build_router
from stratum_worker.gateway.tracing import tracer_from_env

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("STRATUM_LIVE") != "1",
        reason="spends real money; set STRATUM_LIVE=1 (make live) to run",
    ),
]


async def test_live_caption_generate_records_cost() -> None:
    config = load_config(REPO_ROOT / "config" / "gateway.toml")
    router = build_router(config)
    if not any(p.supports(TaskName.CAPTION_GENERATE) for p in router.providers.values()):
        pytest.fail("no API key set for any caption.generate provider (see .env.example)")
    budget = Budget(daily_cap_usd=0.25)
    gateway = Gateway(router=router, budget=budget, tracer=tracer_from_env())

    result = await gateway.run(
        TaskName.CAPTION_GENERATE,
        CaptionRequest(
            brief="Autumn boiler service, 10% off for bookings before 31 October.",
            client_name="Northgate Heating (fictional)",
            tone="friendly, plain-spoken",
        ),
        agency_id="live-test",
    )

    assert isinstance(result, CaptionResult)
    assert result.caption.strip()
    assert budget.spent_today("live-test") > 0, "cost was not recorded"
    close = getattr(gateway.tracer, "close", None)
    if callable(close):
        close()
