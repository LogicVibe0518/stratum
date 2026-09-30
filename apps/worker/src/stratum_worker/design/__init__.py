"""The design space (M11a): parametric layout families that make many valid
candidate designs for one post, laid out around the real words."""

from stratum_worker.design.families import FAMILIES, Family
from stratum_worker.design.space import (
    PRESETS,
    SIZES,
    Design,
    UnknownDesign,
    binding_parts,
    decode,
    design_id,
    everything,
    is_design,
    lay_out,
)
from stratum_worker.design.typeset import PLACEHOLDER, Copy

__all__ = [
    "FAMILIES",
    "PLACEHOLDER",
    "PRESETS",
    "SIZES",
    "Copy",
    "Design",
    "Family",
    "UnknownDesign",
    "binding_parts",
    "decode",
    "design_id",
    "everything",
    "is_design",
    "lay_out",
]
