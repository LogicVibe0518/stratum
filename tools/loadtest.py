"""Drive N posts through the queue and print wait vs work, cost, throughput.

--fake wires FakeProvider and a local SQLite stand-in. That path spends nothing and
is what CI runs. --crash always uses --fake: whether a job survives kill -9 has
nothing to do with which model wrote the caption.

Without --fake the same loop uses the real gateway, capped at Budget(daily_cap_usd=2.0).
If the run would exceed it, BudgetExceeded fails the job and the report says so.
Do not run that from a machine that should not spend money.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


DEFAULT_BUDGET_USD = 2.0
DEFAULT_N = 20


@dataclass(frozen=True)
class Sample:
    job_id: str
    status: str
    queue_wait_s: float
    work_s: float
    attempts: int


@dataclass(frozen=True)
class Report:
    n: int
    concurrency: int
    elapsed_s: float
    cost_usd: float
    samples: tuple[Sample, ...]
    lost: int
    budget_stopped: bool

    @property
    def done(self) -> tuple[Sample, ...]:
        return tuple(s for s in self.samples if s.status == "done")


def percentile(values: Sequence[float], p: float) -> float:
    """Linear interpolation between closest ranks. Empty is 0, not an exception."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _note_first_claim(job: dict[str, object], first: dict[str, datetime]) -> None:
    job_id = str(job.get("id") or "")
    if not job_id or job_id in first:
        return
    attempts = int(job.get("attempts") or 0)
    if attempts < 1:
        return
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if job.get("status") == "running":
        claimed = _parse_dt(job.get("updated_at"))
    elif attempts == 1:
        claimed = _parse_dt(result.get("claimed_at")) if result else None
    else:
        claimed = None
    if claimed is not None:
        first[job_id] = claimed


def samples_from(jobs: Sequence[dict[str, object]], first: dict[str, datetime]) -> list[Sample]:
    out: list[Sample] = []
    for job in jobs:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        created = _parse_dt(job.get("created_at"))
        claimed = first.get(str(job.get("id"))) or (
            _parse_dt(result.get("claimed_at")) if result else None
        )
        wait = max(0.0, (claimed - created).total_seconds()) if created and claimed else 0.0
        work = float(result.get("work_seconds") or 0.0) if result else 0.0
        out.append(
            Sample(
                job_id=str(job.get("id") or ""),
                status=str(job.get("status") or ""),
                queue_wait_s=wait,
                work_s=work,
                attempts=int(job.get("attempts") or 0),
            )
        )
    return out


def format_report(report: Report, *, title: str) -> str:
    waits = [s.queue_wait_s for s in report.done]
    works = [s.work_s for s in report.done]
    rate = report.n / report.elapsed_s if report.elapsed_s else 0.0
    lines = [
        title,
        f"  n={report.n}  concurrency={report.concurrency}  "
        f"done={len(report.done)}  failed={report.n - len(report.done) - report.lost}  "
        f"lost={report.lost}",
        f"  queue wait  p50={percentile(waits, 50):.3f}s  p95={percentile(waits, 95):.3f}s",
        f"  work time   p50={percentile(works, 50):.3f}s  p95={percentile(works, 95):.3f}s",
        f"  total cost  ${report.cost_usd:.4f}",
        f"  throughput  {rate:.2f} posts/s  ({report.n} posts in {report.elapsed_s:.2f}s)",
    ]
    if report.budget_stopped:
        lines.append("  stopped: BudgetExceeded (the $2.00 daily cap would have been crossed)")
    return "\n".join(lines)


async def _enqueue(world: object, agency: str, client_id: str, n: int) -> list[str]:
    from stratum_worker.queue import jobs

    factory = world.factory
    supa = factory(agency)
    ids: list[str] = []
    for i in range(n):
        slug = f"lt-{i:04d}"
        post = (
            await supa.insert(
                "posts",
                {
                    "agency_id": agency,
                    "client_id": client_id,
                    "slug": slug,
                    "brief": "20% off boiler servicing this month. Gas Safe 000000.",
                    "template_id": "offer_4x5",
                    "aspects": ["4:5"],
                },
            )
        )[0]
        job = await jobs.enqueue(
            supa,
            agency_id=agency,
            kind=jobs.DRAFT_POST,
            payload={"post_id": post["id"]},
            key=jobs.idempotency_key(client_id, slug),
        )
        ids.append(str(job["id"]))
    return ids


