"""The one interface every provider implements.

Application code depends on this Protocol, never on a vendor SDK. Adding OpenRouter,
fal, OpenAI or a self-hosted model is a new class here plus a config entry - no change
anywhere else in Stratum.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from stratum_worker.gateway.tasks import TaskName


class ProviderCall(BaseModel):
    """What a provider reports back about one call, for tracing and billing."""

    model: str
    cost_usd: float = 0.0
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@runtime_checkable
class Provider(Protocol):
    """Implementations must be stateless and safe to call concurrently."""

    name: str

    def supports(self, task: TaskName) -> bool:
        """Can this provider serve that task?"""
        ...

    def estimate_usd(self, task: TaskName, request: BaseModel) -> float:
        """Rough price before the call, used for the budget check."""
        ...

    async def invoke(
        self, task: TaskName, request: BaseModel, *, timeout_s: float
    ) -> tuple[dict[str, object], ProviderCall]:
        """Run the task and return (raw result dict, call metadata).

        Raise ProviderUnavailable / ProviderRateLimited / ProviderTimeout on failure.
        Never raise a vendor-specific exception past this boundary.
        """
        ...
