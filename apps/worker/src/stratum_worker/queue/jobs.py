"""What a job is, and every decision about one that needs no database.

Kept pure so the rules - how long to back off, when to give up, what "failed" means
for a job whose worker vanished - are tested once and shared by the real ledger, the
SQLite stand-in and the API's read path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from stratum_worker.app.supa import Supa

DRAFT_POST = "draft_post"
Status = Literal["queued", "running", "done", "failed"]

BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 300.0
ERROR_MAX_CHARS = 1000


class ClientFactory(Protocol):
    """A claimed job -> a database client that can see that job's agency and nothing else.

    The queue calls this once per job and does ALL of the job's work through what it
    returns. It is handed the whole job, not just an agency id, because the real
    implementation (identity.ScopedClientFactory) acts as the person who asked for
    the job and keeps its credential alive only as long as the job's lease.
    """

    def __call__(self, job: Job) -> Supa: ...


class Job(BaseModel):
    """One row of stratum.jobs, as claim_job returned it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    agency_id: str
    kind: str
    payload: dict[str, Any]
    status: Status
    attempts: int
    max_attempts: int
    last_error: str = ""
    leased_by: str | None = None
    leased_until: datetime | None = None
    # Stamped by the database from auth.uid() (migration 20260927174950), so it
    # cannot be chosen by whoever wrote the job. The worker acts as this person.
    requested_by: str | None = None
    run_after: datetime
    created_at: datetime
    # claim_job stamps updated_at with the database's now(), so on a freshly claimed
    # job this is the claim time on the DATABASE clock - the reference every
    # timestamp this worker writes back is measured from, so laptop clock skew never
    # makes a retry due early or a queue wait negative.
    updated_at: datetime


def idempotency_key(client_id: str, slug: str) -> str:
    return f"{client_id}:{slug}"


def backoff_seconds(attempts: int) -> float:
    """5s, 10s, 20s ... capped at five minutes. `attempts` counts the one that failed."""
    return float(min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1)))


def describe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:ERROR_MAX_CHARS]


def done_values(result: dict[str, Any]) -> dict[str, Any]:
    return {"status": "done", "result": result, "last_error": "", "leased_until": None}


def failed_values(
    job: Job, error: str, *, elapsed_s: float, permanent: bool = False
) -> dict[str, Any]:
    """Back to the queue with a delay, or failed for good once attempts run out.

    claim_job already counted this attempt, so `job.attempts` is the number made so
    far including this one. run_after is measured from the claim time on the database
    clock plus how long this worker spent, which is the database's now() give or take
    a network round trip.
    """
    if permanent or job.attempts >= job.max_attempts:
        return {"status": "failed", "last_error": error, "leased_until": None}
    retry_at = job.updated_at + timedelta(seconds=elapsed_s + backoff_seconds(job.attempts))
    return {
        "status": "queued",
        "last_error": error,
        "run_after": retry_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "leased_until": None,
    }


# --- the read side, run as the signed-in user --------------------------------------

LOST_ON_FINAL_ATTEMPT = (
    "the worker stopped during the final attempt and the lease ran out; "
    "claim_job will not start it again"
)


def _parse(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def describe(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The job as a person should see it.

    One case needs interpreting. A worker killed during the LAST allowed attempt
    leaves the row 'running' with attempts == max_attempts; claim_job skips it forever
    and nothing ever writes a last_error. Reporting that as "running" would be a lie
    that never ends, so it is reported as failed. This is a read, not a repair: the
    row is not touched, and no sweeper is involved.
    """
    status = str(row.get("status", ""))
    last_error = str(row.get("last_error") or "")
    leased_until = _parse(row.get("leased_until"))
    attempts = int(row.get("attempts") or 0)
    max_attempts = int(row.get("max_attempts") or 0)
    if (
        status == "running"
        and leased_until is not None
        and leased_until < now
        and attempts >= max_attempts
    ):
        status = "failed"
        last_error = last_error or LOST_ON_FINAL_ATTEMPT
    payload = row.get("payload") or {}
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "status": status,
        "attempts": attempts,
        "max_attempts": max_attempts,
        "last_error": last_error,
        "result": row.get("result"),
        "post_id": payload.get("post_id") if isinstance(payload, dict) else None,
    }


async def find(client: Supa, agency_id: str, key: str) -> dict[str, Any] | None:
    rows = await client.select(
        "jobs",
        agency_id=f"eq.{agency_id}",
        idempotency_key=f"eq.{key}",
        # The whole row, because the caller has to be able to ask describe() whether
        # this job has given up - which needs attempts, leased_until and max_attempts,
        # not just the status column. One row by a unique key; the columns are free.
        select="*",
    )
    return rows[0] if rows else None


REQUEUE: dict[str, Any] = {
    "status": "queued",
    "attempts": 0,
    "last_error": "",
    "result": None,
    "leased_by": None,
    "leased_until": None,
    "run_after": "now",
}


async def requeue(client: Supa, job_id: str) -> None:
    """Put a job that gave up back in the queue, with its attempts reset.

    Without this a failure is a dead end. The idempotency key (client + slug) is
    taken for good, so the post whose draft failed because the client had no logo
    could never be drafted again even once the logo was uploaded - the API would keep
    handing back the same failed job forever. That is the single most likely thing a
    real user would hit, and the fix has to exist before anyone else uses this.

    Only ever called for a job that describe() reports as failed. It is safe because
    draft_post refuses to draft a post that already has a draft, so even if the run
    that appeared to have given up were somehow still alive, the worst case is one
    wasted claim, never two posters.
    """
    await client.update("jobs", {**REQUEUE, "updated_at": "now"}, id=f"eq.{job_id}")


async def enqueue(
    client: Supa, *, agency_id: str, kind: str, payload: dict[str, Any], key: str
) -> dict[str, Any]:
    """Insert a job as the caller, or return the one already there for this key.

    Runs through the caller's own client, so row-level security checks the agency
    exactly as it does for every other write.
    """
    existing = await find(client, agency_id, key)
    if existing is not None:
        return existing
    rows = await client.insert(
        "jobs",
        {"agency_id": agency_id, "kind": kind, "payload": payload, "idempotency_key": key},
        upsert=True,
        on_conflict="agency_id,idempotency_key",
    )
    return rows[0]
