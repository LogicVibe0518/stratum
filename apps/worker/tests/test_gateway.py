from __future__ import annotations

import pytest
from stratum_worker.gateway import (
    BudgetExceeded,
    CaptionRequest,
    CaptionResult,
    Gateway,
    NoProviderAvailable,
    Router,
    TaskName,
    TaskPolicy,
)
from stratum_worker.gateway.breaker import FAILURE_THRESHOLD, Breaker
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.errors import TaskNotSupported
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.providers.fake import (
    FakeProvider,
    always_rate_limited,
    always_unavailable,
)

TASK = TaskName.CAPTION_GENERATE
GOOD = {"caption": "Fresh jalebi every Sunday", "hashtags": ["#southall"]}


class ListTracer:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def build(*providers: FakeProvider, canary: str | None = None, percent: int = 0) -> Gateway:
    registry = {p.name: p for p in providers}
    router = Router(
        providers=dict(registry),
        policies={TASK: TaskPolicy(order=tuple(registry), canary=canary, canary_percent=percent)},
    )
    gateway = Gateway(router=router, budget=Budget(daily_cap_usd=1.0))
    gateway.sleep = _no_sleep
    return gateway


async def _no_sleep(_seconds: float) -> None:
    return None


def request() -> CaptionRequest:
    return CaptionRequest(brief="20% off thalis", client_name="Sharma's Dhaba")


@pytest.mark.asyncio
async def test_happy_path_returns_a_validated_model() -> None:
    gateway = build(FakeProvider(name="primary", results=[GOOD]))
    result = await gateway.run(TASK, request(), agency_id="nimble")
    assert isinstance(result, CaptionResult)
    assert result.caption.startswith("Fresh")


@pytest.mark.asyncio
async def test_fails_over_to_the_next_provider() -> None:
    broken = always_unavailable("primary")
    backup = FakeProvider(name="backup", results=[GOOD])
    gateway = build(broken, backup)
    result = await gateway.run(TASK, request(), agency_id="nimble")
    assert isinstance(result, CaptionResult)
    assert backup.calls, "backup provider was never tried"


@pytest.mark.asyncio
async def test_rate_limit_also_fails_over() -> None:
    gateway = build(always_rate_limited("busy"), FakeProvider(name="backup", results=[GOOD]))
    assert isinstance(await gateway.run(TASK, request(), agency_id="nimble"), CaptionResult)


@pytest.mark.asyncio
async def test_malformed_output_is_retried_then_failed_over() -> None:
    """A model that answers with the wrong shape must never reach the caller."""
    messy = FakeProvider(name="messy", results=[{"nope": 1}, {"still": "wrong"}])
    backup = FakeProvider(name="backup", results=[GOOD])
    gateway = build(messy, backup)
    result = await gateway.run(TASK, request(), agency_id="nimble")
    assert isinstance(result, CaptionResult)
    assert len(messy.calls) == 2, "should have retried once on the same provider"


@pytest.mark.asyncio
async def test_timeout_moves_on_immediately() -> None:
    slow = FakeProvider(name="slow", delay_s=99.0)
    backup = FakeProvider(name="backup", results=[GOOD])
    gateway = build(slow, backup)
    result = await gateway.run(TASK, request(), agency_id="nimble", timeout_s=0.05)
    assert isinstance(result, CaptionResult)
    assert len(slow.calls) == 1, "a timeout should not be retried on the same provider"


@pytest.mark.asyncio
async def test_every_provider_down_raises() -> None:
    gateway = build(always_unavailable("a"), always_unavailable("b"))
    with pytest.raises(NoProviderAvailable):
        await gateway.run(TASK, request(), agency_id="nimble")


@pytest.mark.asyncio
async def test_budget_stops_the_call_before_it_costs_money() -> None:
    expensive = FakeProvider(name="expensive", results=[GOOD], cost_usd=10.0)
    gateway = build(expensive)
    with pytest.raises(BudgetExceeded):
        await gateway.run(TASK, request(), agency_id="nimble")
    assert expensive.calls == [], "provider must not be called when over budget"


@pytest.mark.asyncio
async def test_spend_is_recorded_per_agency() -> None:
    gateway = build(FakeProvider(name="p", results=[GOOD, GOOD], cost_usd=0.02))
    await gateway.run(TASK, request(), agency_id="nimble")
    await gateway.run(TASK, request(), agency_id="other")
    assert gateway.budget.spent_today("nimble") == pytest.approx(0.02)
    assert gateway.budget.spent_today("other") == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_all_attempts_of_one_run_share_a_run_id() -> None:
    """A retry and a failover are one call, not three. Traces must say so."""
    gateway = build(always_unavailable("primary"), FakeProvider(name="backup", results=[GOOD]))
    tracer = ListTracer()
    gateway.tracer = tracer
    await gateway.run(TASK, request(), agency_id="nimble")
    run_ids = {r["run_id"] for r in tracer.records}
    assert len(tracer.records) > 1, "expected retries and a failover to be recorded"
    assert len(run_ids) == 1, f"one call produced {len(run_ids)} trace ids"


