"""The copy eval (M7b): the same briefs, written by the real model, scored in code.

    make copy-eval                      # every case in ml/copy/cases.jsonl, real model
    make copy-eval ARGS="--limit 5"     # the first five

Each case is a brief for one of the fictional benchmark clients, with their full
profile (services, areas, rules) or an empty one (a client made in the app before
anyone filled it in, like the three live briefs of 5 Oct). The words come from
`service.ask_for_copy`, the function drafts use, so this measures the product, not a
copy of it. Every line is then scored:

- echo: the offer line repeats the headline (copycheck.ECHO_MAX);
- copied: a line copies COPIED_MAX or more words in a row from the brief;
- cta_verb: the call to action starts with a verb;
- invented_number, banned_claim, missing_required_phrase: the client's rules
  (draft.preflight.check_copy, the same checks a draft gets);
- service / area in the caption's first sentence, when the client has them;
- too_long: a line over its character limit;
- missing: how many questions came back for the business;
- calls: 2 when the first draft was sent back.

A report (JSON, every case with its words) goes to ml/copy/reports/, named by prompt
version and time. `--baseline` runs the product as it was before v3 (the v2 prompt
kept in ml/copy/post_copy_v2.md, one call, no second try), so old and new are
compared on the same cases with the same code. About a cent per run of 30 cases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stratum_spec.client import ClientRules

from stratum_worker.app.service import ask_for_copy
from stratum_worker.draft import copycheck
from stratum_worker.draft.models import PostCopy
from stratum_worker.draft.pipeline import CollectingTracer
from stratum_worker.draft.preflight import check_copy
from stratum_worker.gateway import prompts
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.prompts import VERSION
from stratum_worker.gateway.tasks import PostCopyRequest, TaskName

REPO = Path(__file__).resolve().parents[5]
CASES = REPO / "ml" / "copy" / "cases.jsonl"
REPORTS = REPO / "ml" / "copy" / "reports"
BASELINE = REPO / "ml" / "copy" / "post_copy_v2.md"  # the prompt before v3, kept for comparison
LIMITS = {"headline": 60, "offer_detail": 120, "cta": 40}
RULE_FLAGS = ("invented_number", "banned_claim", "missing_required_phrase")


@dataclass(frozen=True)
class Case:
    id: str
    client: str  # a folder in clients/
    profile: str  # "full" or "empty"
    brief: str
    about: str = ""


def load_cases(path: Path = CASES) -> list[Case]:
    return [
        Case(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def client_of(case: Case, root: Path = REPO) -> tuple[str, str, ClientRules]:
    """The fictional client's name, trade and rules; an empty profile keeps only the
    name and trade, like a client made in the app and never filled in."""
    meta = json.loads((root / "clients" / case.client / "client.json").read_text("utf-8"))
    rules = ClientRules.model_validate(meta["rules"])
    if case.profile == "empty":
        rules = ClientRules(client_id=rules.client_id, version=rules.version)
    return str(meta["name"]), str(meta.get("trade", "")), rules


def request_for(case: Case, root: Path = REPO) -> tuple[PostCopyRequest, ClientRules]:
    name, trade, rules = client_of(case, root)
    request = PostCopyRequest(
        brief=case.brief,
        client_name=name,
        trade=trade,
        services=tuple(rules.services),
        areas=tuple(rules.areas),
        allowed_hashtags=tuple(rules.allowed_hashtags),
        max_hashtags=rules.max_hashtags,
        must_include=tuple(rules.must_include),
        banned_claims=tuple(rules.banned_claims),
        headline_max_chars=LIMITS["headline"],
        offer_max_chars=LIMITS["offer_detail"],
        cta_max_chars=LIMITS["cta"],
    )
    return request, rules


def score(case: Case, words: PostCopy, rules: ClientRules, calls: int) -> dict[str, Any]:
    """Every check for one case's words: True means the problem is present."""
    flags = {one.code for one in check_copy(words, rules, case.brief)}
    echo = copycheck.echo(words.headline, words.offer_detail)
    copied = max(
        copycheck.copied_run(case.brief, words.headline),
        copycheck.copied_run(case.brief, words.offer_detail),
    )
    lengths = {
        "headline": len(words.headline),
        "offer_detail": len(words.offer_detail),
        "cta": len(words.cta),
    }
    return {
        "echo": echo > copycheck.ECHO_MAX,
        "echo_share": round(echo, 3),
        "copied": copied >= copycheck.COPIED_MAX,
        "copied_run": copied,
        "cta_no_verb": not copycheck.cta_verb(words.cta),
        **{code: code in flags for code in RULE_FLAGS},
        "no_service_first": bool(rules.services) and "no_service_in_first_sentence" in flags,
        "no_area_first": bool(rules.areas) and "no_area_in_first_sentence" in flags,
        "too_long": any(lengths[key] > LIMITS[key] for key in LIMITS),
        "missing": len(words.missing),
        "calls": calls,
    }


