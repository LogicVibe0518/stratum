"""The free load-test path: percentiles, the $2 budget cap, and kill -9 recovery."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.errors import BudgetExceeded
from stratum_worker.queue.fake import gateway

REPO = Path(__file__).resolve().parents[3]


def loadtest():
    path = REPO / "tools" / "loadtest.py"
    spec = importlib.util.spec_from_file_location("stratum_loadtest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_percentile_is_defined_on_tiny_samples() -> None:
    lt = loadtest()
    assert lt.percentile([], 95) == 0.0
    assert lt.percentile([4.0], 50) == 4.0
    assert lt.percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)


async def test_fake_bench_finishes_every_job(tmp_path: Path) -> None:
    lt = loadtest()
    report = await lt.run_bench(
        tmp_path,
        n=3,
        concurrency=2,
        fake=True,
        model_delay_s=0.0,
        budget_usd=2.0,
        lease_s=60,
    )
    assert report.lost == 0
    assert len(report.done) == 3
    assert report.cost_usd == 0.0
    assert not report.budget_stopped
    assert all(s.work_s >= 0 and s.queue_wait_s >= 0 for s in report.done)


def test_crash_recovers_every_job(tmp_path: Path) -> None:
    lt = loadtest()
    report = lt.run_crash(tmp_path, n=4, lease_s=4, model_delay_s=0.25)
    open_jobs = [s.job_id for s in report.samples if s.status not in ("done", "failed")]
    assert report.lost == 0, f"jobs still open: {open_jobs}"
    assert len(report.done) == 4


def test_loadtest_gateway_uses_the_product_budget() -> None:
    """The cap the load test installs is Budget, not a side-channel counter."""
    capped = gateway(budget=Budget(daily_cap_usd=2.0))
    assert capped.budget.daily_cap_usd == 2.0
    capped.budget.record("agency", 2.0)
    with pytest.raises(BudgetExceeded):
        capped.budget.check("agency", 0.001)
