"""The chat eval (M10h): typed changes, read by the real model, scored in code.

    make chat-eval                      # every case in ml/chat/cases.jsonl, real model
    make chat-eval ARGS="--baseline"    # the chat before M10i: prompt v6, with pointing

Each case is a message a designer might type about one of a few fixed posters, made
offline from the design space for a fictional client (the same posters every run),
optionally with the part they pointed at and their last few changes. The request is
built by `edit_request` and the answer validated by `split` and the op schema, the
functions tweaks use, and a pointed short message goes through `quick_ops` with no
model, as in the product. Then it is scored against what the case expects:

- outcome: "change" (at least one valid change), "ask" (a question with choices),
  "refuse" (no change, a reason given), "change_or_ask", or a list of the outcomes
  that are right;
- ops: each expected op must be there, matched on its kind, its layer and the
  direction of its numbers ("factor": ">1" is bigger, "dx": "<0" is left);
  "any": [...] accepts one of several ways (a move, or a restyle that moves);
- only: no other part of the poster may be touched.

A case passes when all of these hold. The report (JSON) goes to ml/chat/reports/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import TypeAdapter, ValidationError
from stratum_spec import BrandKit, PosterSpec
from stratum_spec.ops import EditOp, OpError, apply_ops

from stratum_worker.design.preview import bind, ink
from stratum_worker.design.space import lay_out
from stratum_worker.design.typeset import Copy
from stratum_worker.draft.capacity import measure
from stratum_worker.draft.pipeline import CollectingTracer
from stratum_worker.edit.actions import (
    Ask,
    Badge,
    Restyle,
    Undo,
    split,
    without_logo_recolours,
)
from stratum_worker.edit.context import edit_request
from stratum_worker.edit.pointing import quick_ops
from stratum_worker.gateway import prompts
from stratum_worker.gateway.gateway import Gateway
from stratum_worker.gateway.prompts import VERSION
from stratum_worker.gateway.tasks import EditParseResult, TaskName

REPO = Path(__file__).resolve().parents[5]
CASES = REPO / "ml" / "chat" / "cases.jsonl"
REPORTS = REPO / "ml" / "chat" / "reports"
# The chat before M10i (the first editing set): prompt v6, with pointing. M10h's own
# baseline (v5, no pointing) is kept in ml/chat/edit_parse_v5.md for its report.
BASELINE = REPO / "ml" / "chat" / "edit_parse_v6.md"
BASELINE_VERSION = 6
OPS = TypeAdapter(list[EditOp])

# The fixed posters: a fictional client's words in one design of each family.
POSTERS: dict[str, tuple[str, str]] = {
    "panel": ("kowalski-plumbing", "ds.panel.1.0000000"),
    "overlay": ("kowalski-plumbing", "ds.overlay.1.000000"),
    "pair": ("kowalski-plumbing", "ds.pair.1.0000000"),
    "cover": ("kowalski-plumbing", "ds.cover.1.0001"),
    "type": ("kowalski-plumbing", "ds.type.1.000000"),
}
WORDS = Copy(
    headline="Boiler service, sorted before winter",
    offer="20% off every boiler service booked before 31 October.",
    cta="Call 0161 496 0000",
)
PHOTO_SIZE = (1600, 1200)  # the stand-in scenes
PHOTOS = 2  # the client's photos a restyle may use


@dataclass(frozen=True)
class Poster:
    name: str
    design_id: str
    spec: PosterSpec
    kit: BrandKit


@cache
def poster(name: str, root: Path = REPO) -> Poster:
    client, design_id = POSTERS[name]
    folder = root / "clients" / client
    kit = BrandKit.model_validate(json.loads((folder / "brand.json").read_text("utf-8")))
    with Image.open(folder / "logo.png") as logo:
        aspect = logo.width / logo.height
    laid = lay_out(design_id, kit, root, copy=WORDS, logo_aspect=aspect)
    if laid is None:
        raise ValueError(f"{design_id} does not lay out")
    return Poster(name, design_id, bind(laid, kit, WORDS, logo=ink(folder / "logo.png")), kit)


@dataclass(frozen=True)
class Case:
    id: str
    poster: str
    message: str
    expect: dict[str, Any]
    category: str = ""
    pointed: str | None = None
    recent: tuple[str, ...] = ()
    about: str = ""


def load_cases(path: Path = CASES) -> list[Case]:
    out = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["recent"] = tuple(row.get("recent", ()))
            out.append(Case(**row))
    return out


# --- matching ---------------------------------------------------------------------------


def _number(rule: str, value: object) -> bool:
    """`rule` is ">1", "<0", ">=0.2", or an exact value written as a string."""
    if not isinstance(value, int | float):
        return False
    for sign in (">=", "<=", ">", "<"):
        if rule.startswith(sign):
            limit = float(rule[len(sign) :])
            return {
                ">=": value >= limit,
                "<=": value <= limit,
                ">": value > limit,
                "<": value < limit,
            }[sign]
    return float(rule) == float(value)


def matches(want: dict[str, Any], got: dict[str, Any]) -> bool:
    """One expected op against one returned op."""
    if "any" in want:
        return any(matches(one, got) for one in want["any"])
    if got.get("op") != want["op"]:
        return False
    for key, rule in want.items():
        if key == "op":
            continue
        if key == "layer":
            allowed = rule if isinstance(rule, list) else [rule]
            if got.get("layer") not in allowed:
                return False
        elif key == "text_has":
            text = str(got.get("text", "")).casefold()
            if not all(part.casefold() in text for part in rule):
                return False
        elif key == "changes":
            changes = got.get("changes") or {}
            if not isinstance(changes, dict) or any(
                changes.get(name) != value for name, value in rule.items()
            ):
                return False
        elif isinstance(rule, str) and rule[:1] in "<>":
            if not _number(rule, got.get(key)):
                return False
        elif got.get(key) != rule:
            return False
    return True


def _layers_named(want: dict[str, Any]) -> set[str]:
    if "any" in want:
        return set().union(*(_layers_named(one) for one in want["any"]))
    rule = want.get("layer")
    if rule is None:
        return set()
    return set(rule if isinstance(rule, list) else [rule])


@dataclass(frozen=True)
class Answer:
    outcome: str  # "change", "ask", "refuse", "invalid", "failed"
    ops: list[dict[str, Any]]
    model: bool  # False when the quick path answered without a model
    note: str = ""


def answer_of(result: EditParseResult, spec: PosterSpec | None = None) -> Answer:
    """What the product would do with a model's answer (service.tweak's rules): ops
    that do not validate, or (given the poster) do not apply, are invalid; a logo
    recolour is dropped with a reason, as the editor drops it."""
    raw = [dict(one) for one in result.ops]
    try:
        spec_ops, service_ops = split(raw)
        ops = OPS.validate_python(spec_ops)
    except ValidationError:
        return Answer("invalid", raw, True)
    if any(isinstance(one, Ask) for one in service_ops):
        return Answer("ask", raw, True, result.needs_human or "")
    refused = ""
    if spec is not None:
        ops, refused = without_logo_recolours(spec, ops)
        try:
            apply_ops(spec, ops)
        except OpError:
            return Answer("invalid", raw, True)
    kept = [op.model_dump(mode="json") for op in ops] + [
        dict(one) for one in raw if one.get("op") in ("restyle", "undo", "badge")
    ]
    if not ops and not any(isinstance(one, Restyle | Undo | Badge) for one in service_ops):
        return Answer("refuse", kept, True, refused or result.needs_human or "")
    return Answer("change", kept, True, result.note or "")


def score(case: Case, answer: Answer) -> dict[str, Any]:
    expect = case.expect
    wanted = expect.get("outcome", "change")
    allowed = (
        ("change", "ask")
        if wanted == "change_or_ask"
        else tuple(wanted)
        if isinstance(wanted, list)
        else (wanted,)
    )
    outcome_ok = answer.outcome in allowed
    expected_ops = expect.get("ops", [])
    found = [any(matches(want, got) for got in answer.ops) for want in expected_ops]
    ops_ok = all(found) if answer.outcome == "change" else True
    named = set().union(*(_layers_named(want) for want in expected_ops)) if expected_ops else set()
    touched = {str(got["layer"]) for got in answer.ops if "layer" in got}
    extra = sorted(touched - named) if expect.get("only") and named else []
    return {
        "outcome": answer.outcome,
        "outcome_ok": outcome_ok,
        "ops_ok": ops_ok,
        "extra": extra,
        "model": answer.model,
        "passed": outcome_ok and ops_ok and not extra,
    }


# --- running ----------------------------------------------------------------------------


async def respond(
    gateway: Gateway,
    case: Case,
    *,
    pointing: bool = True,
    root: Path = REPO,
    newer: bool = True,
) -> tuple[Answer, CollectingTracer]:
    """What the product does with this message: a pointed short message is carried
    out without a model; anything else goes to the model."""
    shown = poster(case.poster, root)
    tracer = CollectingTracer(inner=gateway.tracer)
    pointed = case.pointed if pointing else None
    if pointed is not None:
        quick = quick_ops(shown.spec, pointed, case.message, newer=newer)
        if quick:
            ops = [one.model_dump(mode="json") for one in quick]
            return Answer("change", ops, False), tracer
    fits = measure(shown.spec, shown.kit, root=root)
    sizes = {"a" * 64: PHOTO_SIZE, "b" * 64: PHOTO_SIZE}
    request = edit_request(
        shown.spec,
        shown.kit,
        fits,
        sizes,
        template_id=shown.design_id,
        photos=PHOTOS,
        message=case.message,
        recent=[
            {"message": line.split(" -> ")[0], "outcome": line.split(" -> ")[-1]}
            for line in case.recent
        ],
        pointed=pointed,
    )
    traced = Gateway(router=gateway.router, budget=gateway.budget, tracer=tracer)
    result = await traced.run(TaskName.EDIT_PARSE, request, agency_id="chat-eval")
    assert isinstance(result, EditParseResult)
    return answer_of(result, shown.spec), tracer


@dataclass
class Report:
    version: int
    pointing: bool
    started: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        n = len(self.rows)
        passed = [row for row in self.rows if row["score"]["passed"]]
        by: dict[str, list[bool]] = {}
        for row in self.rows:
            by.setdefault(row["category"], []).append(row["score"]["passed"])
        outcomes = Counter(row["score"]["outcome"] for row in self.rows)
        with_model = [row for row in self.rows if row["score"]["model"]]
        return {
            "cases": n,
            "passed": round(len(passed) / n, 3) if n else None,
            "by_category": {key: round(sum(v) / len(v), 3) for key, v in sorted(by.items())},
            "outcomes": dict(sorted(outcomes.items())),
            "without_a_model": n - len(with_model),
            "median_seconds": sorted(row["seconds"] for row in with_model)[len(with_model) // 2]
            if with_model
            else None,
            "cost_usd": round(sum(row["cost_usd"] for row in self.rows), 5),
            "models": sorted({row["model"] for row in self.rows if row["model"]}),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": f"edit.parse v{self.version}",
            "pointing": self.pointing,
            "started": self.started,
            "summary": self.summary(),
            "rows": self.rows,
        }


async def run(
    gateway: Gateway, cases: list[Case], *, baseline: bool = False, root: Path = REPO
) -> Report:
    """Every case; with `baseline`, as the chat was before M10i: prompt v6, and the
    short pointed instructions it knew then."""
    if baseline:
        with prompts.overridden(TaskName.EDIT_PARSE, BASELINE.read_text("utf-8"), BASELINE_VERSION):
            return await _run(
                gateway, cases, version=BASELINE_VERSION, pointing=True, root=root, newer=False
            )
    return await _run(
        gateway, cases, version=VERSION[TaskName.EDIT_PARSE], pointing=True, root=root
    )


async def _run(
    gateway: Gateway,
    cases: list[Case],
    *,
    version: int,
    pointing: bool,
    root: Path,
    newer: bool = True,
) -> Report:
    report = Report(version, pointing, datetime.now(UTC).isoformat())
    for case in cases:
        began = time.perf_counter()
        try:
            answer, tracer = await respond(gateway, case, pointing=pointing, root=root, newer=newer)
        except Exception as exc:
            answer, tracer = Answer("failed", [], True, f"{type(exc).__name__}: {exc}"), None
        step = tracer.step("chat", TaskName.EDIT_PARSE) if tracer else None
        report.rows.append(
            {
                "id": case.id,
                "category": case.category,
                "poster": case.poster,
                "message": case.message,
                "pointed": case.pointed,
                "expect": case.expect,
                "ops": answer.ops,
                "note": answer.note,
                "score": score(case, answer),
                "model": step.model if step else "",
                "cost_usd": step.cost_usd if step else 0.0,
                "seconds": round(time.perf_counter() - began, 2),
            }
        )
    return report


def table(report: Report) -> str:
    summary = report.summary()
    lines = [
        f"edit.parse v{report.version} (pointing {'on' if report.pointing else 'off'}): "
        f"{summary['cases']} cases, models {', '.join(summary['models']) or '-'}, "
        f"${summary['cost_usd']}",
        f"passed: {summary['passed']}   outcomes: {summary['outcomes']}",
        f"answered without a model: {summary['without_a_model']}   median seconds with one: "
        f"{summary['median_seconds']}",
        "",
        "by category:",
        *(f"  {key:<12} {value}" for key, value in summary["by_category"].items()),
        "",
    ]
    for row in report.rows:
        score_ = row["score"]
        if not score_["passed"]:
            why = []
            if not score_["outcome_ok"]:
                why.append(f"{score_['outcome']} (wanted {row['expect'].get('outcome', 'change')})")
            if not score_["ops_ok"]:
                why.append("ops differ")
            if score_["extra"]:
                why.append(f"also touched {', '.join(score_['extra'])}")
            lines.append(f"- {row['id']}: {'; '.join(why)}")
            lines.append(f"    {row['message']!r} -> {json.dumps(row['ops'])[:160]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chat-eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, default=REPORTS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--baseline", action="store_true", help="prompt v5, no pointing")
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
    name = f"edit-parse-v{report.version}{'' if report.pointing else '-nopoint'}-{stamp}.json"
    path = args.out / name
    path.write_text(json.dumps(report.as_dict(), indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(table(report))
    print(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BASELINE",
    "CASES",
    "POSTERS",
    "Answer",
    "Case",
    "Report",
    "answer_of",
    "load_cases",
    "matches",
    "poster",
    "respond",
    "run",
    "score",
    "table",
]