# Problems whose rate the summary reports (share of cases with the problem).
PROBLEMS = (
    "echo",
    "copied",
    "cta_no_verb",
    *RULE_FLAGS,
    "no_service_first",
    "no_area_first",
    "too_long",
)


@dataclass
class Report:
    version: int
    started: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        scored = [row["score"] for row in self.rows]
        n = len(scored)
        rates = {
            key: round(sum(bool(s[key]) for s in scored) / n, 3) if n else None for key in PROBLEMS
        }
        return {
            "cases": n,
            "failed": len(self.failed),
            "rates": rates,
            "clean": round(sum(not any(s[k] for k in PROBLEMS) for s in scored) / n, 3)
            if n
            else None,
            "second_tries": sum(s["calls"] == 2 for s in scored),
            "with_questions": sum(s["missing"] > 0 for s in scored),
            "cost_usd": round(sum(row["cost_usd"] for row in self.rows), 5),
            "models": sorted({row["model"] for row in self.rows if row["model"]}),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": f"post.copy v{self.version}",
            "started": self.started,
            "summary": self.summary(),
            "rows": self.rows,
            "failed": self.failed,
        }


async def run(
    gateway: Gateway, cases: list[Case], *, root: Path = REPO, baseline: bool = False
) -> Report:
    """Every case through `ask_for_copy`; with `baseline`, as the product was before
    v3: the v2 prompt, one call."""
    if baseline:
        with prompts.overridden(TaskName.POST_COPY, BASELINE.read_text("utf-8"), 2):
            return await _run(gateway, cases, root=root, version=2, second_try=False)
    return await _run(gateway, cases, root=root, version=VERSION[TaskName.POST_COPY])


async def _run(
    gateway: Gateway,
    cases: list[Case],
    *,
    root: Path,
    version: int,
    second_try: bool = True,
) -> Report:
    report = Report(version=version, started=datetime.now(UTC).isoformat())
    for case in cases:
        request, rules = request_for(case, root)
        tracer = CollectingTracer(inner=gateway.tracer)
        traced = Gateway(router=gateway.router, budget=gateway.budget, tracer=tracer)
        began = time.perf_counter()
        try:
            result, calls = await ask_for_copy(
                traced, request, agency_id="copy-eval", second_try=second_try
            )
        except Exception as exc:
            report.failed.append({"id": case.id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        words = PostCopy(
            headline=result.headline,
            offer_detail=result.offer_detail,
            cta=result.cta,
            caption=result.caption,
            hashtags=result.hashtags,
            alt_text=result.alt_text,
            missing=tuple(one for one in result.missing if one.strip())[:3],
        )
        step = tracer.step("copy", TaskName.POST_COPY)
        report.rows.append(
            {
                "id": case.id,
                "client": case.client,
                "profile": case.profile,
                "brief": case.brief,
                "about": case.about,
                "words": words.model_dump(mode="json"),
                "score": score(case, words, rules, calls),
                "model": step.model,
                "cost_usd": step.cost_usd,
                "seconds": round(time.perf_counter() - began, 2),
            }
        )
    return report


def table(report: Report) -> str:
    """The summary, and every case with a problem, as text for the terminal."""
    summary = report.summary()
    lines = [
        f"post.copy v{report.version}: {summary['cases']} cases, {summary['failed']} failed, "
        f"models {', '.join(summary['models']) or '-'}, ${summary['cost_usd']}",
        f"clean (no problem at all): {summary['clean']}",
        f"second tries: {summary['second_tries']}   with questions back: "
        f"{summary['with_questions']}",
        "",
        "problem rates:",
        *(f"  {key:<24} {value}" for key, value in summary["rates"].items()),
        "",
    ]
    for row in report.rows:
        found = [key for key in PROBLEMS if row["score"][key]]
        if found:
            words = row["words"]
            lines.append(f"- {row['id']}: {', '.join(found)}")
            lines.append(f"    {words['headline']} | {words['offer_detail']} | {words['cta']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copy-eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, default=REPORTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--baseline", action="store_true", help="the product before v3: v2 prompt, one call"
    )
    args = parser.parse_args(argv)

    from stratum_worker.gateway.config import load_config
    from stratum_worker.gateway.providers.registry import build_router
    from stratum_worker.gateway.tracing import tracer_from_env

    router = build_router(load_config())
    if not router.providers:
        print("no API keys found - see .env.example (OPENROUTER_API_KEY is enough)")
        return 2
    cases = load_cases(args.cases)
    cases = cases[: args.limit] if args.limit else cases
    gateway = Gateway(router=router, tracer=tracer_from_env())
    report = asyncio.run(run(gateway, cases, baseline=args.baseline))
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = args.out / f"post-copy-v{report.version}-{stamp}.json"
    path.write_text(json.dumps(report.as_dict(), indent=1, ensure_ascii=False), "utf-8")
    print(table(report))
    print(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BASELINE",
    "CASES",
    "Case",
    "Report",
    "load_cases",
    "request_for",
    "run",
    "score",
    "table",
]