@pytest.mark.asyncio
async def test_separate_runs_get_separate_run_ids() -> None:
    gateway = build(FakeProvider(name="primary", results=[GOOD, GOOD]))
    tracer = ListTracer()
    gateway.tracer = tracer
    await gateway.run(TASK, request(), agency_id="nimble")
    await gateway.run(TASK, request(), agency_id="nimble")
    assert len({r["run_id"] for r in tracer.records}) == 2


@pytest.mark.asyncio
async def test_unsupported_request_does_not_penalise_the_provider() -> None:
    """Declining a feature is not a fault. One unsupported request must not open
    the circuit on a healthy vendor and take its other tasks down with it."""
    picky = FakeProvider(name="picky", failures=[TaskNotSupported("picky: no reference images")])
    backup = FakeProvider(name="backup", results=[GOOD])
    gateway = build(picky, backup)
    tracer = ListTracer()
    gateway.tracer = tracer
    result = await gateway.run(TASK, request(), agency_id="nimble")
    assert isinstance(result, CaptionResult)
    assert len(picky.calls) == 1, "an unsupported request must not be retried"
    assert gateway.router.breakers["picky"].allows()
    assert [r["outcome"] for r in tracer.records] == ["unsupported", "ok"]


@pytest.mark.asyncio
async def test_every_call_is_traced() -> None:
    gateway = build(always_unavailable("down"), FakeProvider(name="up", results=[GOOD]))
    tracer = ListTracer()
    gateway.tracer = tracer
    await gateway.run(TASK, request(), agency_id="nimble")
    outcomes = [r["outcome"] for r in tracer.records]
    assert "ok" in outcomes
    assert any("Unavailable" in str(o) for o in outcomes)


def test_circuit_opens_after_repeated_failures() -> None:
    clock = [0.0]
    breaker = Breaker(now=lambda: clock[0])
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure()
    assert not breaker.allows(), "circuit should be open"
    clock[0] += 31.0  # past the cooldown
    assert breaker.allows(), "circuit should half-open and let a probe through"
    breaker.record_success()
    assert breaker.allows()


@pytest.mark.asyncio
async def test_open_circuit_skips_the_provider_without_calling_it() -> None:
    down = FakeProvider(name="down", results=[GOOD])
    up = FakeProvider(name="up", results=[GOOD])
    gateway = build(down, up)
    breaker = Breaker()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure()
    gateway.router.breakers["down"] = breaker
    await gateway.run(TASK, request(), agency_id="nimble")
    assert down.calls == [], "an open circuit must not be called"
    assert up.calls, "traffic should go to the healthy provider"


def test_canary_routing_is_deterministic_under_test() -> None:
    main = FakeProvider(name="main")
    new = FakeProvider(name="new")
    router = Router(
        providers={"main": main, "new": new},
        policies={TASK: TaskPolicy(order=("main", "new"), canary="new", canary_percent=10)},
    )
    assert router.chain(TASK, roll=5)[0].name == "new"  # inside the canary share
    assert router.chain(TASK, roll=50)[0].name == "main"  # outside it


def test_provider_call_defaults_are_zero() -> None:
    call = ProviderCall(model="x/y")
    assert call.cost_usd == 0.0
    assert call.tokens_in == 0


@pytest.mark.asyncio
async def test_a_cut_off_answer_is_not_retried_at_the_same_limit() -> None:
    """5 Oct copy eval: an answer cut off at max_output_tokens was retried on the same
    provider at the same limit and cut off again, four calls per brief. Move on."""
    from stratum_worker.gateway.tasks import CUT_OFF, UNPARSEABLE

    primary = FakeProvider(name="primary", results=[{UNPARSEABLE: CUT_OFF}, GOOD])
    backup = FakeProvider(name="backup", results=[GOOD])
    gateway = build(primary, backup)
    result = await gateway.run(TASK, request(), agency_id="nimble")
    assert isinstance(result, CaptionResult)
    assert len(primary.calls) == 1 and len(backup.calls) == 1


def test_post_copy_has_room_to_reason_and_answer() -> None:
    from stratum_worker.gateway.config import default_config, load_config
    from stratum_worker.gateway.tasks import TIMEOUTS_S

    for config in (default_config(), load_config()):
        assert config.max_output_tokens(TaskName.POST_COPY) >= 3000
    assert TIMEOUTS_S[TaskName.POST_COPY] >= 40
