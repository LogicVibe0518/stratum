"""Log lines that Railway reads correctly: JSON on stdout, with a level.

Python's logging writes to stderr by default, and Railway files everything on stderr
as an error. So every line - "Application startup complete", "4 workers waiting
for jobs" - showed up red, and a real error looked like all the others. On Railway
each line is now one JSON object on stdout whose "level" Railway understands; on a
laptop it stays a short readable line.

Never log secrets, tokens or client images (AGENTS.md); this module only formats.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

# Python's level names -> the ones Railway's log viewer understands.
LEVELS = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}


class JsonLines(logging.Formatter):
    """One JSON object per record: level, message, logger, time (and error, if any)."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, str] = {
            "level": LEVELS.get(record.levelname, "info"),
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
        }
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def on_railway() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT"))


def configure(level: int = logging.INFO) -> None:
    """Every log line to stdout: JSON on Railway, a short line anywhere else."""
    handler = logging.StreamHandler(sys.stdout)
    if on_railway():
        handler.setFormatter(JsonLines())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


__all__ = ["LEVELS", "JsonLines", "configure", "on_railway"]
