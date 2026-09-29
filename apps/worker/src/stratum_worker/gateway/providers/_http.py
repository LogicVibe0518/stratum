"""Shared plumbing for HTTP adapters: error mapping and OpenAI-style chat parsing.

Only gateway exceptions leave this module. An httpx error or a vendor error body
never crosses the provider boundary.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import httpx
from PIL import Image, UnidentifiedImageError

from stratum_worker.gateway.errors import (
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from stratum_worker.gateway.prompts import RenderedPrompt
from stratum_worker.gateway.tasks import CUT_OFF, RESULT_TYPES, UNPARSEABLE, TaskName

# unparseable() is returned in place of a result when the model answered with
# something that is not a JSON object (or not an image). Every task result model
# forbids extra keys, so the gateway's validation rejects it and takes its
# invalid-output path: retry once, record the cost, then fail over. Never return {}
# instead - an empty EditParseResult is valid and would silently mean "no changes".
MAX_ERROR_CHARS = 200
CHARS_PER_TOKEN = 4  # rough, for pre-call budget estimates only


def unparseable(detail: str) -> dict[str, object]:
    return {UNPARSEABLE: detail[:MAX_ERROR_CHARS]}


def _vendor_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:MAX_ERROR_CHARS]
    if isinstance(body, dict):
        error = body.get("error", body.get("detail", body))
        if isinstance(error, dict):
            error = error.get("message", error)
        return str(error)[:MAX_ERROR_CHARS]
    return str(body)[:MAX_ERROR_CHARS]


def raise_for_status(response: httpx.Response, provider: str) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = f"{provider}: HTTP {status}: {_vendor_message(response)}"
    if status == 429:
        raise ProviderRateLimited(detail)
    # 5xx is the vendor's problem; other 4xx (bad key, no credit, unknown model)
    # will not fix itself on this provider either, so both mean "try the next one".
    raise ProviderUnavailable(detail)


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
) -> httpx.Response:
    try:
        response = await client.request(method, url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"{provider}: timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"{provider}: {type(exc).__name__}") from exc
    raise_for_status(response, provider)
    return response


def json_object(response: httpx.Response, provider: str) -> dict[str, object]:
    """The HTTP body itself. A broken envelope is a provider fault, not a model one."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderUnavailable(f"{provider}: response body is not JSON") from exc
    if not isinstance(body, dict):
        raise ProviderUnavailable(f"{provider}: response body is not a JSON object")
    return body


# --- OpenAI-compatible chat -------------------------------------------------------


def response_format(task: TaskName) -> dict[str, object]:
    # strict=False: EditParseResult.ops holds free-form objects, which strict mode
    # rejects. The gateway validates every result against the task model anyway.
    return {
        "type": "json_schema",
        "json_schema": {
            "name": task.value.replace(".", "_"),
            "strict": False,
            "schema": RESULT_TYPES[task].model_json_schema(),
        },
    }


def messages(prompt: RenderedPrompt) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]


def estimate_tokens_in(prompt: RenderedPrompt) -> int:
    return (len(prompt.system) + len(prompt.user)) // CHARS_PER_TOKEN + 16


@dataclass(frozen=True)
class ChatAnswer:
    result: dict[str, object]
    tokens_in: int
    tokens_out: int
    reported_cost_usd: float | None
    model: str | None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_chat(body: dict[str, object], provider: str) -> ChatAnswer:
    if "error" in body:
        raise ProviderUnavailable(f"{provider}: {str(body['error'])[:MAX_ERROR_CHARS]}")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderUnavailable(f"{provider}: response has no choices")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None

    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    cost = usage.get("cost")
    answer_model = body.get("model")

    result: dict[str, object]
    if not isinstance(content, str):
        result = unparseable("no text content (refusal or empty answer)")
    else:
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        result = parsed if isinstance(parsed, dict) else unparseable(content)
    if UNPARSEABLE in result and choices[0].get("finish_reason") == "length":
        # Said plainly, because it is not the model misbehaving: the answer did not fit
        # in max_output_tokens, and a retry at the same limit is cut off the same way.
        result = unparseable(CUT_OFF)
    return ChatAnswer(
        result=result,
        tokens_in=_int(usage.get("prompt_tokens")),
        tokens_out=_int(usage.get("completion_tokens")),
        reported_cost_usd=float(cost) if isinstance(cost, int | float) else None,
        model=answer_model if isinstance(answer_model, str) else None,
    )


# --- images -----------------------------------------------------------------------


def png_result(data: bytes) -> dict[str, object]:
    """Decode whatever the vendor sent, re-encode as PNG, and measure it ourselves."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            size = image.size
            if image.format == "PNG":
                png = data
            else:
                buffer = io.BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(buffer, "PNG")
                png = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return unparseable("image bytes could not be decoded")
    return {"image_png": png, "width": size[0], "height": size[1]}
