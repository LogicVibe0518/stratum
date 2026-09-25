"""`stratum-batch`: a month of posts for every client, and the numbers to prove it.

    stratum-batch --plan benchmarks/v1/month.json

The owner's problem was stated in minutes: about an hour per post. This command is
the answer in the same unit - it prints how long every post took, what it cost, and
what a human still has to look at. Those three numbers are the pitch.

Posts run concurrently because they are independent; the gateway's budget cap and
circuit breakers are shared, so running twenty at once cannot outspend one at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from stratum_spec.models import Aspect

from stratum_worker.assets import AssetStore
from stratum_worker.draft.cli import VALID_ASPECTS
from stratum_worker.draft.models import PostBrief, PostDraft
from stratum_worker.draft.pipeline import Deps, draft_post
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import build_router
from stratum_worker.gateway.tracing import tracer_from_env


@dataclass
class Outcome:
    brief: PostBrief
    draft: PostDraft | None
    seconds: float
    error: str = ""


def read_plan(path: Path, aspects: tuple[Aspect, ...]) -> list[PostBrief]:
    """A plan is {client_id: [post, post, ...]}. Everything else has a default."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected an object of client id -> list of posts")
    briefs: list[PostBrief] = []
    for client_id, posts in data.items():
        if not isinstance(posts, list):
            raise SystemExit(f"{path}: {client_id} should map to a list")
        for index, post in enumerate(posts, start=1):
            if not isinstance(post, dict) or "brief" not in post:
                raise SystemExit(f"{path}: {client_id}[{index}] needs at least a 'brief'")
            briefs.append(
                PostBrief(
                    client_id=client_id,
                    brief=str(post["brief"]),
                    template_id=str(post.get("template", "offer_4x5")),
                    slug=str(post.get("slug") or f"post-{index:02d}"),
                    aspects=aspects,
                    photo_description=str(post.get("photo_shows", "")),
                    occasion=str(post.get("occasion", "")),
                )
            )
    return briefs


async def _one(brief: PostBrief, deps: Deps, gate: asyncio.Semaphore) -> Outcome:
    async with gate:
        started = time.perf_counter()
        try:
            draft = await draft_post(brief, deps)
        except Exception as exc:  # one bad post must not lose the other thirty-four
            return Outcome(
                brief, None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
            )
        return Outcome(brief, draft, time.perf_counter() - started)


async def run_plan(briefs: list[PostBrief], deps: Deps, concurrency: int) -> list[Outcome]:
    gate = asyncio.Semaphore(concurrency)
    return list(await asyncio.gather(*(_one(brief, deps, gate) for brief in briefs)))


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)}m{seconds % 60:04.1f}s" if seconds >= 60 else f"{seconds:.1f}s"


def report(outcomes: list[Outcome], wall: float) -> int:
    by_client: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_client.setdefault(outcome.brief.client_id, []).append(outcome)

    print(f"\n{'client':<22}{'posts':>6}{'ok':>5}{'slowest':>10}{'cost':>10}{'to check':>10}")
    print("-" * 63)
    total_cost = 0.0
    total_flags = 0
    total_failed = 0
    for client_id, group in sorted(by_client.items()):
        drafts = [o.draft for o in group if o.draft is not None]
        cost = sum(d.cost_usd for d in drafts)
        flags = sum(len(d.flags) for d in drafts)
        slowest = max((o.seconds for o in group), default=0.0)
        total_cost += cost
        total_flags += flags
        total_failed += len(group) - len(drafts)
        print(
            f"{client_id:<22}{len(group):>6}{len(drafts):>5}"
            f"{_minutes(slowest):>10}{'$' + format(cost, '.4f'):>10}{flags:>10}"
        )
    print("-" * 63)
    posts = len(outcomes)
    done = posts - total_failed
    print(
        f"{'TOTAL':<22}{posts:>6}{done:>5}{_minutes(wall):>10}"
        f"{'$' + format(total_cost, '.4f'):>10}{total_flags:>10}"
    )
    if done:
        print(
            f"\n{_minutes(wall / done)} and ${total_cost / done:.4f} per post, "
            f"across {len(by_client)} clients."
        )

    blockers = [
        (o.brief.client_id, o.brief.slug, flag)
        for o in outcomes
        if o.draft is not None
        for flag in o.draft.blockers
    ]
    if blockers:
        print(f"\n{len(blockers)} blocker(s) - do not publish these until a human looks:")
        for client_id, slug, flag in blockers:
            print(f"  {client_id}/{slug}: {flag.message}")
    for outcome in outcomes:
        if outcome.error:
            print(f"\nFAILED {outcome.brief.client_id}/{outcome.brief.slug}: {outcome.error}")
    return 1 if total_failed or blockers else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratum-batch", description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--aspects", default="4:5,1:1,9:16,16:9")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--clients", type=Path, default=Path("clients"))
    parser.add_argument("--templates", type=Path, default=Path("templates"))
    parser.add_argument("--store", type=Path, default=Path(".stratum/assets"))
    args = parser.parse_args(argv)

    aspects: list[Aspect] = []
    for part in args.aspects.split(","):
        value = part.strip()
        if value not in VALID_ASPECTS:
            raise SystemExit(f"--aspects: {value!r} is not one of {', '.join(VALID_ASPECTS)}")
        aspects.append(value)

    briefs = read_plan(args.plan, tuple(aspects))
    if not briefs:
        raise SystemExit(f"{args.plan} has no posts in it")

    config = load_config()
    router = build_router(config)
    if not router.providers:
        raise SystemExit("no API keys found - see .env.example")
    deps = Deps(
        gateway=Gateway(router=router, tracer=tracer_from_env()),
        store=AssetStore(args.store),
        templates_dir=args.templates,
        repo_root=Path.cwd(),
        clients_root=args.clients,
    )

    print(f"{len(briefs)} posts x {len(aspects)} formats, {args.concurrency} at a time...")
    started = time.perf_counter()
    outcomes = asyncio.run(run_plan(briefs, deps, max(1, args.concurrency)))
    return report(outcomes, time.perf_counter() - started)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
