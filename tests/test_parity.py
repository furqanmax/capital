"""Workbook parity: the application must reproduce Excel's own results.

These tests are marked ``slow`` — each one recomputes the whole Analysis block
and/or the whole Main screener and compares several thousand cells against the
results Excel cached inside the supplied workbook.

Run them explicitly with::

    pytest -m slow
"""

from __future__ import annotations

import pytest

from services.parity import (
    DEFAULT_TOLERANCE,
    Mismatch,
    ParityReport,
    compare_analysis,
    compare_screener,
    run_all,
)

pytestmark = pytest.mark.slow


def _first_mismatches(report: ParityReport, count: int = 10) -> str:
    return report.mismatch_frame().head(count).to_string()


# --------------------------------------------------------------------------
# the report container itself (fast)
# --------------------------------------------------------------------------


def test_parity_report_container() -> None:
    report = ParityReport("analysis", "original", DEFAULT_TOLERANCE,
                          compared=10, matched=9, unavailable=2)
    report.mismatches.append(Mismatch("M15", 1.0, 2.0, delta=1.0))

    assert report.failed == 1
    assert report.passed is False
    assert report.match_rate == pytest.approx(0.9)
    summary = report.summary()
    assert summary["scope"] == "analysis"
    assert summary["matched"] == 9
    assert summary["failed"] == 1
    assert list(report.mismatch_frame().columns) == \
        ["cell", "expected", "actual", "delta", "kind"]


def test_empty_parity_report_passes() -> None:
    report = ParityReport("main", "original", DEFAULT_TOLERANCE)
    assert report.passed
    assert report.match_rate == 1.0
    assert report.mismatch_frame().empty


# --------------------------------------------------------------------------
# the real workbook
# --------------------------------------------------------------------------


def test_analysis_block_matches_excel(workbook_path) -> None:
    report = compare_analysis(workbook_path)
    assert report.compared >= 2_000, report.summary()
    assert report.passed, _first_mismatches(report)


def test_screener_matches_excel(workbook_path, filled_data) -> None:
    report = compare_screener(workbook_path, filled_data.frame())
    assert report.compared >= 7_000, report.summary()
    assert report.passed, _first_mismatches(report)


def test_run_all_covers_both_oracles(workbook_path, filled_data) -> None:
    reports = run_all(workbook_path, filled_data.frame())
    assert {report.name for report in reports} == {"analysis", "main"}
    assert all(report.passed for report in reports), \
        {report.name: _first_mismatches(report) for report in reports if not report.passed}
    total = sum(report.compared for report in reports)
    assert total >= 9_000, total


def test_corrected_mode_diverges_from_the_defective_workbook(workbook_path) -> None:
    """``corrected`` mode *fixes* the sheet, so it must stop matching it.

    The forward-looking volume window (finding B3) alone guarantees a
    difference; if this ever passes, the workbook no longer contains the
    defects the corrected mode is designed to repair.
    """
    report = compare_analysis(workbook_path, mode="corrected")
    assert report.failed > 0
