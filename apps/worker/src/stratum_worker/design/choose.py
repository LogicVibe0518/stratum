"""The top three (M12b): many designs laid out around this post's words and photos,
three different ones shown, and a record of exactly what was shown and how likely.

A designer choosing between three good, different options is faster than editing
one, and every choice is a label the critic can later learn taste from (M15). For
those labels to be usable, two things are logged with every option: where it was
shown, and the probability that this policy would have shown it (its propensity).
Without the propensity, a model retrained on picks only ever learns to prefer what
it already showed.

How the three are chosen, for one post:

1. Candidates: every design the post can fill. Two-photo designs need two real
   photos; one-photo designs need a photo or the post's generated background;
   type-only designs need none. Up to MAX_CANDIDATES, sampled evenly across
   families with a seed taken from the post, so the same post always gets the same
   candidates.
2. A first score in the post's own size, for every candidate: laid out around the
   real words, the logo panel fitted, the photo framed (M10b), then
       score = critic, as a z-score across this post's candidates
               - PHOTO_WEIGHT x how badly the photo sits (framing cost)
   A design with any blocker (text off the canvas, a hidden logo, ...) is out, and
   a words-only design starts NO_PHOTO_SHOWN behind when the client sent photos.
3. The best SHORTLIST (and at least the best FAMILY_FLOOR of each family) are framed
   and scored again in every size the post needs, and averaged. A design that is
   fine as a feed post but cuts a face in the story falls here.
4. Three from different families: the best, the best of another family, and the
   best of a third. With probability EXPLORE the third is instead drawn at random
   from the shortlist, so the picks also teach about designs the ranking does not
   yet favour.
5. The display order is shuffled (seeded), and recorded, so a preference for "the
   first one" can be measured and removed later.

Pure: no database, no network. The service does the reading and the saving.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from stratum_spec import BrandKit, PosterSpec, retarget
from stratum_spec.models import Aspect

from stratum_worker.critic.features import measure
from stratum_worker.critic.ranker import score
from stratum_worker.design.copyfit import best_copy, versions
from stratum_worker.design.space import everything
from stratum_worker.design.typeset import Copy
from stratum_worker.draft.bindings import binding_for
from stratum_worker.draft.compose import build_spec
from stratum_worker.draft.logo_fit import LogoInk, fit_logo
from stratum_worker.draft.preflight import check_layout
from stratum_worker.photo.frame import frame_photos, framing_cost
from stratum_worker.photo.read import PhotoRead

# The template id a post carries when the designer lets Stratum choose.
AUTO = "best_three"

MAX_CANDIDATES = 240
SHORTLIST = 24
FAMILY_FLOOR = 4
EXPLORE = 0.15
# One unit of framing cost is worth half a standard deviation of critic score. A
# face under the words costs about 12: such a design is out unless nothing else is.
PHOTO_WEIGHT = 0.5
# A client who sent photos of their own work almost always wants them on the post:
# a words-only design is still offered, but starts this far behind.
NO_PHOTO_SHOWN = 0.75


class NothingFits(RuntimeError):
    """No design could be laid out around these words with this material."""


@dataclass(frozen=True)
class Material:
    """Everything a design is filled with, decided before any design is chosen."""

    words: dict[str, str]  # PostCopy field -> text: headline, offer_detail, cta
    kit: BrandKit
    photos: tuple[str, ...]  # real photos, best first (asset ids)
    background: str | None  # a generated background, when there is no real photo
    logo: str  # asset id
    logo_aspect: float
    ink: LogoInk | None
    reads: Mapping[str, PhotoRead]
    edges: Mapping[str, np.ndarray] = field(default_factory=dict)

    @property
    def copy(self) -> Copy:
        return Copy(
            headline=self.words.get("headline", ""),
            offer=self.words.get("offer_detail", ""),
            cta=self.words.get("cta", ""),
        )


@dataclass(frozen=True)
class Candidate:
    design_id: str
    family: str
    spec: PosterSpec  # filled, framed, with every size the post needs
    score: float  # higher is better; comparable only within one post
    parts: dict[str, float]


@dataclass(frozen=True)
class Option:
    candidate: Candidate
    slot: int  # 1 = the ranking's best
    position: int  # where it is shown, 0 = first
    propensity: float  # P(this policy shows this design in this slot)
    explored: bool


def seed_for(post_id: str) -> int:
    """A stable seed from the post: the same post always gets the same draws."""
    return int.from_bytes(hashlib.sha256(post_id.encode()).digest()[:8], "big")


def family_of(design_id: str) -> str:
    return design_id.split(".")[1]


def eligible(material: Material) -> list[str]:
    """Design ids this material can fill, in the design space's own order."""
    out = []
    for design in everything():
        roles = list(binding_for(design.id).photos.values())
        if roles.count("photo") > len(material.photos):
            continue  # real photos are never invented
        if "background" in roles and not (material.photos or material.background):
            continue
        out.append(design.id)
    return out