async def _cost_usd(world: object, agency: str) -> float:
    posts = await world.factory(agency).select("posts", select="cost_usd")
    return sum(float(row.get("cost_usd") or 0) for row in posts)


async def _wait_idle(
    world: object,
    n: int,
    first: dict[str, datetime],
    timeout_s: float,
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rows = list(world.jobs())
        for row in rows:
            _note_first_claim(row, first)
        terminal = sum(1 for row in rows if row.get("status") in ("done", "failed"))
        if len(rows) >= n and terminal >= n:
            return rows
        await asyncio.sleep(0.02)
    return list(world.jobs())


def _gateway(*, fake: bool, model_delay_s: float, budget_usd: float):
    from stratum_worker.gateway.budget import Budget
    from stratum_worker.gateway.gateway import Gateway
    from stratum_worker.queue.fake import gateway as fake_gateway

    budget = Budget(daily_cap_usd=budget_usd)
    if fake:
        return fake_gateway(model_delay_s, budget=budget)
    from stratum_worker.gateway.config import load_config
    from stratum_worker.gateway.providers.registry import build_router
    from stratum_worker.gateway.tracing import tracer_from_env

    return Gateway(
        router=build_router(load_config()),
        budget=budget,
        tracer=tracer_from_env(),
    )


async def run_bench(
    workdir: Path,
    *,
    n: int,
    concurrency: int,
    fake: bool,
    model_delay_s: float,
    budget_usd: float,
    lease_s: int,
) -> Report:
    from stratum_worker.queue.fake import World, seed
    from stratum_worker.queue.worker import Deps, run_pool

    workdir.mkdir(parents=True, exist_ok=True)
    world = World(workdir / "db.sqlite")
    agency, client_id = await seed(world, REPO)
    await _enqueue(world, agency, client_id, n)
    first: dict[str, datetime] = {}
    stop = asyncio.Event()
    deps = Deps(
        settings=world.settings(REPO),
        gateway=_gateway(fake=fake, model_delay_s=model_delay_s, budget_usd=budget_usd),
    )
    started = time.perf_counter()
    workers = asyncio.create_task(
        run_pool(
            world.ledger(),
            world.scoped,
            deps,
            concurrency=concurrency,
            stop=stop,
            lease_seconds=lease_s,
            idle_s=0.05,
        )
    )
    try:
        rows = await _wait_idle(world, n, first, timeout_s=max(30.0, n * 8.0 / max(concurrency, 1)))
    finally:
        stop.set()
        await workers
    elapsed = time.perf_counter() - started
    for row in rows:
        _note_first_claim(row, first)
    found = samples_from(rows, first)
    lost = sum(1 for row in rows if row.get("status") not in ("done", "failed"))
    cost = await _cost_usd(world, agency)
    budget_stopped = any("BudgetExceeded" in str(row.get("last_error") or "") for row in rows)
    return Report(
        n=n,
        concurrency=concurrency,
        elapsed_s=elapsed,
        cost_usd=cost,
        samples=tuple(found),
        lost=lost,
        budget_stopped=budget_stopped,
    )


def _worker_cmd(db: Path, *, lease_s: int, model_delay_s: float) -> list[str]:
    return [
        sys.executable,
        "-m",
        "stratum_worker.queue.worker",
        "--fake-db",
        str(db),
        "--concurrency",
        "1",
        "--lease",
        str(lease_s),
        "--model-delay",
        str(model_delay_s),
    ]


def run_crash(
    workdir: Path,
    *,
    n: int = 8,
    lease_s: int = 5,
    model_delay_s: float = 0.4,
) -> Report:
    """Enqueue N jobs, start two worker processes, SIGKILL one, wait for the rest.

    Always fake. The question is 'did the lease recover the in-flight job?', which
    does not need a model bill.
    """
    from stratum_worker.queue.fake import World

    async def setup() -> tuple[World, str]:
        from stratum_worker.queue.fake import seed

        workdir.mkdir(parents=True, exist_ok=True)
        world = World(workdir / "db.sqlite")
        agency, client_id = await seed(world, REPO)
        await _enqueue(world, agency, client_id, n)
        return world, agency

    world, agency = asyncio.run(setup())
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "")
    cmd = _worker_cmd(world.path, lease_s=lease_s, model_delay_s=model_delay_s)
    procs = [
        subprocess.Popen(cmd, cwd=REPO, env=env, start_new_session=True)  # noqa: S603
        for _ in range(2)
    ]
    first: dict[str, datetime] = {}
    started = time.perf_counter()
    killed = False
    rows: list[dict[str, object]] = []
    try:
        deadline = time.monotonic() + max(45.0, n * (model_delay_s + 2.0) + lease_s + 5.0)
        while time.monotonic() < deadline:
            rows = world.jobs()
            for row in rows:
                _note_first_claim(row, first)
            in_flight = [row for row in rows if row.get("status") == "running"]
            if in_flight and not killed:
                os.kill(procs[0].pid, signal.SIGKILL)
                killed = True
            terminal = sum(1 for row in rows if row.get("status") in ("done", "failed"))
            if killed and terminal >= n:
                break
            time.sleep(0.05)
        if not killed:
            raise RuntimeError("never saw a running job to kill - raise --model-delay")
        rows = world.jobs()
        for row in rows:
            _note_first_claim(row, first)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    elapsed = time.perf_counter() - started
    found = samples_from(rows, first)
    lost = sum(1 for row in rows if row.get("status") not in ("done", "failed"))
    cost = asyncio.run(_cost_usd(world, agency))
    return Report(
        n=n,
        concurrency=2,
        elapsed_s=elapsed,
        cost_usd=cost,
        samples=tuple(found),
        lost=lost,
        budget_stopped=False,
    )


