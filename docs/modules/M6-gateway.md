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
