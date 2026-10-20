"""Structured application logging.

Logs go to a rotating file and, optionally, to stderr.  Every record is written as
a single JSON object so the log can be parsed by a script instead of being read by
eye when something goes wrong.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Optional

CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the logger name and level included."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "time": _dt.datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        stamp = _dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{stamp} {record.levelname:<7} {record.name}: {record.getMessage()}"


def configure_logging(settings=None, *, to_stderr: bool = False,
                      force: bool = False) -> Optional[Path]:
    """Install the rotating file handler described by the settings.

    Returns the log file path (or ``None`` when a file could not be created).
    Safe to call more than once.
    """
    global CONFIGURED
    if CONFIGURED and not force:
        return _log_path(settings)

    level_name = "INFO"
    max_bytes = 2 * 1024 * 1024
    backup_count = 3
    path: Optional[Path] = None

    if settings is not None:
        try:
            level_name = str(settings.get("logging.level", "INFO")).upper()
            max_bytes = int(settings.get("logging.max_bytes", max_bytes))
            backup_count = int(settings.get("logging.backup_count", backup_count))
            path = settings.log_file()
        except Exception:  # pragma: no cover - settings must never break logging
            level_name, max_bytes, backup_count = "INFO", max_bytes, backup_count
    else:
        from database.db import PROJECT_ROOT

        path = PROJECT_ROOT / "logs" / "capital.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
            file_handler.setFormatter(HumanFormatter())
            root.addHandler(file_handler)
            # a machine-readable twin so automated checks can parse the trail
            json_handler = logging.handlers.RotatingFileHandler(
                path.with_suffix(".jsonl"), maxBytes=max_bytes,
                backupCount=backup_count, encoding="utf-8")
            json_handler.setFormatter(JsonFormatter())
            root.addHandler(json_handler)
        except OSError:  # pragma: no cover - read-only filesystem
            path = None

    if to_stderr or path is None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(HumanFormatter())
        root.addHandler(stream)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    CONFIGURED = True
    logging.getLogger("capital").info(
        "Logging configured (level=%s, file=%s)", level_name, path)
    return path


def _log_path(settings) -> Optional[Path]:
    try:
        return settings.log_file() if settings is not None else None
    except Exception:  # pragma: no cover
        return None


__all__ = ["configure_logging", "JsonFormatter", "HumanFormatter"]
