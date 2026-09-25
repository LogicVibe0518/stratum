"""`stratum-draft`: one brief in, a month's worth of a client's posts out.

    stratum-draft --client kowalski-plumbing \
        --brief "20% off boiler servicing, book before 31 October" \
        --slug offer-boiler-october \
        --photo-shows "an engineer kneeling beside a wall-mounted boiler"

Writes into the client's own folder and prints what it cost and what a human should
look at. Run it twice and the second run costs nothing: the words are checkpointed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from stratum_spec.models import Aspect

from stratum_worker.assets import AssetStore
from stratum_worker.draft.clients import ClientFileError, list_clients
from stratum_worker.draft.compose import ComposeError
from stratum_worker.draft.models import PostBrief, PostDraft
from stratum_worker.draft.pipeline import Deps, DraftError, draft_post
from stratum_worker.gateway.config import load_config
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.providers.registry import build_router
from stratum_worker.gateway.tracing import tracer_from_env

VALID_ASPECTS = ("1:1", "4:5", "9:16", "16:9")


def _aspects(raw: str) -> tuple[Aspect, ...]:
    chosen: list[Aspect] = []
    for part in raw.split(","):
        value = part.strip()
        if value not in VALID_ASPECTS:
            raise SystemExit(f"--aspects: {value!r} is not one of {', '.join(VALID_ASPECTS)}")
        if value not in chosen:
            chosen.append(value)  # type: ignore[arg-type]
    if not chosen:
        raise SystemExit("--aspects needs at least one aspect")
    return tuple(chosen)


def _report(draft: PostDraft, seconds: float) -> None:
    print(f"\n{draft.client_id} / {draft.slug}  ({draft.template_id})")
    print(f'  headline "{draft.words.headline}"')
    print(f'  offer    "{draft.words.offer_detail}"')
    print(f'  cta      "{draft.words.cta}"')
    print(f"  caption  {len(draft.words.caption)} chars, {len(draft.words.hashtags)} hashtags")
    for poster in draft.posters:
        print(f"  {poster.aspect:<5} {poster.width}x{poster.height}  {poster.png_path}")
    print(f"  cost ${draft.cost_usd:.5f}   time {seconds:.1f}s")
    if not draft.flags:
        print("  no flags")
        return
    for flag in draft.flags:
        mark = "BLOCKER" if flag.severity == "blocker" else "check  "
        print(f"  {mark}  {flag.message}")


async def _run(brief: PostBrief, deps: Deps) -> PostDraft:
    return await draft_post(brief, deps)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratum-draft", description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--slug", help="folder name for this post (default: from the brief)")
    parser.add_argument("--template", default="offer_4x5")
    parser.add_argument("--aspects", default="4:5,1:1", help=f"any of {', '.join(VALID_ASPECTS)}")
    parser.add_argument("--photo-shows", default="", help="what the photo shows, for alt text")
    parser.add_argument("--occasion", default="", help="Diwali, Christmas, Eid ...")
    parser.add_argument(
        "--platform", default="instagram", choices=("instagram", "facebook", "linkedin")
    )
    parser.add_argument("--clients", type=Path, default=Path("clients"))
    parser.add_argument("--templates", type=Path, default=Path("templates"))
    parser.add_argument("--store", type=Path, default=Path(".stratum/assets"))
    args = parser.parse_args(argv)

    slug = args.slug or "-".join(args.brief.lower().split()[:5]).strip("-.,")
    brief = PostBrief(
        client_id=args.client,
        brief=args.brief,
        template_id=args.template,
        slug=slug,
        aspects=_aspects(args.aspects),
        platform=args.platform,
        photo_description=args.photo_shows,
        occasion=args.occasion,
    )

    config = load_config()
    router = build_router(config)
    if not router.providers:
        raise SystemExit("no API keys found - see .env.example (OPENROUTER_API_KEY is enough)")
    deps = Deps(
        gateway=Gateway(router=router, tracer=tracer_from_env()),
        store=AssetStore(args.store),
        templates_dir=args.templates,
        repo_root=Path.cwd(),
        clients_root=args.clients,
    )

    started = time.perf_counter()
    try:
        draft = asyncio.run(_run(brief, deps))
    except ClientFileError as exc:
        known = ", ".join(list_clients(args.clients)) or "none"
        raise SystemExit(f"{exc}\nclients available: {known}") from exc
    except (DraftError, ComposeError) as exc:
        raise SystemExit(str(exc)) from exc
    _report(draft, time.perf_counter() - started)
    return 1 if draft.blockers else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
