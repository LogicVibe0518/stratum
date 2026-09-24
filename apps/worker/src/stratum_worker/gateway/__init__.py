"""Model-agnostic AI gateway."""

from stratum_worker.gateway.budget import Budget
from stratum_worker.gateway.errors import (
    BudgetExceeded,
    GatewayError,
    InvalidProviderOutput,
    NoProviderAvailable,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from stratum_worker.gateway.gateway import Gateway, NullTracer, Tracer
from stratum_worker.gateway.router import Router, TaskPolicy
from stratum_worker.gateway.tasks import (
    BackgroundRequest,
    BackgroundResult,
    CaptionRequest,
    CaptionResult,
    EditParseRequest,
    EditParseResult,
    TaskName,
)

__all__ = [
    "BackgroundRequest",
    "BackgroundResult",
    "Budget",
    "BudgetExceeded",
    "CaptionRequest",
    "CaptionResult",
    "EditParseRequest",
    "EditParseResult",
    "Gateway",
    "GatewayError",
    "InvalidProviderOutput",
    "NoProviderAvailable",
    "NullTracer",
    "ProviderRateLimited",
    "ProviderTimeout",
    "ProviderUnavailable",
    "Router",
    "TaskName",
    "TaskPolicy",
    "Tracer",
]