def sample(design_ids: Sequence[str], rng: random.Random, limit: int) -> list[str]:
    """Up to `limit` designs, as even across families as the families allow."""
    by_family: dict[str, list[str]] = {}
    for design_id in design_ids:
        by_family.setdefault(family_of(design_id), []).append(design_id)
    for members in by_family.values():
        rng.shuffle(members)
    picked: list[str] = []
    while len(picked) < limit and any(by_family.values()):
        for family in sorted(by_family):
            if by_family[family] and len(picked) < limit:
                picked.append(by_family[family].pop())
    return picked


def fill(
    design_id: str, material: Material, aspects: Sequence[Aspect], root: Path
) -> PosterSpec | None:
    """The design laid out around the words and filled with this post's material, or
    None if the words do not fit it at a readable size.

    When the copy has headlines of several lengths, the design gets the one it sets
    best (design/copyfit.py)."""
    options = versions(dict(material.words)) or [(material.copy, True)]
    found = best_copy(design_id, options, material.kit, root, logo_aspect=material.logo_aspect)
    if found is None:
        return None
    chosen, laid = found
    words = {**material.words, "headline": chosen.headline}
    binding = binding_for(design_id)
    texts = {layer: words.get(field_name, "") for layer, field_name in binding.texts.items()}
    texts.update(binding.fixed or {})
    photos = list(material.photos)
    assets: dict[str, str] = {}
    for layer, role in binding.photos.items():
        if role == "photo":
            assets[layer] = photos.pop(0)
        else:  # background: the best real photo, else the generated one
            assets[layer] = material.photos[0] if material.photos else str(material.background)
    if binding.logo:
        assets[binding.logo] = material.logo
    spec = build_spec(laid, material.kit, texts, assets)
    if binding.logo and material.ink is not None:
        spec = fit_logo(spec, material.kit, material.ink, {}, list(aspects))
    return spec


def _hides_photos(design_id: str, material: Material) -> float:
    """1 if the client sent photos and this design shows none of them."""
    return float(bool(material.photos) and not binding_for(design_id).photos)


def _z(values: np.ndarray) -> np.ndarray:
    spread = float(values.std())
    out: np.ndarray = (values - values.mean()) / spread if spread > 1e-9 else values * 0.0
    return out


