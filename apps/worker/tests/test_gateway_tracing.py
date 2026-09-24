from __future__ import annotations

import base64
import json
import logging
import warnings

import httpx
import pytest
from stratum_worker.gateway import Budget, CaptionRequest, Gateway, NullTracer, Router, TaskName
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.providers.fake import FakeProvider
from stratum_worker.gateway.router import TaskPolicy
from stratum_worker.gateway.tracing import OTLP_PATH, LangfuseTracer, tracer_from_env

PK, SK = "pk-lf-test", "sk-lf-test"


def _collector(status: int = 200) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    return httpx.MockTransport(handler), seen


def _tracer(transport: httpx.MockTransport) -> LangfuseTracer:
    return LangfuseTracer(
        public_key=PK,
        secret_key=SK,
        base_url="https://langfuse.example",
        transport=transport,
        background=False,
    )


def _attrs(request: httpx.Request) -> list[dict[str, object]]:
    body = json.loads(request.content)
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    return [{a["key"]: next(iter(a["value"].values())) for a in s["attributes"]} for s in spans]


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LANGFUSE_PUBLIC_KEY": PK},
        {"LANGFUSE_SECRET_KEY": SK},
        {"LANGFUSE_PUBLIC_KEY": " ", "LANGFUSE_SECRET_KEY": " "},
    ],
)
def test_missing_langfuse_keys_degrade_to_null_tracer_silently(
    env: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with caplog.at_level(logging.DEBUG):
            tracer = tracer_from_env(env)
            tracer.record(
                run_id="0" * 32,
                task=TaskName.CAPTION_GENERATE,
                provider="p",
                call=None,
                outcome="ok",
                agency_id="nimble",
            )
    assert isinstance(tracer, NullTracer)
    assert caplog.records == []


def test_keys_present_give_a_langfuse_tracer() -> None:
    tracer = tracer_from_env({"LANGFUSE_PUBLIC_KEY": PK, "LANGFUSE_SECRET_KEY": SK})
    assert isinstance(tracer, LangfuseTracer)
    assert SK not in repr(tracer) and PK not in repr(tracer)


def test_record_does_no_io_and_flush_exports_otlp() -> None:
    transport, seen = _collector()
    tracer = _tracer(transport)
    call = ProviderCall(
        model="vendor/m", cost_usd=0.0042, latency_ms=900, tokens_in=10, tokens_out=3
    )

    tracer.record(
        run_id="a" * 32,
        task=TaskName.CAPTION_GENERATE,
        provider="openrouter",
        call=call,
        outcome="ok",
        agency_id="a1",
    )
    assert seen == [], "record() must only queue"
    assert tracer.flush() == 1

    request = seen[0]
    assert str(request.url) == "https://langfuse.example" + OTLP_PATH
    assert request.headers["x-langfuse-ingestion-version"] == "4"
    user, password = (
        base64.b64decode(request.headers["Authorization"].split()[1]).decode().split(":")
    )
    assert (user, password) == (PK, SK)
    [attrs] = _attrs(request)
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["langfuse.observation.model.name"] == "vendor/m"
    assert json.loads(str(attrs["langfuse.observation.cost_details"])) == {"total": 0.0042}
    assert attrs["langfuse.user.id"] == "a1"


def test_export_failure_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    transport, _ = _collector(status=500)
    tracer = _tracer(transport)
    tracer.record(
        run_id="b" * 32,
        task=TaskName.EDIT_PARSE,
        provider="p",
        call=None,
        outcome="timeout",
        agency_id="a1",
    )
    with caplog.at_level(logging.WARNING):
        assert tracer.flush() == 0
    assert tracer.dropped == 1
    assert "langfuse export failed" in caplog.text


def test_unreachable_langfuse_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    tracer = _tracer(httpx.MockTransport(handler))
    tracer.record(
        run_id="c" * 32,
        task=TaskName.EDIT_PARSE,
        provider="p",
        call=None,
        outcome="ok",
        agency_id="a",
    )
    assert tracer.flush() == 0


async def test_gateway_attempts_all_reach_langfuse() -> None:
    transport, seen = _collector()
    tracer = _tracer(transport)
    good = FakeProvider(name="up", results=[{"caption": "Hi"}])
    router = Router(
        providers={"up": good},
        policies={TaskName.CAPTION_GENERATE: TaskPolicy(order=("up",))},
    )
    gateway = Gateway(router=router, budget=Budget(daily_cap_usd=1.0), tracer=tracer)

    await gateway.run(
        TaskName.CAPTION_GENERATE, CaptionRequest(brief="b", client_name="c"), agency_id="a1"
    )

    assert tracer.flush() == 1
    [attrs] = _attrs(seen[0])
    assert attrs["langfuse.observation.metadata.outcome"] == "ok"
    assert attrs["langfuse.observation.metadata.provider"] == "up"
