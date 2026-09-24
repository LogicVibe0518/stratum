"""AGENTS.md rule 5: application code never imports a vendor SDK directly.

Vendor SDKs and concrete provider modules may only be imported inside
gateway/providers/. Everything else talks to the gateway, or builds one through
providers.registry.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDERS_DIR = REPO_ROOT / "apps/worker/src/stratum_worker/gateway/providers"
SOURCE_ROOTS = [*REPO_ROOT.glob("apps/*/src"), *REPO_ROOT.glob("packages/*/src"), REPO_ROOT / "ml"]

VENDOR_SDKS = {
    "anthropic",
    "cohere",
    "elevenlabs",
    "fal",
    "fal_client",
    "google.genai",
    "google.generativeai",
    "groq",
    "huggingface_hub",
    "langfuse",
    "litellm",
    "mistralai",
    "openai",
    "replicate",
    "stability_sdk",
    "together",
}
PROVIDERS_PKG = "stratum_worker.gateway.providers"
# The Protocol and the factory are the public face of providers/; everything else
# in there is an implementation detail.
ALLOWED_PROVIDER_MODULES = {f"{PROVIDERS_PKG}.base", f"{PROVIDERS_PKG}.registry"}


def _imported_modules(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _is_vendor(module: str) -> bool:
    return any(module == sdk or module.startswith(f"{sdk}.") for sdk in VENDOR_SDKS)


def _is_forbidden_provider(module: str) -> bool:
    if not module.startswith(f"{PROVIDERS_PKG}."):
        return False
    top = ".".join(module.split(".")[:4])  # stratum_worker.gateway.providers.<module>
    return top not in ALLOWED_PROVIDER_MODULES


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if ".venv" not in p.parts)
    return files


def test_scan_actually_covers_the_codebase() -> None:
    files = _source_files()
    assert any(p.name == "gateway.py" for p in files)
    assert any(p.is_relative_to(PROVIDERS_DIR) for p in files)


def test_no_vendor_sdk_or_provider_module_outside_providers() -> None:
    offenders: list[str] = []
    for path in _source_files():
        if path.is_relative_to(PROVIDERS_DIR):
            continue
        for module in _imported_modules(ast.parse(path.read_text(encoding="utf-8"))):
            if _is_vendor(module) or _is_forbidden_provider(module):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")
    assert offenders == [], "\n".join(offenders)


def test_the_check_catches_offenders() -> None:
    tree = ast.parse(
        "import openai\n"
        "from langfuse import Langfuse\n"
        "from stratum_worker.gateway.providers.openrouter import OpenRouterProvider\n"
        "from stratum_worker.gateway.providers import fal_images\n"
        "from stratum_worker.gateway.providers.base import Provider\n"
    )
    modules = _imported_modules(tree)
    flagged = {m for m in modules if _is_vendor(m) or _is_forbidden_provider(m)}
    assert "openai" in flagged
    assert "langfuse" in flagged
    assert f"{PROVIDERS_PKG}.openrouter" in flagged
    assert f"{PROVIDERS_PKG}.fal_images" in flagged
    assert f"{PROVIDERS_PKG}.base" not in flagged
