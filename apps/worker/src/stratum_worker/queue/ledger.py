"""Queue bookkeeping: the ONLY code that uses a credential able to cross agencies.

Claiming has to look at every agency's queue, so it cannot run as one agency's user.
Everything here is deliberately small and knows nothing about drafts: it claims a
row, and it writes a row's outcome. The draft itself runs through a client scoped to
the job's agency (see ClientFactory) and never sees this key.

Two rules keep a lost lease from doing damage:
- settle() only writes if this worker STILL holds the lease (leased_by + status are
  in the filter). A worker whose lease expired and was re-claimed writes nothing.
- The worker stops a job well before its lease runs out (see worker.work_timeout),
  so in normal operation a lease is never lost at all.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values

from stratum_worker.app.supa import Supa
from stratum_worker.queue.jobs import Job

KEY = "SUPABASE_SERVICE_ROLE_KEY"
WORKER_ENV = Path(".env.worker")


class Ledger(Protocol):
    async def claim(self, worker: str, lease_seconds: int) -> Job | None: ...

    async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        """Write the outcome if `worker` still holds the lease. False means it lost it."""
        ...


class PostgrestLedger:
    def __init__(self, supa: Supa) -> None:
        self._supa = supa

    async def claim(self, worker: str, lease_seconds: int) -> Job | None:
        rows = await self._supa.rpc("claim_job", {"worker": worker, "lease_seconds": lease_seconds})
        return Job.model_validate(rows[0]) if isinstance(rows, list) and rows else None

    async def settle(self, job: Job, worker: str, values: dict[str, Any]) -> bool:
        rows = await self._supa.update(
            "jobs",
            {**values, "updated_at": "now"},
            id=f"eq.{job.id}",
            leased_by=f"eq.{worker}",
            status="eq.running",
        )
        return bool(rows)


def worker_env(env_file: Path = WORKER_ENV) -> dict[str, str]:
    """The worker's settings: the process environment over .env.worker.

    A deployed worker gets its secrets from the environment; a laptop gets them from
    .env.worker. Never from .env, which the API also reads.
    """
    file = {k: v for k, v in dotenv_values(env_file).items() if v} if env_file.is_file() else {}
    live = {k: v for k, v in os.environ.items() if v}
    return {**file, **live}


def privileged_client(env_file: Path = WORKER_ENV) -> Supa:
    """The service-role client, from the worker's own environment only."""
    env = worker_env(env_file)
    url = env.get("SUPABASE_URL", "")
    key = env.get(KEY, "")
    if not url or not key:
        raise RuntimeError(
            f"the queue needs SUPABASE_URL and {KEY} in the worker's environment or "
            f"in {env_file} (gitignored; nothing else reads it)"
        )
    return Supa(
        base_rest=f"{url}/rest/v1",
        base_storage=f"{url}/storage/v1",
        api_key=key,
        access_token=key,
    )
