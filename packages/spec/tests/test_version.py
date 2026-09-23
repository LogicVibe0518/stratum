from __future__ import annotations

import stratum_spec


def test_public_api_is_exported() -> None:
    for name in ("PosterSpec", "apply_ops", "load", "SCHEMA_VERSION"):
        assert hasattr(stratum_spec, name), name
