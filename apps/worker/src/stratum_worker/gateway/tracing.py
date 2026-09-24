"""Langfuse tracing for the gateway.

Spans are sent as OTLP/HTTP JSON to Langfuse's OpenTelemetry endpoint using httpx,
so no vendor SDK is imported outside gateway/providers/. (Langfuse's older batch
ingestion API is being shut down; OTLP is its supported write path.)

`record()` is called inside the gateway's async code, so it never does I/O: it only
queues a span. A daemon thread exports batches. Export failures are logged and the
batch is dropped - tracing must never take down a model call.

Only call metadata is sent (task, provider, model, tokens, cost, latency, outcome,
agency id). Prompts, briefs and images are not.
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import queue
import secrets
import threading
import time
from collections.abc import Callable, Mapping

import httpx

from stratum_worker.gateway.gateway import NullTracer, Tracer
from stratum_worker.gateway.providers.base import ProviderCall
from stratum_worker.gateway.tasks import TaskName

DEFAULT_BASE_URL = "https://cloud.langfuse.com"
OTLP_PATH = "/api/public/otel/v1/traces"
BATCH_SIZE = 50
FLUSH_INTERVAL_S = 2.0
MAX_QUEUE = 10_000
EXPORT_TIMEOUT_S = 10.0
SPAN_KIND_CLIENT = 3
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2

AttrValue = str | int | float | bool
Span = dict[str, object]

logger = logging.getLogger(__name__)


def tracer_from_env(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> Tracer:
    """A LangfuseTracer if both keys are set, otherwise a NullTracer. Never raises."""
    env = os.environ if env is None else env
    public_key = env.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = env.get("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        return NullTracer()
    base_url = (
        env.get("LANGFUSE_BASE_URL", "").strip()
        or env.get("LANGFUSE_HOST", "").strip()
        or DEFAULT_BASE_URL
    )
    return LangfuseTracer(
        public_key=public_key, secret_key=secret_key, base_url=base_url, transport=transport
    )


def _attr(key: str, value: AttrValue) -> dict[str, object]:
    if isinstance(value, bool):
        wrapped: dict[str, object] = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}  # OTLP JSON encodes int64 as a string
    elif isinstance(value, float):
        wrapped = {"doubleValue": value}
    else:
        wrapped = {"stringValue": value}
    return {"key": key, "value": wrapped}


def _level(outcome: str) -> tuple[str, int]:
    if outcome == "ok":
        return "DEFAULT", STATUS_OK
    if outcome.startswith("skipped"):
        return "WARNING", STATUS_UNSET
    return "ERROR", STATUS_ERROR


class LangfuseTracer:
    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        background: bool = True,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": "4",
        }
        self._url = base_url.rstrip("/") + OTLP_PATH
        self._transport = transport
        self._background = background
        self._clock_ns = clock_ns
        self._queue: queue.Queue[Span] = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.dropped = 0

    def __repr__(self) -> str:
        return f"LangfuseTracer(url={self._url!r})"  # never the keys

    def record(
        self,
        *,
        run_id: str,
        task: TaskName,
        provider: str,
        call: ProviderCall | None,
        outcome: str,
        agency_id: str,
        error: str | None = None,
    ) -> None:
        span = self._span(run_id, task, provider, call, outcome, agency_id, error)
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            self.dropped += 1
            return
        if self._background:
            self._ensure_worker()

    def _span(
        self,
        run_id: str,
        task: TaskName,
        provider: str,
        call: ProviderCall | None,
        outcome: str,
        agency_id: str,
        error: str | None,
    ) -> Span:
        end = self._clock_ns()
        start = end - (call.latency_ms if call else 0) * 1_000_000
        level, status_code = _level(outcome)
        attrs: list[dict[str, object]] = [
            _attr("langfuse.observation.type", "generation"),
            _attr("langfuse.trace.name", str(task)),
            _attr("langfuse.user.id", agency_id),
            _attr("langfuse.trace.metadata.agency_id", agency_id),
            _attr("langfuse.observation.level", level),
            _attr("langfuse.observation.metadata.provider", provider),
            _attr("langfuse.observation.metadata.outcome", outcome),
        ]
        if call is not None:
            attrs += [
                _attr("langfuse.observation.model.name", call.model),
                _attr(
                    "langfuse.observation.usage_details",
                    json.dumps({"input": call.tokens_in, "output": call.tokens_out}),
                ),
                _attr("langfuse.observation.cost_details", json.dumps({"total": call.cost_usd})),
            ]
        if error:
            attrs.append(_attr("langfuse.observation.status_message", error[:500]))
        return {
            # The gateway's run id IS the trace id (32 hex chars, as OTLP requires),
            # so every attempt of one logical call lands in one Langfuse trace.
            "traceId": run_id,
            "spanId": secrets.token_hex(8),
            "name": f"{task} {provider}",
            "kind": SPAN_KIND_CLIENT,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(end),
            "attributes": attrs,
            "status": {"code": status_code, "message": (error or "")[:500]},
        }

    def _payload(self, spans: list[Span]) -> dict[str, object]:
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", "stratum-worker")]},
                    "scopeSpans": [{"scope": {"name": "stratum.gateway"}, "spans": spans}],
                }
            ]
        }

    def flush(self) -> int:
        """Export everything queued. Returns the number of spans accepted. Never raises."""
        sent = 0
        with httpx.Client(transport=self._transport, timeout=EXPORT_TIMEOUT_S) as client:
            while True:
                batch: list[Span] = []
                while len(batch) < BATCH_SIZE:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                if not batch:
                    return sent
                try:
                    response = client.post(
                        self._url, headers=self._headers, json=self._payload(batch)
                    )
                    response.raise_for_status()
                    sent += len(batch)
                except httpx.HTTPError as exc:
                    self.dropped += len(batch)
                    logger.warning(
                        "langfuse export failed; dropped %d spans",
                        len(batch),
                        extra={"error": type(exc).__name__, "dropped": len(batch)},
                    )

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        with self._lock:
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._run, name="langfuse-export", daemon=True)
            self._worker.start()
            atexit.register(self.close)

    def _run(self) -> None:
        while not self._stop.wait(FLUSH_INTERVAL_S):
            self.flush()

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=EXPORT_TIMEOUT_S)
        self.flush()


__all__ = ["LangfuseTracer", "tracer_from_env"]
