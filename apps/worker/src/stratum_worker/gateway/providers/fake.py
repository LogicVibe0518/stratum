"""A scriptable provider for tests and local development.

Every failure mode the router must survive - timeouts, rate limits, outages, and
malformed output - is reproducible here with no network and no spend.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from pydantic import BaseModel

from stratum_worker.gateway.errors import (
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import TaskName

Behaviour = Callable[[TaskName, BaseModel], dict[str, object]]


@dataclass
class FakeProvider:
    """Answers with `results`, or raises whatever `failures` says, in order."""

    name: str = "fake"
    tasks: frozenset[TaskName] = field(
        default_factory=lambda: frozenset(TaskName.__members__.values())
    )
    results: list[dict[str, object]] = field(default_factory=list)
    failures: list[Exception | None] = field(default_factory=list)
    cost_usd: float = 0.001
    delay_s: float = 0.0
    calls: list[tuple[TaskName, BaseModel]] = field(default_factory=list)
    _failures: Iterator[Exception | None] | None = None
    _results: Iterator[dict[str, object]] | None = None

    def supports(self, task: TaskName) -> bool:
        return task in self.tasks

    def estimate_usd(self, task: TaskName, request: BaseModel) -> float:
        return self.cost_usd

    async def invoke(
        self, task: TaskName, request: BaseModel, *, timeout_s: float
    ) -> tuple[dict[str, object], ProviderCall]:
        self.calls.append((task, request))
        if self._failures is None:
            self._failures = iter(self.failures)
        if self._results is None:
            self._results = iter(self.results)

        failure = next(self._failures, None)
        if failure is not None:
            raise failure
        if self.delay_s:
            if self.delay_s >= timeout_s:
                raise ProviderTimeout(f"{self.name} exceeded {timeout_s}s")
            await asyncio.sleep(self.delay_s)
        result = next(self._results, {})
        return result, ProviderCall(model=f"{self.name}/test", cost_usd=self.cost_usd)


def always_unavailable(name: str = "broken") -> FakeProvider:
    return FakeProvider(name=name, failures=[ProviderUnavailable(name)] * 50)


def always_rate_limited(name: str = "busy") -> FakeProvider:
    return FakeProvider(name=name, failures=[ProviderRateLimited(name)] * 50)