def _judge(
    specs: Sequence[PosterSpec],
    aspect: Aspect,
    material: Material,
    root: Path,
    logos: Mapping[str, LogoInk],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Critic scores, framing costs and "has a blocker" for these posters in one size."""
    ones = [retarget(spec, aspect) if spec.canvas.aspect != aspect else spec for spec in specs]
    edges = dict(material.edges)
    features = np.array([measure(one, material.kit, root, edges=edges) for one in ones])
    critic = score(features) if len(ones) else np.zeros(0)
    cost = np.array([framing_cost(one, material.reads, material.kit, root) for one in ones])
    blocked = np.array(
        [
            any(
                flag.severity == "blocker"
                for flag in check_layout(one, material.kit, root=root, logos=logos)
            )
            for one in ones
        ],
        dtype=bool,
    )
    return np.asarray(critic, dtype=np.float64), cost, blocked


def candidates(
    material: Material, aspects: Sequence[Aspect], root: Path, *, seed: int
) -> list[Candidate]:
    """Every candidate that survived both rounds, best first."""
    rng = random.Random(seed)  # noqa: S311 - reproducible draws, not a secret
    logos = {material.logo: material.ink} if material.ink is not None else {}
    ids = sample(eligible(material), rng, MAX_CANDIDATES)
    filled: list[tuple[str, PosterSpec]] = []
    for design_id in ids:
        spec = fill(design_id, material, aspects, root)
        if spec is None:
            continue
        base = spec.canvas.aspect
        filled.append((design_id, frame_photos(spec, material.reads, material.kit, root, [base])))
    if not filled:
        raise NothingFits("These words do not fit any design. Shorten the headline or the offer.")

    # Round 1: the post's own size, every candidate.
    base = filled[0][1].canvas.aspect
    critic, cost, blocked = _judge([spec for _, spec in filled], base, material, root, logos)
    unused = np.array([_hides_photos(design_id, material) for design_id, _ in filled])
    first = _z(critic) - PHOTO_WEIGHT * cost - NO_PHOTO_SHOWN * unused
    first[blocked] = -np.inf
    order = [int(i) for i in np.argsort(-first, kind="stable") if np.isfinite(first[i])]
    if not order:
        raise NothingFits("Every design that fits these words breaks a layout rule.")
    keep = order[:SHORTLIST]
    for family in sorted({family_of(design_id) for design_id, _ in filled}):
        keep += [i for i in order if family_of(filled[i][0]) == family][:FAMILY_FLOOR]
    keep = list(dict.fromkeys(keep))

    # Round 2: framed in every size the post needs, judged in each, averaged.
    sizes = list(dict.fromkeys([base, *aspects]))
    full = [frame_photos(filled[i][1], material.reads, material.kit, root, sizes) for i in keep]
    totals = np.zeros(len(full))
    critic_mean = np.zeros(len(full))
    cost_mean = np.zeros(len(full))
    out_of_play = np.zeros(len(full), dtype=bool)
    for aspect in sizes:
        critic, cost, blocked = _judge(full, aspect, material, root, logos)
        totals += _z(critic) - PHOTO_WEIGHT * cost
        critic_mean += critic / len(sizes)
        cost_mean += cost / len(sizes)
        out_of_play |= blocked
    totals /= len(sizes)
    totals -= NO_PHOTO_SHOWN * np.array([_hides_photos(filled[i][0], material) for i in keep])
    ranked = [
        Candidate(
            design_id=filled[i][0],
            family=family_of(filled[i][0]),
            spec=spec,
            score=float(totals[n]),
            parts={
                "critic": round(float(critic_mean[n]), 4),
                "framing_cost": round(float(cost_mean[n]), 4),
                "round1": round(float(first[i]), 4),
            },
        )
        for n, (i, spec) in enumerate(zip(keep, full, strict=True))
        if not out_of_play[n]
    ]
    if not ranked:
        raise NothingFits("Every design that fits these words breaks a layout rule in some size.")
    ranked.sort(key=lambda one: one.score, reverse=True)
    return ranked


def choose(ranked: Sequence[Candidate], *, seed: int, explore: float = EXPLORE) -> list[Option]:
    """Three options from `ranked` (best first), different families where possible."""
    rng = random.Random(seed ^ 0x5EED)  # noqa: S311 - reproducible draws, not a secret
    greedy: list[Candidate] = []
    for candidate in ranked:
        if len(greedy) == 3:
            break
        if all(candidate.family != other.family for other in greedy):
            greedy.append(candidate)
    for candidate in ranked:  # fewer than three families: fill with the next best
        if len(greedy) == 3:
            break
        if candidate not in greedy:
            greedy.append(candidate)

    picks = list(greedy)
    explored = False
    propensities = [1.0] * len(picks)
    if len(picks) == 3:
        pool = [one for one in ranked if one not in picks[:2]]
        fresh = [one for one in pool if one.family not in {picks[0].family, picks[1].family}]
        pool = fresh or pool
        uniform = 1.0 / len(pool)
        if rng.random() < explore:
            picks[2] = rng.choice(pool)
            explored = picks[2] is not greedy[2]
        greedy_share = (1.0 - explore) if picks[2] is greedy[2] else 0.0
        drawn_share = explore * uniform if picks[2] in pool else 0.0
        propensities[2] = greedy_share + drawn_share

    positions = list(range(len(picks)))
    rng.shuffle(positions)
    return [
        Option(
            candidate=candidate,
            slot=slot,
            position=positions[slot - 1],
            propensity=round(propensities[slot - 1], 6),
            explored=explored and slot == 3,
        )
        for slot, candidate in enumerate(picks, start=1)
    ]


__all__ = [
    "AUTO",
    "EXPLORE",
    "Candidate",
    "Material",
    "NothingFits",
    "Option",
    "candidates",
    "choose",
    "eligible",
    "family_of",
    "fill",
    "sample",
    "seed_for",
]
