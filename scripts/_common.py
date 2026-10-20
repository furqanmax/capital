#!/usr/bin/env python3
"""Shared helpers for the command-line scripts.

Every script is runnable directly (``python3 scripts/foo.py``) without installing
the package, so this module makes the project root importable first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_WORKBOOK = PROJECT_ROOT / "data" / "Saif-Stock Market Data File-Sample.xlsm"
DOCS_DIR = PROJECT_ROOT / "docs"


def add_common_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--db", default=None,
                        help="SQLite database file (default: the configured one)")
    return parser


def add_workbook_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK),
                        help=f"source workbook (default: {DEFAULT_WORKBOOK.name})")
    return parser


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


__all__ = [
    "PROJECT_ROOT", "DEFAULT_WORKBOOK", "DOCS_DIR",
    "add_common_arguments", "add_workbook_arguments", "banner", "fail",
]
