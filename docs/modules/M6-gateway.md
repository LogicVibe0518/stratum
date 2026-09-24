# M6 — AI task gateway

**Goal:** every model call in Stratum goes through one typed, measured, fail-safe
path, so swapping providers is a config change and no vendor's bad day is an outage.

## Core (written by hand, not by an agent)
- `tasks.py` — typed tasks with request/result models and latency budgets.
  `edit.parse` gets 8s because it sits on the interactive (later voice) path.
- `providers/base.py` — the `Provider` Protocol. Application code depends on this,
  never on a vendor SDK.
- `providers/fake.py` — a scriptable provider so every failure mode is testable
  with no network and no spend.
- `router.py` — policy as data: provider order per task, plus canary share.
- `breaker.py` — circuit breaker (closed / open / half-open), injectable clock.
- `budget.py` — per-agency daily spend cap, checked before the call.
- `gateway.py` — the orchestration: timeout, retry with jitter, validate, fail over,
  record spend, trace.

## Invariants (tested)
1. A result reaching the caller has always been validated against the task's model.
2. Malformed output is retried once on the same provider, then fails over.
3. A timeout is never retried on the same provider (it would burn the budget twice).
4. An open circuit means the provider is not called at all.
5. Over-budget raises before any provider is invoked, and is never retried.
6. Every attempt is traced: provider, outcome, cost, latency.
7. Spend is tracked per agency per UTC day.

## Deliberately not here
Prompt text, op semantics, model names. Prompts live in the prompt registry, ops are
validated by `stratum_spec`, model choice lives in config so the Model Scout can
change it without touching code.

## Next
Real adapters (OpenRouter, fal, OpenAI), Langfuse tracing, prompt registry, and the
nightly eval that decides which provider leads each task.

## Adapter-layer decisions (ruled 2026-09-25)

The adapters were written to spec by Cursor; these five calls were escalated, plus
one the review found.

**Tracing goes over OTLP, not the Langfuse SDK.** Langfuse's older batch ingestion
API is being retired, OTLP/HTTP is its supported write path, and posting JSON spans
with `httpx` keeps a vendor SDK out of the dependency tree and out of every import
graph but `gateway/tracing.py`. Cost: we hand-build OTLP envelopes. Accepted -> ADR-0005.

**One span per attempt, all attempts under one trace id.** Per-attempt spans are
right: a retry and a failover are exactly what we want to see. But Cursor minted a
fresh trace id per span, so one caption that retried twice and failed over appeared
as three unrelated events and the failover was invisible in the only place we would
look for it. `Gateway.run` now mints a `run_id` once and passes it to every
`tracer.record` call, including the "skipped, circuit open" ones; the tracer uses it
as the OTLP trace id (32 hex chars, as the spec requires). Two tests pin this:
all attempts of one run share an id, separate runs do not.

**`{"_unparseable_output": ...}` instead of raising.** When a model answers with prose
instead of JSON, the adapter returns that marker. Every result model forbids extra
keys, so the gateway's own validation rejects it and takes the invalid-output path -
retry once, charge the cost, then fail over. The alternative, returning `{}`, is a
valid `EditParseResult` with no ops, i.e. "the user's instruction produced no
changes", which is the one wrong answer that looks like a right one. Accepted.

**`strict: false` on the JSON schema.** `EditParseResult.ops` holds free-form objects
by design - the gateway must not know op semantics - and strict mode rejects that
shape. The gateway validates every result against the task model regardless, so
strict mode would buy a second copy of a check we already have. Accepted.

**A capability gap is not a failure.** Declining a reference image raised
`ProviderUnavailable`, which counts towards the circuit breaker: five such requests
would open the circuit on a perfectly healthy vendor and take captions and edit
parsing down with it. Added `TaskNotSupported`, raised before any network call; the
gateway records it as `unsupported`, moves to the next provider, and does not retry
or penalise the breaker.

## Model ids and prices

Verified 2026-09-25 against `openrouter.ai/api/v1/models`,
`developers.openai.com/api/docs/pricing` and `fal.ai`. Two of the five ids Cursor
proposed did not exist: `google/gemini-3.5-flash-lite` (no flash-lite in the current
Gemini line) and `gpt-image-1` (superseded by `gpt-image-2.5-flare`/`-sunburst`).

A wrong model id fails closed - the first call 404s and the router moves on. A wrong
price fails open: the budget cap is the only thing between a retry loop and a real
bill, so prices are rounded up, never down. `gpt-image-1`'s $0.063 was replaced with
$0.025 for `gpt-image-2.5-flare` (token-billed, ~$0.013 at 1024x1024 medium plus
prompt tokens), and fal's $0.004 with $0.006 (schnell bills $0.003/megapixel rounded
up to the next whole megapixel; a 1080x1350 poster is 1.46 MP, so 2 MP).

Known limit, written into the config: both text routes run the same model, so
failover covers a dead route, not a degraded model. A second model belongs in the
`canary` slot once the eval harness can show it is as good - picking one now would
add variance to captions with no evidence behind it.
