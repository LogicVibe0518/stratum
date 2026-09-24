"""OpenAI adapter: caption.generate and edit.parse via chat completions, and
background.create via the image generation endpoint. OpenAI does not report cost,
so it is computed from token usage (or per image) with the config price table."""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel

from stratum_worker.gateway.config import GatewayConfig
from stratum_worker.gateway.errors import ProviderUnavailable, TaskNotSupported
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.providers._http import (
    estimate_tokens_in,
    json_object,
    messages,
    parse_chat,
    png_result,
    response_format,
    send,
    unparseable,
)
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import BackgroundRequest, TaskName

DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHAT_TASKS = frozenset({TaskName.CAPTION_GENERATE, TaskName.EDIT_PARSE})
TASKS = CHAT_TASKS | {TaskName.BACKGROUND_CREATE}
# The image endpoint offers few sizes; the compositor cover-crops to the canvas.
IMAGE_SIZES = {"1:1": "1024x1024", "4:5": "1024x1536", "9:16": "1024x1536"}


@dataclass
class OpenAIProvider:
    api_key: str = field(repr=False)
    config: GatewayConfig = field(repr=False)
    name: str = "openai"
    base_url: str = DEFAULT_BASE_URL
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def supports(self, task: TaskName) -> bool:
        return task in TASKS and self.config.has_model(self.name, task)

    def estimate_usd(self, task: TaskName, request: BaseModel) -> float:
        price = self.config.price(self.config.model(self.name, task))
        if task is TaskName.BACKGROUND_CREATE:
            return price.per_image
        tokens_in = estimate_tokens_in(render(task, request))
        return price.tokens_usd(tokens_in, self.config.max_output_tokens(task))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def invoke(
        self, task: TaskName, request: BaseModel, *, timeout_s: float
    ) -> tuple[dict[str, object], ProviderCall]:
        if task not in TASKS:
            raise ValueError(f"{self.name} does not serve {task}")
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout_s) as client:
            if task is TaskName.BACKGROUND_CREATE:
                result, call = await self._image(client, request)
            else:
                result, call = await self._chat(client, task, request)
        latency = round((time.perf_counter() - started) * 1000)
        return result, call.model_copy(update={"latency_ms": latency})

    async def _chat(
        self, client: httpx.AsyncClient, task: TaskName, request: BaseModel
    ) -> tuple[dict[str, object], ProviderCall]:
        model = self.config.model(self.name, task)
        payload: dict[str, object] = {
            "model": model,
            "messages": messages(render(task, request)),
            "response_format": response_format(task),
            "max_completion_tokens": self.config.max_output_tokens(task),
        }
        response = await send(
            client,
            "POST",
            f"{self.base_url}/chat/completions",
            provider=self.name,
            headers=self._headers(),
            payload=payload,
        )
        answer = parse_chat(json_object(response, self.name), self.name)
        cost = self.config.price(model).tokens_usd(answer.tokens_in, answer.tokens_out)
        return answer.result, ProviderCall(
            model=answer.model or model,
            cost_usd=cost,
            tokens_in=answer.tokens_in,
            tokens_out=answer.tokens_out,
        )

    async def _image(
        self, client: httpx.AsyncClient, request: BaseModel
    ) -> tuple[dict[str, object], ProviderCall]:
        if not isinstance(request, BackgroundRequest):
            raise TypeError(f"{self.name}: expected BackgroundRequest")
        if request.reference_image is not None:
            raise TaskNotSupported(f"{self.name}: reference images are not supported yet")
        model = self.config.model(self.name, TaskName.BACKGROUND_CREATE)
        payload: dict[str, object] = {
            "model": model,
            "prompt": render(TaskName.BACKGROUND_CREATE, request).user,
            "size": IMAGE_SIZES[request.aspect],
            "n": 1,
        }
        response = await send(
            client,
            "POST",
            f"{self.base_url}/images/generations",
            provider=self.name,
            headers=self._headers(),
            payload=payload,
        )
        body = json_object(response, self.name)
        data = body.get("data")
        first = data[0] if isinstance(data, list) and data else None
        encoded = first.get("b64_json") if isinstance(first, dict) else None
        if not isinstance(encoded, str):
            raise ProviderUnavailable(f"{self.name}: image response has no b64_json")
        try:
            result = png_result(base64.b64decode(encoded, validate=True))
        except binascii.Error:
            result = unparseable("b64_json is not valid base64")
        call = ProviderCall(model=model, cost_usd=self.config.price(model).per_image)
        return result, call
