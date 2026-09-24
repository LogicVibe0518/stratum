"""Gateway failures, named so the router can decide what to do with each."""

from __future__ import annotations


class GatewayError(Exception):
    """Base class. Everything the gateway raises is one of these."""


class ProviderUnavailable(GatewayError):
    """Network error, 5xx, or the circuit breaker is open. Try the next provider."""


class ProviderRateLimited(GatewayError):
    """429. Back off, then try the next provider."""


class ProviderTimeout(GatewayError):
    """The call exceeded the task's latency budget."""


class InvalidProviderOutput(GatewayError):
    """The provider answered, but the answer did not match the task's result model.

    Retried once on the same provider (models often fix themselves), then treated
    as a provider failure.
    """


class BudgetExceeded(GatewayError):
    """The agency's daily AI spend cap would be breached. Never retried."""


class NoProviderAvailable(GatewayError):
    """Every provider for this task failed or is unavailable."""
