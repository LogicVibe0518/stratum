from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError
from stratum_worker.gateway import TaskName, TaskPolicy
from stratum_worker.gateway.config import default_config, load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = REPO_ROOT / "config" / "gateway.toml"


def test_defaults_are_valid_and_cover_every_task() -> None:
    config = default_config()
    assert set(config.tasks) == set(TaskName)
    for task, policy in config.policies().items():
        assert isinstance(policy, TaskPolicy)
        assert policy.order == config.tasks[task].order


def test_shipped_file_matches_the_defaults() -> None:
    """The TOML is the human-facing copy; the two must not drift."""
    assert load_config(SHIPPED) == default_config()


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml") == default_config()


def test_file_overrides_are_merged_over_defaults(tmp_path: Path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(
        """
[tasks."caption.generate"]
canary = "openai"
canary_percent = 10

[models.openrouter]
"caption.generate" = "vendor/new-model"

[prices."vendor/new-model"]
input_per_mtok = 1.0
output_per_mtok = 2.0
""",
        encoding="utf-8",
    )
    config = load_config(path)
    caption = config.tasks[TaskName.CAPTION_GENERATE]
    assert caption.order == ("openrouter", "openai")  # kept from defaults
    assert (caption.canary, caption.canary_percent) == ("openai", 10)
    assert config.model("openrouter", TaskName.CAPTION_GENERATE) == "vendor/new-model"
    assert config.model("openrouter", TaskName.EDIT_PARSE) == "openai/gpt-6-luna"
    assert config.policies()[TaskName.CAPTION_GENERATE].canary_percent == 10


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[models.openrouter]\n"edit.parse" = "unpriced/model"\n', "has no price"),
        ('[tasks."edit.parse"]\norder = ["nobody"]\n', "no model configured"),
        ('[tasks."edit.parse"]\ncanary_percent = 5\n', "no canary"),
        ('[tasks."edit.parse"]\ncanary_percent = 150\n', "less than or equal to 100"),
        ('[tasks."edit.parse"]\nunknown_key = 1\n', "Extra inputs"),
    ],
)
def test_invalid_config_fails_loudly(tmp_path: Path, toml: str, message: str) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ValidationError, match=message):
        load_config(path)


def test_broken_toml_is_an_error_not_defaults(tmp_path: Path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text("[tasks\n", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(path)
