"""Loading template files.

A template is just a PosterSpec with placeholder text and no assets bound yet.
Keeping templates as data (not code) means new layouts ship without a deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

from stratum_spec.migrate import load
from stratum_spec.models import PosterSpec


def load_template(path: Path) -> PosterSpec:
    """Read one template file, upgrading it if it was written by an older schema."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a template object")
    return load(data)


def load_templates(directory: Path) -> dict[str, PosterSpec]:
    """Load every template in a directory, keyed by template_id."""
    templates: dict[str, PosterSpec] = {}
    for path in sorted(directory.glob("*.json")):
        template = load_template(path)
        if template.template_id in templates:
            raise ValueError(f"duplicate template_id {template.template_id!r} in {path}")
        templates[template.template_id] = template
    return templates
