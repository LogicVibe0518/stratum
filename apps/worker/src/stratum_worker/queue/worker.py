"""The queue loop: claim a job, do it as the person who asked, write down what happened.

Nothing here holds a credential. Claiming and settling go through an injected Ledger;
the work itself goes through `factory(job)`, a client that acts as the job's requester
and so can only see that person's agency. A job for agency A therefore cannot read or
write agency B's rows even if this code had a bug in it - the database would refuse.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import logging
import os
import signal
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from stratum_worker.app import service
from stratum_worker.app.settings import Settings
from stratum_worker.app.supa import Supa, SupabaseError
from stratum_worker.draft.compose import ComposeError
from stratum_worker.draft.pipeline import DraftError
from stratum_worker.gateway.errors import BudgetExceeded
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.queue.jobs import (
    DRAFT_POST,
    ClientFactory,
    Job,
    describe_error,
    done_values,
    failed_values,
)
from stratum_worker.queue.ledger import Ledger

log = logging.getLogger("stratum.queue")

DEFAULT_LEASE_S = 180

# Errors that will fail the same way however often they are retried, and where each
# retry would pay for another model call first: a design with no photo for a slot, a
# client with no logo, a spend cap already hit. These fail immediately rather than
# burning the remaining attempts.
PERMANENT: tuple[type[BaseException], ...] = (
    DraftError,
    ComposeError,
    ValidationError,
    BudgetExceeded,
    PermissionError,
)


class DraftPayload(BaseModel):
    # Who asked is NOT read from here any more: a payload is written by the caller, so
    # it could name anyone. The requester is the job's requested_by column, which the
    # database stamps itself. Old payloads that still carry the field parse fine.
    post_id: str


@dataclass(frozen=True)
class Deps:
    settings: Settings
    gateway: Gateway


Handler = Callable[[Job, Supa, Deps], Awaitable[dict[str, Any]]]


async def draft_post(job: Job, client: Supa, deps: Deps) -> dict[str, Any]:
    payload = DraftPayload.model_validate(job.payload)
    posts = await client.select("posts", id=f"eq.{payload.post_id}", select="*")
    if not posts:
        raise DraftError(f"post {payload.post_id} is not visible to agency {job.agency_id}")
    post = posts[0]
    if str(post.get("agency_id")) != job.agency_id:
        # The scoped client should make this impossible. Checked anyway, because a
        # factory that returned the wrong agency's client is exactly the bug that
        # must fail loudly rather than write a poster into someone else's account.
        raise PermissionError("the client factory returned a client for another agency")
    # M7 invariant 3: one draft per post, however many times this job runs.
    #
    # An attempt that finished the draft and then died before it could settle leaves
    # the job claimable again. Drafting a second time would pay for a second set of
    # model calls and leave the client looking at two "first drafts" of one post, so
    # the draft that already exists is reported instead. This is what makes the
    # queue's at-least-once delivery safe to build a product on, and what makes
    # putting a failed job back in the queue safe (see jobs.requeue).
    #
    # Checked on every run rather than only when attempts > 1: the invariant is "the
    # queue never drafts a post twice", and one indexed lookup is a cheap way to say
    # exactly that instead of a condition a reader has to reason about.
    done = await client.select(
        "post_versions",
        post_id=f"eq.{payload.post_id}",
        select="id,version",
        order="version.asc",
        limit="1",
    )
    if done:
        return {
            "post_id": post["id"],
            "version_id": done[0]["id"],
            "version": done[0]["version"],
            "recovered": True,
        }
    clients = await client.select("clients", id=f"eq.{post['client_id']}", select="*")
    if not clients:
        raise DraftError(f"client {post['client_id']} is not visible to agency {job.agency_id}")
    ctx = service.Context(
        supa=client,
        settings=deps.settings,
        gateway=deps.gateway,
        agency_id=job.agency_id,
        user_id=job.requested_by,
    )
    version = await service.draft(ctx, post, clients[0])
    return {"post_id": post["id"], "version_id": version["id"], "version": version["version"]}


HANDLERS: Mapping[str, Handler] = {DRAFT_POST: draft_post}


def work_timeout(lease_seconds: int) -> float:
    """How long a job may run: comfortably less than the lease it holds.

    A job still running when its lease expires would be handed to a second worker
    while the first carries on - two drafts of one post. Stopping first means a lease
    only ever runs out because the worker is gone.
    """
    margin = max(1.0, min(30.0, lease_seconds / 4))
    return lease_seconds - margin


def new_worker_id(slot: int = 0) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}:{slot}"


@dataclass
class QueueWorker:
    ledger: Ledger
    factory: ClientFactory
    deps: Deps
    lease_seconds: int = DEFAULT_LEASE_S
    idle_s: float = 1.0
    worker_id: str = field(default_factory=new_worker_id)
    handlers: Mapping[str, Handler] = field(default_factory=lambda: HANDLERS)

    async def run_once(self) -> bool:
        """Claim and finish at most one job. True if there was one."""
        job = await self.ledger.claim(self.worker_id, self.lease_seconds)
        if job is None:
            return False
        fields = {"agency_id": job.agency_id, "job_id": job.id, "worker": self.worker_id}
        started = time.monotonic()
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                raise DraftError(f"no handler for job kind {job.kind!r}")
            client = self.factory(job)
            result = await asyncio.wait_for(
                handler(job, client, self.deps), timeout=work_timeout(self.lease_seconds)
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            permanent = isinstance(exc, PERMANENT)
            values = failed_values(job, describe_error(exc), elapsed_s=elapsed, permanent=permanent)
            kept = await self.ledger.settle(job, self.worker_id, values)
            log.warning(
                "job failed",
                extra={**fields, "attempt": job.attempts, "next": values["status"], "kept": kept},
                exc_info=exc,
            )
            return True
        work = time.monotonic() - started
        result = {
            **result,
            "claimed_at": job.updated_at.isoformat(),
            "work_seconds": round(work, 3),
            "attempt": job.attempts,
            "worker": self.worker_id,
        }
        if not await self.ledger.settle(job, self.worker_id, done_values(result)):
            log.warning("lease lost before completion; result discarded", extra=fields)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                handled = await self.run_once()
            except (SupabaseError, httpx.HTTPError):
                # The ledger itself is unreachable. Nothing was claimed, or a claim
                # we cannot settle will simply lease-expire; wait and ask again.
                log.exception("queue unavailable", extra={"worker": self.worker_id})
                handled = False
            if not handled:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.idle_s)


async def run_pool(
    ledger: Ledger,
    factory: ClientFactory,
    deps: Deps,
    *,
    concurrency: int,
    stop: asyncio.Event,
    lease_seconds: int = DEFAULT_LEASE_S,
    idle_s: float = 1.0,
) -> None:
    """`concurrency` loops in one process, each its own worker with its own id."""
    workers = [
        QueueWorker(
            ledger=ledger,
            factory=factory,
            deps=deps,
            lease_seconds=lease_seconds,
            idle_s=idle_s,
            worker_id=new_worker_id(slot),
        )
        for slot in range(concurrency)
    ]
    await asyncio.gather(*(one.run(stop) for one in workers))


# --- console script ----------------------------------------------------------------


def _load_factory(target: str) -> ClientFactory:
    """`package.module:callable`, where callable() returns the ClientFactory."""
    module_name, _, attr = target.partition(":")
    if not module_name or not attr:
        raise SystemExit(f"--factory must look like package.module:callable, not {target!r}")
    made = getattr(importlib.import_module(module_name), attr)()
    if not callable(made):
        raise SystemExit(f"{target} did not return a callable client factory")
    return made  # type: ignore[no-any-return]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="stratum-queue", description=__doc__)
    parser.add_argument(
        "--factory",
        help="package.module:callable returning the scoped ClientFactory, normally "
        "stratum_worker.queue.identity:from_env (required unless --fake-db)",
    )
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--lease", type=int, default=DEFAULT_LEASE_S)
    parser.add_argument("--fake-db", help="run against a local SQLite stand-in, no network")
    parser.add_argument("--model-delay", type=float, default=0.5, help="fake mode only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    ledger: Ledger
    if args.fake_db:
        from pathlib import Path

        from stratum_worker.queue import fake

        world = fake.World(Path(args.fake_db))
        ledger, factory = world.ledger(), world.scoped
        deps = Deps(settings=world.settings(), gateway=fake.gateway(args.model_delay))
    else:
        if not args.factory:
            raise SystemExit(
                "stratum-queue needs --factory package.module:callable, normally "
                "stratum_worker.queue.identity:from_env. It is never implied: the one "
                "place a credential is made should be named on the command line."
            )
        from stratum_worker.app.settings import settings
        from stratum_worker.gateway.config import load_config
        from stratum_worker.gateway.providers.registry import build_router
        from stratum_worker.gateway.tracing import tracer_from_env
        from stratum_worker.queue.ledger import PostgrestLedger, privileged_client

        ledger = PostgrestLedger(privileged_client())
        factory = _load_factory(args.factory)
        gateway = Gateway(router=build_router(load_config()), tracer=tracer_from_env())
        deps = Deps(settings=settings(), gateway=gateway)

    async def go() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        await run_pool(
            ledger, factory, deps, concurrency=args.concurrency, stop=stop, lease_seconds=args.lease
        )

    asyncio.run(go())


if __name__ == "__main__":
    main()
