"""fal adapter: background.create via fal's queue API.

Submit -> poll status until COMPLETED (within the task's latency budget) -> fetch
the result -> download the image -> return PNG bytes. fal does not report cost, so
it comes from the per-image price in config.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from stratum_worker.gateway.config import GatewayConfig
from stratum_worker.gateway.errors import (
    ProviderTimeout,
    ProviderUnavailable,
    TaskNotSupported,
)
from stratum_worker.gateway.prompts import render
from stratum_worker.gateway.providers._http import json_object, png_result, send, unparseable
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import BackgroundRequest, TaskName

DEFAULT_BASE_URL = "https://queue.fal.run"
POLL_INTERVAL_S = 0.5
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Multiples of 16, close to one megapixel, matching our canvas aspects.
IMAGE_SIZES = {"1:1": (1024, 1024), "4:5": (1024, 1280), "9:16": (768, 1344)}
# URLs in fal's responses are only followed to fal's own hosts, and the API key is
# only sent to the queue host. A tampered response cannot point us at internal
# addresses or collect the key.
API_HOST_SUFFIXES = (".fal.run",)
DOWNLOAD_HOST_SUFFIXES = (".fal.run", ".fal.media", "fal.media")


def _host_allowed(url: str, suffixes: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and any(
        host == suffix.lstrip(".") or host.endswith(suffix) for suffix in suffixes
    )


@dataclass
class FalImagesProvider:
    api_key: str = field(repr=False)
    config: GatewayConfig = field(repr=False)
    name: str = "fal"
    base_url: str = DEFAULT_BASE_URL
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def supports(self, task: TaskName) -> bool:
        return task is TaskName.BACKGROUND_CREATE and self.config.has_model(self.name, task)

    def estimate_usd(self, task: TaskName, request: BaseModel) -> float:
        return self.config.price(self.config.model(self.name, task)).per_image

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.api_key}"}

    def _api_url(self, url: object, what: str) -> str:
        if not isinstance(url, str) or not _host_allowed(url, API_HOST_SUFFIXES):
            raise ProviderUnavailable(f"{self.name}: unexpected {what} URL")
        return url

    async def invoke(
        self, task: TaskName, request: BaseModel, *, timeout_s: float
    ) -> tuple[dict[str, object], ProviderCall]:
        if task is not TaskName.BACKGROUND_CREATE:
            raise ValueError(f"{self.name} does not serve {task}")
        if not isinstance(request, BackgroundRequest):
            raise TypeError(f"{self.name}: expected BackgroundRequest")
        if request.reference_image is not None:
            raise TaskNotSupported(f"{self.name}: reference images are not supported yet")

        model = self.config.model(self.name, task)
        width, height = IMAGE_SIZES[request.aspect]
        payload: dict[str, object] = {
            "prompt": render(task, request).user,
            "image_size": {"width": width, "height": height},
            "num_images": 1,
            "output_format": "png",
            "enable_safety_checker": True,
        }
        started = self.clock()
        deadline = started + timeout_s
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout_s) as client:
            submitted = json_object(
                await send(
                    client,
                    "POST",
                    f"{self.base_url}/{model}",
                    provider=self.name,
                    headers=self._headers(),
                    payload=payload,
                ),
                self.name,
            )
            status_url = self._api_url(submitted.get("status_url"), "status")
            response_url = self._api_url(submitted.get("response_url"), "response")
            await self._wait(client, status_url, deadline)
            output = json_object(
                await send(
                    client, "GET", response_url, provider=self.name, headers=self._headers()
                ),
                self.name,
            )
            result = await self._download(client, output)

        call = ProviderCall(
            model=model,
            cost_usd=self.config.price(model).per_image,
            latency_ms=round((self.clock() - started) * 1000),
        )
        return result, call

    async def _wait(self, client: httpx.AsyncClient, status_url: str, deadline: float) -> None:
        while True:
            status = json_object(
                await send(client, "GET", status_url, provider=self.name, headers=self._headers()),
                self.name,
            )
            state = status.get("status")
            if state == "COMPLETED":
                if status.get("error"):
                    raise ProviderUnavailable(f"{self.name}: {str(status['error'])[:200]}")
                return
            if state not in ("IN_QUEUE", "IN_PROGRESS"):
                raise ProviderUnavailable(f"{self.name}: unknown queue status {state!r}")
            if self.clock() + POLL_INTERVAL_S > deadline:
                raise ProviderTimeout(f"{self.name}: still {state} at the deadline")
            await self.sleep(POLL_INTERVAL_S)

    async def _download(
        self, client: httpx.AsyncClient, output: dict[str, object]
    ) -> dict[str, object]:
        images = output.get("images")
        first = images[0] if isinstance(images, list) and images else None
        url = first.get("url") if isinstance(first, dict) else None
        if not isinstance(url, str):
            return unparseable("result has no image url")
        if not _host_allowed(url, DOWNLOAD_HOST_SUFFIXES):
            raise ProviderUnavailable(f"{self.name}: image URL is not on a fal host")
        image = await send(client, "GET", url, provider=self.name)  # no API key to the CDN
        if len(image.content) > MAX_IMAGE_BYTES:
            return unparseable("image is larger than the download cap")
        return png_result(image.content)