def _parse_conc(text: str) -> list[int]:
    values = [int(part) for part in text.split(",") if part.strip()]
    if not values or any(v < 1 for v in values):
        raise SystemExit("--concurrency must be a positive int, or a comma list like 5,20")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loadtest", description=__doc__)
    parser.add_argument(
        "--fake",
        action="store_true",
        help="FakeProvider plus local SQLite. Free. This is the CI path.",
    )
    parser.add_argument(
        "--crash",
        action="store_true",
        help="kill -9 one worker halfway; always uses --fake",
    )
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument(
        "--concurrency",
        default="5",
        help="worker loops (one int), or 5,20 to print both",
    )
    parser.add_argument("--model-delay", type=float, default=0.0)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--lease", type=int, default=180)
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.crash:
        args.fake = True
    if not args.fake:
        print(
            "real providers: spend is capped at "
            f"${args.budget:.2f} by Budget. Ctrl-C if that is not what you meant.",
            file=sys.stderr,
        )

    root = args.workdir or Path(".stratum/loadtest")
    lines: list[str] = []
    if args.crash:
        crash_dir = root / "crash"
        report = run_crash(crash_dir, n=max(args.n, 6) if args.n == DEFAULT_N else args.n)
        lines.append(format_report(report, title="crash (kill -9, --fake)"))
        if report.lost:
            lines.append(f"FAIL: {report.lost} job(s) lost after the worker was killed")
            print("\n".join(lines))
            return 1
    else:
        for conc in _parse_conc(args.concurrency):
            workdir = root / f"c{conc}"
            report = asyncio.run(
                run_bench(
                    workdir,
                    n=args.n,
                    concurrency=conc,
                    fake=args.fake,
                    model_delay_s=args.model_delay,
                    budget_usd=args.budget,
                    lease_s=args.lease,
                )
            )
            mode = "fake" if args.fake else "live"
            lines.append(format_report(report, title=f"{mode} loadtest  concurrency={conc}"))
            if report.lost or report.budget_stopped:
                print("\n".join(lines))
                return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
