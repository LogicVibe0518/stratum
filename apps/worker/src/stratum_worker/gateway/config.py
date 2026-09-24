"""Gateway configuration: provider order, canary share, model names and prices.

Model choice is data, not code (see docs/modules/M6-gateway.md). The file at
config/gateway.toml is merged over the defaults below, table by table, so it only
needs to say what differs. A file that exists but is invalid is an error, never
silently replaced by defaults.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratum_worker.gateway.router import TaskPolicy
from stratum_worker.gateway.tasks import TaskName

DEFAULT_PATH = Path("config/gateway.toml")
DEFAULT_MAX_OUTPUT_TOKENS = 600


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskSettings(_Frozen):
    order: tuple[str, ...] = Field(min_length=1)
    canary: str | None = None
    canary_percent: int = Field(default=0, ge=0, le=100)
    max_attempts_per_provider: int = Field(default=2, ge=1, le=5)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, ge=1, le=16_000)

    @model_validator(mode="after")
    def _canary_named(self) -> Self:
        if self.canary_percent and not self.canary:
            raise ValueError("canary_percent is set but no canary provider is named")
        return self


class ModelPrice(_Frozen):
    """USD. Used for the budget estimate, and for actual cost when a provider
    does not report one."""

    input_per_mtok: float = Field(default=0.0, ge=0.0)
    output_per_mtok: float = Field(default=0.0, ge=0.0)
    per_image: float = Field(default=0.0, ge=0.0)

    def tokens_usd(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.input_per_mtok + tokens_out * self.output_per_mtok) / 1e6


class GatewayConfig(_Frozen):
    tasks: dict[TaskName, TaskSettings]
    # provider name -> task -> model name
    models: dict[str, dict[TaskName, str]]
    # model name -> price
    prices: dict[str, ModelPrice]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for task, settings in self.tasks.items():
            for provider in (*settings.order, *filter(None, [settings.canary])):
                if task not in self.models.get(provider, {}):
                    raise ValueError(f"{task}: provider {provider!r} has no model configured")
        for provider, per_task in self.models.items():
            for task, model in per_task.items():
                if model not in self.prices:
                    # An unpriced model would pass every budget check for free.
                    raise ValueError(f"{provider}/{task}: model {model!r} has no price")
        return self

    def policies(self) -> dict[TaskName, TaskPolicy]:
        return {
            task: TaskPolicy(
                order=settings.order,
                canary=settings.canary,
                canary_percent=settings.canary_percent,
                max_attempts_per_provider=settings.max_attempts_per_provider,
            )
            for task, settings in self.tasks.items()
        }

    def has_model(self, provider: str, task: TaskName) -> bool:
        return task in self.models.get(provider, {})

    def model(self, provider: str, task: TaskName) -> str:
        try:
            return self.models[provider][task]
        except KeyError as exc:
            raise KeyError(f"no model configured for {provider}/{task}") from exc

    def price(self, model: str) -> ModelPrice:
        return self.prices[model]

    def max_output_tokens(self, task: TaskName) -> int:
        settings = self.tasks.get(task)
        return settings.max_output_tokens if settings else DEFAULT_MAX_OUTPUT_TOKENS


# Defaults mirror config/gateway.toml. Model ids and prices verified 2026-09-25
# against openrouter.ai/api/v1/models, developers.openai.com/api/docs/pricing and
# fal.ai. Image prices are rounded UP: under-pricing a model would let it slip past
# the daily budget cap, which is the one control standing between a bad loop and a
# real bill.
_DEFAULTS: dict[str, object] = {
    "tasks": {
        "caption.generate": {"order": ["openrouter", "openai"], "max_output_tokens": 600},
        "edit.parse": {"order": ["openrouter", "openai"], "max_output_tokens": 400},
        "background.create": {"order": ["fal", "openai"]},
    },
    "models": {
        "openrouter": {
            "caption.generate": "openai/gpt-6-luna",
            "edit.parse": "openai/gpt-6-luna",
        },
        "openai": {
            "caption.generate": "gpt-6-luna",
            "edit.parse": "gpt-6-luna",
            "background.create": "gpt-image-2.5-flare",
        },
        "fal": {"background.create": "fal-ai/flux/schnell"},
    },
    "prices": {
        "openai/gpt-6-luna": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},
        "gpt-6-luna": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},
        "gpt-image-2.5-flare": {"per_image": 0.025},
        "fal-ai/flux/schnell": {"per_image": 0.006},
    },
}


def _merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def default_config() -> GatewayConfig:
    return GatewayConfig.model_validate(_DEFAULTS)


def load_config(path: Path = DEFAULT_PATH) -> GatewayConfig:
    """Defaults if the file is absent; defaults merged with the file if present."""
    if not path.is_file():
        return default_config()
    with path.open("rb") as handle:
        overrides = tomllib.load(handle)
    return GatewayConfig.model_validate(_merge(_DEFAULTS, overrides))
