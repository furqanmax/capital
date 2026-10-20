"""The command-line entry points are part of the deliverable.

Each script must at least be importable and answer ``--help``; the ones that can
run without a workbook or a database are exercised for real in a temporary
directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"

HELP_SCRIPTS = [
    "init_db.py",
    "inspect_workbook.py",
    "audit_formulas.py",
    "import_workbook.py",
    "parity_check.py",
    "export_report.py",
    "run_app.py",
]


def run(script: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=timeout,
    )


@pytest.mark.parametrize("script", HELP_SCRIPTS)
def test_scripts_answer_help(script: str) -> None:
    result = run(script, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


def test_init_db_creates_the_schema(tmp_path: Path) -> None:
    target = tmp_path / "cli.db"
    result = run("init_db.py", "--db", str(target))
    assert result.returncode == 0, result.stderr
    assert target.exists()

    from database.db import Database

    database = Database(target)
    assert database.table_names()
    assert database.summary_counts()["records"] == 0


def test_export_report_writes_a_file(tmp_path: Path, workbook_path: Path) -> None:
    target = tmp_path / "cli.db"
    run("init_db.py", "--db", str(target))
    imported = run("import_workbook.py", "--db", str(target),
                   "--workbook", str(workbook_path), "--sheet", "Data")
    assert imported.returncode == 0, imported.stderr

    out = tmp_path / "screener.csv"
    result = run("export_report.py", "--db", str(target), "--kind", "screener",
                 "--format", "csv", "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert "Symbol" in out.read_text(encoding="utf-8-sig")


def test_export_report_refuses_an_empty_report(tmp_path: Path) -> None:
    target = tmp_path / "cli.db"
    run("init_db.py", "--db", str(target))
    result = run("export_report.py", "--db", str(target), "--kind", "screener",
                 "--format", "csv", "--out", str(tmp_path / "nothing.csv"))
    assert result.returncode != 0
    assert "Nothing to export" in result.stdout


def test_import_workbook_dry_run(tmp_path: Path, workbook_path: Path) -> None:
    target = tmp_path / "cli.db"
    run("init_db.py", "--db", str(target))
    result = run("import_workbook.py", "--db", str(target),
                 "--workbook", str(workbook_path), "--sheet", "Data", "--dry-run")
    assert result.returncode == 0, result.stderr

    from database.db import Database

    assert Database(target).summary_counts()["records"] == 0      # dry run writes nothing


def test_parity_check_passes(tmp_path: Path) -> None:
    result = run("parity_check.py", "--db", str(tmp_path / "none.db"),
                 "--no-db", "--no-report")
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_main_check_self_test(tmp_path: Path, workbook_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "main.py"), "--check",
         "--db", str(tmp_path / "self.db"), "--workbook", str(workbook_path)],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "result   : OK" in result.stdout
    assert "import   :" in result.stdout


def test_main_version() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "main.py"), "--version"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert "Capital Analytics" in result.stdout


def test_default_workbook_resolves_in_a_source_checkout(workbook_path: Path) -> None:
    import main

    found = main.default_workbook()
    assert found.exists()
    assert found == workbook_path
    assert found.name == "Saif-Stock Market Data File-Sample.xlsm"
