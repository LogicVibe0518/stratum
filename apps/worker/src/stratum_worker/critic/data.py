"""Training groups for the critic, made by the renderer from fictional material only.

A group is one design, for one brand, with one set of words, in one size, on one
photo - plus the damaged copies of it (critic/damage.py). Everything is fictional
and reproducible from a seed: the five benchmark brand kits, the copy below, and
procedural photos. No client's work, no scraped designs, no dataset licence.

Splits are by BRAND and by COPY, never by row: a group in the test split was made
with a brand kit and words the model never saw in training, so a good test score
cannot come from memorising a colour scheme or a headline.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from stratum_spec import BrandKit, embed, load_template, load_variants, retarget
from stratum_spec.models import Aspect, PosterSpec

from stratum_worker.critic.damage import DAMAGES, LEVELS
from stratum_worker.critic.features import FEATURES, edge_map, measure
from stratum_worker.design.preview import FAKE_SHA, scene
from stratum_worker.design.space import SIZES, everything, lay_out
from stratum_worker.design.typeset import Copy
from stratum_worker.draft.bindings import BINDINGS
from stratum_worker.draft.compose import ComposeError, build_spec

# Fictional copy across the trades the product is for. Lengths vary on purpose:
# a design that is good with a four-word headline may be poor with a ten-word one.
COPIES: tuple[Copy, ...] = (
    Copy("Boiler service, sorted", "20% off every service booked this month", "Call 0161 496 0000"),
    Copy(
        "Winter-proof your roof",
        "Free inspection for homes in Plano until 30 Nov",
        "Book online today",
    ),
    Copy("Fresh cuts, sharp edges", "Weekly lawn care from $35 a visit", "Text us for a quote"),
    Copy(
        "Sunday thali is back",
        "Unlimited refills, 12 to 4, every Sunday",
        "Walk in or book a table",
    ),
    Copy("Spotless in three hours", "Deep clean for a two-bed flat, £89", "Book your slot"),
    Copy(
        "Meditation camp for self-discovery",
        "5 Nov 2026 to 1 Feb 2027. Women and under-18s get 50% off.",
        "Book your place today",
    ),
    Copy(
        "New bathroom, no fuss",
        "Fitted in five days, or the sixth is free",
        "Get a free design visit",
    ),
    Copy("Leak? We're on the way", "Emergency plumber, 24 hours, 7 days", "Call now"),
    Copy(
        "Storm damage? Call the roofers who stay",
        "Insurance claims handled for you, start to finish",
        "Free roof check",
    ),
    Copy(
        "Gardens that look after themselves",
        "Planting plans from £150, with a year of care advice",
        "Ask about a plan",
    ),
    Copy(
        "Diwali sweets, made fresh daily", "Order boxes of 12 or 24 by 30 October", "Order in store"
    ),
    Copy(
        "End of tenancy? Get the deposit back",
        "Landlord-ready cleans, checklist included",
        "Book this week",
    ),
    Copy(
        "Heat pumps, fitted properly",
        "Up to £7,500 off with the government grant",
        "Check if you qualify",
    ),
    Copy(
        "Gutters cleared before the leaves",
        "Autumn clean from $99, photos before and after",
        "Reserve a date",
    ),
    Copy("Wedding flowers for every budget", "Free consultation for 2027 dates", "Message us"),
    Copy("Clean ovens, happy cooks", "Oven valet from £55, done in two hours", "Book online"),
)


@dataclass(frozen=True)
class Split:
    name: str
    kits: tuple[str, ...]
    copies: tuple[int, ...]


SPLITS = (
    Split("train", ("kowalski-plumbing", "bloom-and-blade", "sharmas-dhaba"), tuple(range(0, 10))),
    Split("valid", ("lone-star-roofing",), (10, 11, 12)),
    Split("test", ("sparkle-squad",), (13, 14, 15)),
)


@dataclass(frozen=True)
class Group:
    """One design and damaged copies of it, ranked.

    Two kinds, because the two questions are different:
    - "broken": the untouched design (label 1) against one copy per kind of damage
      (label 0). Is any damage worse than none? The labels say nothing about whether
      a crooked line is worse than small print - nobody could say that in general.
    - "severity": the untouched design and one kind of damage at three strengths,
      labelled 3, 2, 1, 0. Is more of the same damage worse?
    """

    key: str
    split: str
    kind: str  # "broken" | "severity"
    family: str
    aspect: Aspect
    features: np.ndarray  # (rows, len(FEATURES))
    labels: np.ndarray  # (rows,)
    damages: tuple[str, ...]  # "" for the untouched design


def kits(root: Path) -> dict[str, BrandKit]:
    return {
        path.parent.name: BrandKit.model_validate(json.loads(path.read_text()))
        for path in sorted((root / "clients").glob("*/brand.json"))
    }


def _templates(root: Path, copy: Copy, kit: BrandKit) -> Iterator[tuple[str, PosterSpec]]:
    """The nine hand-made templates too, so the critic sees more than one style."""
    for template_id, binding in BINDINGS.items():
        path = root / "templates" / f"{template_id}.json"
        template = embed(load_template(path), load_variants(path))
        words = {"headline": copy.headline, "offer_detail": copy.offer, "cta": copy.cta}
        texts = {layer: words[field] for layer, field in binding.texts.items()}
        texts.update(binding.fixed or {})
        assets = {layer: FAKE_SHA["photo_a"] for layer in binding.photos}
        if binding.logo:
            assets[binding.logo] = FAKE_SHA["logo"]
        if len(binding.photos) > 1:
            assets[list(binding.photos)[1]] = FAKE_SHA["photo_b"]
        try:
            yield template_id, build_spec(template, kit, texts, assets)
        except ComposeError:
            continue


def _designs(
    root: Path, copy: Copy, kit: BrandKit, rng: random.Random, count: int
) -> list[tuple[str, PosterSpec]]:
    pool = list(everything())
    rng.shuffle(pool)
    chosen: list[tuple[str, PosterSpec]] = []
    for design in pool:
        laid = lay_out(design.id, kit, root, copy=copy)
        if laid is None:
            continue
        texts = {"headline": copy.headline, "offer": copy.offer, "cta": copy.cta}
        ids = {layer.id for layer in laid.layers}
        assets = {lid: sha for lid, sha in FAKE_SHA.items() if lid in ids}
        chosen.append((design.family.name, build_spec(laid, kit, texts, assets)))
        if len(chosen) == count:
            break
    return chosen


def groups(
    root: Path,
    split: Split,
    *,
    designs_per_copy: int,
    templates: bool = True,
    seed: int = 0,
    scenes: int = 6,
    exclude_family: str | None = None,
    only_family: str | None = None,
) -> Iterator[Group]:
    """Every group for one split. Deterministic for a given seed."""
    rng = random.Random(f"{seed}-{split.name}")  # noqa: S311 - reproducible data, not secrets
    all_kits = kits(root)
    photos = [np.asarray(scene(1000 * seed + i)) for i in range(scenes * 2)]
    edge_sets = [
        {
            FAKE_SHA["photo_a"]: edge_map(photos[2 * i]),
            FAKE_SHA["photo_b"]: edge_map(photos[2 * i + 1]),
        }
        for i in range(scenes)
    ]
    for kit_name in split.kits:
        kit = all_kits[kit_name]
        for copy_index in split.copies:
            copy = COPIES[copy_index]
            candidates = _designs(root, copy, kit, rng, designs_per_copy)
            if templates:
                candidates += list(_templates(root, copy, kit))
            for family, spec in candidates:
                family = family if family in ("panel", "overlay", "pair", "type") else "template"
                if exclude_family and family == exclude_family:
                    continue
                if only_family and family != only_family:
                    continue
                aspect = rng.choice(SIZES)
                edges = rng.choice(edge_sets)
                key = f"{split.name}/{kit_name}/{copy_index}/{spec.template_id}/{aspect}"
                yield from _groups(spec, kit, root, rng, edges, aspect, split.name, family, key)


def _groups(
    spec: PosterSpec,
    kit: BrandKit,
    root: Path,
    rng: random.Random,
    edges: dict[str, np.ndarray],
    aspect: Aspect,
    split: str,
    family: str,
    key: str,
) -> Iterator[Group]:
    one = retarget(spec, aspect)
    base = measure(one, kit, root, edges=edges)

    def group(kind: str, rows: list[np.ndarray], labels: list[int], names: list[str]) -> Group:
        return Group(
            key=f"{key}/{kind}",
            split=split,
            kind=kind,
            family=family,
            aspect=aspect,
            features=np.vstack(rows),
            labels=np.asarray(labels, dtype=np.int64),
            damages=tuple(names),
        )

    rows, labels, names = [base], [1], [""]
    for name, damage in DAMAGES.items():
        level = rng.choice(LEVELS)
        broken = damage(one, level, rng, kit)
        if broken is not None:
            rows.append(measure(broken, kit, root, edges=edges))
            labels.append(0)
            names.append(f"{name}:{level}")
    if len(rows) > 1:
        yield group("broken", rows, labels, names)

    kinds = list(DAMAGES)
    rng.shuffle(kinds)
    for name in kinds:
        graded = [(level, DAMAGES[name](one, level, rng, kit)) for level in LEVELS]
        if any(broken is None for _, broken in graded):
            continue
        yield group(
            "severity",
            [base, *(measure(b, kit, root, edges=edges) for _, b in graded if b is not None)],
            [3, *(3 - level for level, _ in graded)],
            ["", *(f"{name}:{level}" for level, _ in graded)],
        )
        break


__all__ = ["COPIES", "FEATURES", "SPLITS", "Group", "Split", "groups", "kits"]
