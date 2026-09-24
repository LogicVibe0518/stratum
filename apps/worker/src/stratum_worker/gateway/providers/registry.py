"""Build the real providers and a Router from config plus API keys.

This is the one place application code may import from providers/ (besides the
Protocol in base.py). A provider whose key is missing is simply not registered, and
the router skips it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from stratum_worker.gateway.config import GatewayConfig
from stratum_worker.gateway.providers.base import Provider
from stratum_worker.gateway.providers.fal_images import FalImagesProvider
from stratum_worker.gateway.providers.openai_direct import OpenAIProvider
from stratum_worker.gateway.providers.openrouter import OpenRouterProvider
from stratum_worker.gateway.router import Router

API_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "fal": "FAL_KEY",
}


def build_providers(
    config: GatewayConfig,
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Provider]:
    env = os.environ if env is None else env
    keys = {name: env.get(var, "").strip() for name, var in API_KEY_ENV.items()}
    providers: dict[str, Provider] = {}
    if keys["openrouter"]:
        providers["openrouter"] = OpenRouterProvider(
            api_key=keys["openrouter"], config=config, transport=transport
        )
    if keys["openai"]:
        providers["openai"] = OpenAIProvider(
            api_key=keys["openai"], config=config, transport=transport
        )
    if keys["fal"]:
        providers["fal"] = FalImagesProvider(
            api_key=keys["fal"], config=config, transport=transport
        )
    return providers


def build_router(
    config: GatewayConfig,
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Router:
    return Router(
        providers=build_providers(config, env, transport=transport),
        policies=config.policies(),
    )
