"""Real adapters against httpx.MockTransport. No network, no spend."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel, ValidationError
from stratum_worker.gateway import (
    BackgroundRequest,
    BackgroundResult,
    Budget,
    CaptionRequest,
    CaptionResult,
    EditParseRequest,
    EditParseResult,
    Gateway,
    InvalidProviderOutput,
    NoProviderAvailable,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    TaskName,
    TaskNotSupported,
)
from stratum_worker.gateway.config import default_config
from stratum_worker.gateway.errors import GatewayError
from stratum_worker.gateway.prompts import load
from stratum_worker.gateway.providers._http import UNPARSEABLE
from stratum_worker.gateway.providers.base import Provider
from stratum_worker.gateway.providers.fal_images import FalImagesProvider
from stratum_worker.gateway.providers.openai_direct import OpenAIProvider
from stratum_worker.gateway.providers.openrouter import OpenRouterProvider
from stratum_worker.gateway.providers.registry import build_providers, build_router
from stratum_worker.gateway.tasks import RESULT_TYPES

CONFIG = default_config()
KEY = "sk-test-not-a-real-key"
CAPTION = TaskName.CAPTION_GENERATE
EDIT = TaskName.EDIT_PARSE
BACKGROUND = TaskName.BACKGROUND_CREATE
GOOD_CAPTION = {"caption": "Boiler serviced before winter?", "hashtags": ["#plumbing"]}
GOOD_EDIT = {"ops": [{"op": "set_text", "layer": "headline", "text": "Hi"}], "needs_human": None}

Handler = Callable[[httpx.Request], httpx.Response]


def _requests() -> dict[TaskName, BaseModel]:
    return {
        CAPTION: CaptionRequest(brief="Winter boiler checks, book now", client_name="Northgate"),
        EDIT: EditParseRequest(
            message="make the headline say Hi",
            layers={"headline": "text"},
            palette=["palette.primary"],
        ),
        BACKGROUND: BackgroundRequest(prompt="a clean modern kitchen", aspect="4:5"),
    }


def _png(width: int = 64, height: int = 80, fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, fmt)
    return buffer.getvalue()


def _chat_body(content: object, usage: dict[str, object] | None = None) -> dict[str, object]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": "gen-1",
        "model": "served/model",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": usage if usage is not None else {"prompt_tokens": 1000, "completion_tokens": 200},
    }


def _openrouter(handler: Handler) -> OpenRouterProvider:
    return OpenRouterProvider(api_key=KEY, config=CONFIG, transport=httpx.MockTransport(handler))


def _openai(handler: Handler) -> OpenAIProvider:
    return OpenAIProvider(api_key=KEY, config=CONFIG, transport=httpx.MockTransport(handler))


async def _no_sleep(_: float) -> None:
    return None


def _fal(handler: Handler, clock: Callable[[], float] | None = None) -> FalImagesProvider:
    provider = FalImagesProvider(
        api_key=KEY, config=CONFIG, transport=httpx.MockTransport(handler), sleep=_no_sleep
    )
    if clock is not None:
        provider.clock = clock
    return provider


QUEUE = "https://queue.fal.run/fal-ai/flux/schnell"
STATUS_URL = f"{QUEUE}/requests/r1/status"
RESPONSE_URL = f"{QUEUE}/requests/r1"
IMAGE_URL = "https://v3.fal.media/files/r1.png"


def _fal_happy(
    image: bytes | None = None, statuses: list[str] | None = None, image_url: str = IMAGE_URL
) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    pending = list(statuses or ["IN_QUEUE", "IN_PROGRESS", "COMPLETED"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if request.method == "POST" and url == QUEUE:
            body = {"request_id": "r1", "status_url": STATUS_URL, "response_url": RESPONSE_URL}
            return httpx.Response(200, json=body)
        if url == STATUS_URL:
            state = pending.pop(0) if len(pending) > 1 else pending[0]
            return httpx.Response(200, json={"status": state})
        if url == RESPONSE_URL:
            return httpx.Response(200, json={"images": [{"url": image_url}]})
        if url == image_url:
            return httpx.Response(200, content=image if image is not None else _png())
        return httpx.Response(404)

    return handler, seen


# --- every adapter x task: the error mapping ------------------------------------

ADAPTERS: list[tuple[str, TaskName]] = [
    ("openrouter", CAPTION),
    ("openrouter", EDIT),
    ("openai", CAPTION),
    ("openai", EDIT),
    ("openai", BACKGROUND),
    ("fal", BACKGROUND),
]


def _build(name: str, handler: Handler) -> Provider:
    if name == "openrouter":
        return _openrouter(handler)
    if name == "openai":
        return _openai(handler)
    return _fal(handler)


def _status(code: int, body: object = None) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json=body if body is not None else {"error": {"message": "x"}})

    return handler


def _raises(exc: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


ERROR_CASES: list[tuple[str, Handler, type[GatewayError]]] = [
    ("429", _status(429), ProviderRateLimited),
    ("500", _status(500), ProviderUnavailable),
    ("503", _status(503), ProviderUnavailable),
    ("401", _status(401), ProviderUnavailable),
    ("timeout", _raises(httpx.ReadTimeout("slow")), ProviderTimeout),
    ("connect-timeout", _raises(httpx.ConnectTimeout("slow")), ProviderTimeout),
    ("network", _raises(httpx.ConnectError("refused")), ProviderUnavailable),
    ("non-json-body", lambda _: httpx.Response(200, text="<html>oops"), ProviderUnavailable),
    ("json-array-body", lambda _: httpx.Response(200, json=[1, 2]), ProviderUnavailable),
]


@pytest.mark.parametrize(("name", "task"), ADAPTERS)
@pytest.mark.parametrize(("case", "handler", "expected"), ERROR_CASES)
async def test_errors_map_to_gateway_exceptions(
    name: str, task: TaskName, case: str, handler: Handler, expected: type[GatewayError]
) -> None:
    provider = _build(name, handler)
    with pytest.raises(expected) as caught:
        await provider.invoke(task, _requests()[task], timeout_s=5.0)
    assert isinstance(caught.value, GatewayError)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(("name", "task"), ADAPTERS)
def test_adapters_satisfy_the_protocol_and_hide_keys(name: str, task: TaskName) -> None:
    provider = _build(name, _status(200))
    assert isinstance(provider, Provider)
    assert provider.supports(task)
    assert provider.estimate_usd(task, _requests()[task]) > 0
    assert KEY not in repr(provider)


def test_unsupported_tasks_are_declined() -> None:
    assert not _openrouter(_status(200)).supports(BACKGROUND)
    assert not _fal(_status(200)).supports(CAPTION)
    assert not _fal(_status(200)).supports(EDIT)


# --- chat adapters ---------------------------------------------------------------


@pytest.mark.parametrize("build", [_openrouter, _openai])
@pytest.mark.parametrize(("task", "good"), [(CAPTION, GOOD_CAPTION), (EDIT, GOOD_EDIT)])
async def test_chat_success_validates(
    build: Callable[[Handler], Provider], task: TaskName, good: dict[str, object]
) -> None:
    provider = build(lambda _: httpx.Response(200, json=_chat_body(good)))
    raw, call = await provider.invoke(task, _requests()[task], timeout_s=5.0)
    assert RESULT_TYPES[task].model_validate(raw)
    assert call.tokens_in == 1000 and call.tokens_out == 200
    assert call.cost_usd > 0


@pytest.mark.parametrize("build", [_openrouter, _openai])
@pytest.mark.parametrize("content", ["not json at all", "[1, 2, 3]", '{"caption": '])
async def test_malformed_model_json_is_returned_as_invalid_output(
    build: Callable[[Handler], Provider], content: str
) -> None:
    provider = build(lambda _: httpx.Response(200, json=_chat_body(content)))
    raw, call = await provider.invoke(EDIT, _requests()[EDIT], timeout_s=5.0)
    assert UNPARSEABLE in raw
    with pytest.raises(ValidationError):
        EditParseResult.model_validate(raw)
    assert call.cost_usd > 0, "a bad answer still costs money"


def test_unparseable_marker_fails_every_result_model() -> None:
    """The adapters rely on this: {} would be a valid, empty EditParseResult."""
    for result_type in RESULT_TYPES.values():
        with pytest.raises(ValidationError):
            result_type.model_validate({UNPARSEABLE: "x"})


async def test_openrouter_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_chat_body(GOOD_CAPTION))

    await _openrouter(handler).invoke(CAPTION, _requests()[CAPTION], timeout_s=5.0)

    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == CONFIG.model("openrouter", CAPTION)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == CaptionResult.model_json_schema()
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
    assert body["messages"][0]["content"] == load(CAPTION).system  # from the .md file
    assert "Winter boiler checks" in body["messages"][1]["content"]


async def test_openrouter_uses_reported_cost_when_present() -> None:
    usage: dict[str, object] = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0123}
    provider = _openrouter(lambda _: httpx.Response(200, json=_chat_body(GOOD_CAPTION, usage)))
    _, call = await provider.invoke(CAPTION, _requests()[CAPTION], timeout_s=5.0)
    assert call.cost_usd == pytest.approx(0.0123)


async def test_openrouter_estimates_cost_from_price_table_otherwise() -> None:
    provider = _openrouter(lambda _: httpx.Response(200, json=_chat_body(GOOD_CAPTION)))
    _, call = await provider.invoke(CAPTION, _requests()[CAPTION], timeout_s=5.0)
    price = CONFIG.price(CONFIG.model("openrouter", CAPTION))
    assert call.cost_usd == pytest.approx(price.tokens_usd(1000, 200))


async def test_openai_chat_request_shape_and_cost() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_chat_body(GOOD_EDIT))

    _, call = await _openai(handler).invoke(EDIT, _requests()[EDIT], timeout_s=5.0)
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert body["model"] == CONFIG.model("openai", EDIT)
    assert "max_completion_tokens" in body and "temperature" not in body
    price = CONFIG.price(CONFIG.model("openai", EDIT))
    assert call.cost_usd == pytest.approx(price.tokens_usd(1000, 200))


async def test_chat_refusal_is_invalid_output() -> None:
    body = _chat_body(GOOD_CAPTION)
    body["choices"] = [{"message": {"role": "assistant", "content": None, "refusal": "no"}}]
    provider = _openai(lambda _: httpx.Response(200, json=body))
    raw, _ = await provider.invoke(CAPTION, _requests()[CAPTION], timeout_s=5.0)
    assert UNPARSEABLE in raw


async def test_chat_body_without_choices_is_unavailable() -> None:
    provider = _openrouter(lambda _: httpx.Response(200, json={"id": "x", "choices": []}))
    with pytest.raises(ProviderUnavailable, match="no choices"):
        await provider.invoke(CAPTION, _requests()[CAPTION], timeout_s=5.0)


# --- openai images ---------------------------------------------------------------


async def test_openai_image_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]})

    raw, call = await _openai(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=5.0)
    result = BackgroundResult.model_validate(raw)
    assert result.image_png.startswith(b"\x89PNG") and (result.width, result.height) == (64, 80)
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.openai.com/v1/images/generations"
    assert body["size"] == "1024x1536"
    assert "no text" in body["prompt"].lower()
    assert call.cost_usd == CONFIG.price("gpt-image-2.5-flare").per_image


@pytest.mark.parametrize(
    "item", [{"b64_json": "!!!not base64!!!"}, {"b64_json": base64.b64encode(b"junk").decode()}]
)
async def test_openai_bad_image_is_invalid_output(item: dict[str, str]) -> None:
    provider = _openai(lambda _: httpx.Response(200, json={"data": [item]}))
    raw, _ = await provider.invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=5.0)
    assert UNPARSEABLE in raw


async def test_openai_image_without_payload_is_unavailable() -> None:
    provider = _openai(lambda _: httpx.Response(200, json={"data": []}))
    with pytest.raises(ProviderUnavailable):
        await provider.invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=5.0)


async def test_reference_image_is_declined_not_ignored() -> None:
    """TaskNotSupported, not ProviderUnavailable: the vendor is healthy, it just
    cannot do this yet, so the decline must not count towards its breaker."""
    request = BackgroundRequest(prompt="kitchen", reference_image=_png())
    for provider in (_openai(_status(200)), _fal(_status(200))):
        with pytest.raises(TaskNotSupported, match="reference images"):
            await provider.invoke(BACKGROUND, request, timeout_s=5.0)


# --- fal ---------------------------------------------------------------------------


async def test_fal_polls_until_complete_and_returns_png() -> None:
    handler, seen = _fal_happy()
    raw, call = await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)

    result = BackgroundResult.model_validate(raw)
    assert result.image_png.startswith(b"\x89PNG")
    assert [str(r.url) for r in seen].count(STATUS_URL) == 3
    submit = json.loads(seen[0].content)
    assert submit["image_size"] == {"width": 1024, "height": 1280}
    assert submit["output_format"] == "png"
    assert seen[0].headers["Authorization"] == f"Key {KEY}"
    assert "Authorization" not in seen[-1].headers, "the API key must not go to the CDN"
    assert call.cost_usd == CONFIG.price("fal-ai/flux/schnell").per_image


async def test_fal_converts_jpeg_to_png() -> None:
    handler, _ = _fal_happy(image=_png(fmt="JPEG"))
    raw, _ = await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)
    assert BackgroundResult.model_validate(raw).image_png.startswith(b"\x89PNG")


async def test_fal_times_out_while_queued() -> None:
    now = [0.0]

    async def advancing_sleep(seconds: float) -> None:
        now[0] += seconds

    handler, _ = _fal_happy(statuses=["IN_QUEUE"])
    provider = _fal(handler, clock=lambda: now[0])
    provider.sleep = advancing_sleep
    with pytest.raises(ProviderTimeout):
        await provider.invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=3.0)
    assert now[0] <= 3.0


async def test_fal_failed_job_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"status_url": STATUS_URL, "response_url": RESPONSE_URL}
            )
        return httpx.Response(200, json={"status": "COMPLETED", "error": "NSFW detected"})

    with pytest.raises(ProviderUnavailable, match="NSFW"):
        await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)


async def test_fal_refuses_image_urls_off_fal_hosts() -> None:
    handler, seen = _fal_happy(image_url="https://169.254.169.254/latest/meta-data")
    with pytest.raises(ProviderUnavailable, match="not on a fal host"):
        await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)
    assert not any("169.254" in str(r.url) for r in seen)


async def test_fal_refuses_status_urls_off_fal_hosts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status_url": "http://evil.example/s", "response_url": RESPONSE_URL}
        )

    with pytest.raises(ProviderUnavailable, match="unexpected status URL"):
        await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)


async def test_fal_undecodable_image_is_invalid_output() -> None:
    handler, _ = _fal_happy(image=b"definitely not an image")
    raw, _ = await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)
    assert UNPARSEABLE in raw


async def test_fal_malformed_status_json_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"status_url": STATUS_URL, "response_url": RESPONSE_URL}
            )
        return httpx.Response(200, text="{not json")

    with pytest.raises(ProviderUnavailable, match="not JSON"):
        await _fal(handler).invoke(BACKGROUND, _requests()[BACKGROUND], timeout_s=30.0)


# --- registry and the whole gateway ---------------------------------------------


def test_registry_only_builds_providers_with_keys() -> None:
    providers = build_providers(CONFIG, {"OPENROUTER_API_KEY": "k", "FAL_KEY": " "})
    assert set(providers) == {"openrouter"}
    assert build_providers(CONFIG, {}) == {}


async def test_gateway_end_to_end_with_real_adapter_records_cost() -> None:
    usage: dict[str, object] = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.002}
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=_chat_body(GOOD_CAPTION, usage))
    )
    router = build_router(CONFIG, {"OPENROUTER_API_KEY": KEY}, transport=transport)
    gateway = Gateway(router=router, budget=Budget(daily_cap_usd=1.0))

    result = await gateway.run(CAPTION, _requests()[CAPTION], agency_id="nimble")

    assert isinstance(result, CaptionResult)
    assert gateway.budget.spent_today("nimble") == pytest.approx(0.002)


async def test_gateway_treats_malformed_model_json_as_invalid_output() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_body("sorry, I can't do JSON"))

    router = build_router(
        CONFIG, {"OPENROUTER_API_KEY": KEY}, transport=httpx.MockTransport(handler)
    )
    gateway = Gateway(router=router, budget=Budget(daily_cap_usd=1.0))

    with pytest.raises(NoProviderAvailable) as caught:
        await gateway.run(EDIT, _requests()[EDIT], agency_id="nimble")
    assert isinstance(caught.value.__cause__, InvalidProviderOutput)
    assert len(calls) == 2, "invalid output is retried once on the same provider"
    assert gateway.budget.spent_today("nimble") > 0
