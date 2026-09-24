"""The AI gateway: one call site for every model in Stratum.

Responsibilities, in order of importance:
  1. Never let an AI call corrupt the system - results are validated against the
     task's model before anything else sees them.
  2. Never let one vendor's bad day become an outage - timeouts, retries with
     jitter, circuit breakers and failover across providers.
  3. Never let spend run away - budget checked before the call, recorded after.
  4. Make everything measurable - one trace per call with model, cost and latency.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

from stratum_worker.gateway.breaker import Breaker
from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.errors import (
    GatewayError,
    InvalidProviderOutput,
    NoProviderAvailable,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.router import Router
from stratum_worker.gateway.tasks import RESULT_TYPES, TIMEOUTS_S, TaskName

BASE_BACKOFF_S = 0.25
MAX_BACKOFF_S = 4.0


class Tracer(Protocol):
    """Where call records go. Langfuse implements this; tests use a list."""

    def record(
        self,
        *,
        task: TaskName,
        provider: str,
        call: ProviderCall | None,
        outcome: str,
        agency_id: str,
        error: str | None = None,
    ) -> None: ...


class NullTracer:
    def record(self, **_: object) -> None:
        return None


@dataclass
class Gateway:
    router: Router
    budget: Budget = field(default_factory=Budget)
    tracer: Tracer = field(default_factory=NullTracer)
    rng: random.Random = field(default_factory=random.Random)
    sleep: object = asyncio.sleep

    def _breaker(self, provider_name: str) -> Breaker:
        breaker = self.router.breakers.get(provider_name)
        if not isinstance(breaker, Breaker):
            breaker = Breaker()
            self.router.breakers[provider_name] = breaker
        return breaker

    async def _backoff(self, attempt: int) -> None:
        delay = min(BASE_BACKOFF_S * (2**attempt), MAX_BACKOFF_S)
        jitter = self.rng.uniform(0, delay / 2)  # jitter stops retry storms
        await self.sleep(delay + jitter)  # type: ignore[operator]

    async def run(
        self,
        task: TaskName,
        request: BaseModel,
        *,
        agency_id: str,
        timeout_s: float | None = None,
    ) -> BaseModel:
        """Run one task. Returns a validated result model or raises a GatewayError."""
        budget_s = timeout_s or TIMEOUTS_S[task]
        result_type = RESULT_TYPES[task]
        chain = self.router.chain(task, roll=self.rng.randrange(100))
        policy = self.router.policies[task]
        last_error: Exception | None = None

        for provider in chain:
            breaker = self._breaker(provider.name)
            if not breaker.allows():
                self.tracer.record(
                    task=task,
                    provider=provider.name,
                    call=None,
                    outcome="skipped_circuit_open",
                    agency_id=agency_id,
                )
                continue

            estimate = provider.estimate_usd(task, request)
            self.budget.check(agency_id, estimate)  # raises BudgetExceeded, never retried

            for attempt in range(policy.max_attempts_per_provider):
                try:
                    raw, call = await asyncio.wait_for(
                        provider.invoke(task, request, timeout_s=budget_s),
                        timeout=budget_s,
                    )
                except (TimeoutError, ProviderTimeout) as exc:
                    # A timeout means the provider is slow right now; retrying the
                    # same one burns the caller's latency budget twice. Move on.
                    last_error = ProviderTimeout(f"{provider.name} timed out")
                    breaker.record_failure()
                    self.tracer.record(
                        task=task,
                        provider=provider.name,
                        call=None,
                        outcome="timeout",
                        agency_id=agency_id,
                        error=str(exc),
                    )
                    break  # a timeout will not fix itself on a retry; move on
                except (ProviderUnavailable, ProviderRateLimited) as exc:
                    last_error = exc
                    breaker.record_failure()
                    self.tracer.record(
                        task=task,
                        provider=provider.name,
                        call=None,
                        outcome=type(exc).__name__,
                        agency_id=agency_id,
                        error=str(exc),
                    )
                    if attempt + 1 < policy.max_attempts_per_provider:
                        await self._backoff(attempt)
                        continue
                    break

                try:
                    validated = result_type.model_validate(raw)
                except ValidationError as exc:
                    last_error = InvalidProviderOutput(
                        f"{provider.name}: {exc.error_count()} validation errors"
                    )
                    self.tracer.record(
                        task=task,
                        provider=provider.name,
                        call=call,
                        outcome="invalid_output",
                        agency_id=agency_id,
                        error=str(exc)[:500],
                    )
                    self.budget.record(agency_id, call.cost_usd)  # a bad answer still costs
                    if attempt + 1 < policy.max_attempts_per_provider:
                        continue  # models often fix themselves on a second pass
                    breaker.record_failure()
                    break

                breaker.record_success()
                self.budget.record(agency_id, call.cost_usd)
                self.tracer.record(
                    task=task,
                    provider=provider.name,
                    call=call,
                    outcome="ok",
                    agency_id=agency_id,
                )
                return validated

        raise NoProviderAvailable(f"{task}: every provider failed") from last_error


__all__ = ["Gateway", "GatewayError", "NullTracer", "Tracer"]
