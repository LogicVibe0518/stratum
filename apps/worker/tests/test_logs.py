"""Log lines Railway can read: JSON with a level, on stdout, only on Railway."""

from __future__ import annotations

import json
import logging

import pytest
from stratum_worker import logs


def _record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord("stratum.queue", level, __file__, 1, message, None, None)


def test_a_warning_is_one_json_line_railway_calls_warn() -> None:
    line = json.loads(logs.JsonLines().format(_record(logging.WARNING, "slow job")))
    assert line["level"] == "warn"
    assert line["message"] == "slow job"
    assert line["logger"] == "stratum.queue"


def test_an_info_line_is_info_not_error() -> None:
    assert json.loads(logs.JsonLines().format(_record(logging.INFO, "idle")))["level"] == "info"


def test_on_railway_lines_go_to_stdout_as_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        logs.configure()
        logging.getLogger("stratum.test").info("4 workers waiting")
        out, err = capsys.readouterr()
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
    assert err == ""
    assert json.loads(out.strip().splitlines()[-1])["message"] == "4 workers waiting"


def test_on_a_laptop_lines_stay_short_and_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        logs.configure()
        logging.getLogger("stratum.test").info("4 workers waiting")
        out, _ = capsys.readouterr()
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
    assert out.strip().endswith("INFO 4 workers waiting")
