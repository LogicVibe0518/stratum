"""OpenRouter adapter: caption.generate and edit.parse over the OpenAI-compatible
chat API. Cost comes from the response's `usage.cost` when OpenRouter reports it,
otherwise from the price table in config."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel

from stratum_worker.gateway.config import GatewayConfig
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.providers._http import (
    estimate_tokens_in,
    json_object,
    messages,
    parse_chat,
    response_format,
    send,
)
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import TaskName

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
TASKS = frozenset({TaskName.CAPTION_GENERATE, TaskName.EDIT_PARSE})


@dataclass
class OpenRouterProvider:
    api_key: str = field(repr=False)
    config: GatewayConfig = field(repr=False)
    name: str = "openrouter"
    base_url: str = DEFAULT_BASE_URL
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def supports(self, task: TaskName) -> bool:
        return task in TASKS and self.config.has_model(self.name, task)

    def estimate_usd(self, task: TaskName, request: BaseModel) -> float:
        price = self.config.price(self.config.model(self.name, task))
        tokens_in = estimate_tokens_in(render(task, request))
        return price.tokens_usd(tokens_in, self.config.max_output_tokens(task))

    def _payload(self, task: TaskName, request: BaseModel) -> dict[str, object]:
        return {
            "model": self.config.model(self.name, task),
            "messages": messages(render(task, request)),
            "response_format": response_format(task),
            "max_tokens": self.config.max_output_tokens(task),
            # Only route to upstreams that honour response_format, and never to
            # ones that keep or train on prompts: briefs are client data.
            "provider": {"require_parameters": True, "data_collection": "deny"},
        }

    async def invoke(
        self, task: TaskName, request: BaseModel, *, timeout_s: float
    ) -> tuple[dict[str, object], ProviderCall]:
        if task not in TASKS:
            raise ValueError(f"{self.name} does not serve {task}")
        payload = self._payload(task, request)
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": "Stratum"}
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout_s) as client:
            response = await send(
                client,
                "POST",
                f"{self.base_url}/chat/completions",
                provider=self.name,
                headers=headers,
                payload=payload,
            )
        answer = parse_chat(json_object(response, self.name), self.name)
        model = str(payload["model"])
        cost = answer.reported_cost_usd
        if cost is None:
            cost = self.config.price(model).tokens_usd(answer.tokens_in, answer.tokens_out)
        return answer.result, ProviderCall(
            model=answer.model or model,
            cost_usd=cost,
            latency_ms=round((time.perf_counter() - started) * 1000),
            tokens_in=answer.tokens_in,
            tokens_out=answer.tokens_out,
        )
