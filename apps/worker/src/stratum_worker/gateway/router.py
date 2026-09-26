"""Routing policy: which provider serves a task, and in what order.

Policy is data, not code, so switching to a model released this morning is a config
edit plus an eval run - never a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stratum_worker.gateway.errors import NoProviderAvailable
from stratum_worker.gateway.providers.base import Provider
from stratum_worker.gateway.tasks import TaskName


@dataclass(frozen=True)
class TaskPolicy:
    """Providers are tried in order; `canary` gets a share of traffic first."""

    order: tuple[str, ...]
    canary: str | None = None
    canary_percent: int = 0
    max_attempts_per_provider: int = 2


@dataclass
class Router:
    providers: dict[str, Provider]
    policies: dict[TaskName, TaskPolicy]
    breakers: dict[str, object] = field(default_factory=dict)

    def chain(self, task: TaskName, *, roll: int = 100) -> list[Provider]:
        """The providers to try for this task, best first.

        `roll` is a 0-99 draw supplied by the caller (seeded in tests) so canary
        routing is deterministic under test and random in production.
        """
        policy = self.policies.get(task)
        if policy is None:
            raise KeyError(f"no routing policy for {task}")

        names = list(policy.order)
        if policy.canary and roll < policy.canary_percent and policy.canary in self.providers:
            names = [policy.canary, *[n for n in names if n != policy.canary]]

        chain = [self.providers[n] for n in names if n in self.providers]
        usable = [p for p in chain if p.supports(task)]
        if not usable:
            raise NoProviderAvailable(
                f"nothing is configured to serve {task}. Check that the right API key is "
                f"set (see .env.example) and that config/gateway.toml lists a model for it."
            )
        return usable
