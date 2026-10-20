"""Streaming worksheet reads and streaming validation.

The importer no longer builds a Python object per cell, and it can validate a
sheet without keeping the rows.  These tests pin the behaviour that made the
rewrite safe in the first place: the same headers, the same rows, the same
issues — and a read that stays linear in the number of rows.
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

import openpyxl
import pytest
from openpyxl import Workbook

from services.workbook_reader import (
    DataStream,
    is_blank_row,
    workbook_engine,
)
from services.validation import validate_records, validate_stream

HEADERS = ["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
           "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %"]
MAPPING = {name: name for name in HEADERS}


def make_workbook(path: Path, rows: int = 5) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(HEADERS)
    base = _dt.date(2025, 1, 1)
    for index in range(rows):
        sheet.append([
            f"SYM{index:03d}", base + _dt.timedelta(days=index),
            100.0, 110.0, 90.0, 105.0, 106.0, 100.0,
            1_000.0, 105_000.0, 50.0, 40.0,
        ])
    workbook.save(path)
    return path


def make_messy_workbook(path: Path) -> Path:
    """Preamble rows, a blank header cell, a blank row and a short row."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Monthly market report"])
    sheet.append(["generated", "by", "a", "human"])
    sheet.append([*HEADERS[:4], None, *HEADERS[5:]])     # blank 5th header
    sheet.append(["AAA", _dt.date(2025, 1, 1), 1.0, 2.0, 0.5, 1.5, 1.6, 1.4,
                  10.0, 15.0, 3.0, 40.0])
    sheet.append([None] * len(HEADERS))                  # entirely blank
    sheet.append(["BBB", _dt.date(2025, 1, 2), 3.0])     # short row
    sheet.append(["CCC", _dt.date(2025, 1, 3), 4.0, 5.0, 3.5, 4.5, 4.6, 4.4,
                  20.0, 90.0, 6.0, 55.0, "stray"])       # extra trailing cell
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_stream_reads_headers_and_rows(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=5)
    with DataStream(path, sheet="Data") as stream:
        assert stream.header_row == 1
        assert stream.headers == HEADERS
        rows = list(stream.records(stream.headers))

    assert [row["Symbol"] for row in rows] == [f"SYM{i:03d}" for i in range(5)]
    assert [row["__row__"] for row in rows] == [2, 3, 4, 5, 6]


def test_stream_skips_preamble_and_finds_the_header(tmp_path: Path) -> None:
    path = make_messy_workbook(tmp_path / "messy.xlsx")
    with DataStream(path, sheet="Data") as stream:
        assert stream.header_row == 3
        # A blank header cell becomes ColumnN, exactly like the old inspector.
        assert stream.headers[4] == "Column5"
        records = list(stream.records(stream.headers))

    assert [row["Symbol"] for row in records] == ["AAA", "BBB", "CCC"]
    assert [row["__row__"] for row in records] == [4, 6, 7]
    # Short rows are padded with None to the header width.
    assert records[1]["CLOSE"] is None
    # A cell beyond the header row widens the sheet, exactly as before: the
    # extra column is named ColumnN and its value is kept rather than dropped.
    assert stream.headers[12] == "Column13"
    assert records[2]["Column13"] == "stray"


def test_blank_rows_are_recognised() -> None:
    assert is_blank_row([None, "", "   "])
    assert not is_blank_row([None, 0])
    assert not is_blank_row(["x"])


def test_missing_sheet_is_a_value_error(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    with pytest.raises(ValueError):
        with DataStream(path, sheet="Nope"):
            pass


def test_missing_file_is_a_file_not_found_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        with DataStream(tmp_path / "absent.xlsx", sheet="Data"):
            pass


def test_sheet_is_picked_when_none_is_given(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    with DataStream(path) as stream:
        assert stream.sheet == "Data"


@pytest.mark.skipif(workbook_engine() != "calamine",
                    reason="needs the optional python-calamine reader")
def test_both_readers_return_identical_values(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The Rust reader is an optimisation, never a behaviour change."""
    paths = [make_workbook(tmp_path / "plain.xlsx", rows=7),
             make_messy_workbook(tmp_path / "messy.xlsx")]

    outputs = {}
    for engine in ("openpyxl", "calamine"):
        monkeypatch.setenv("CAPITAL_WORKBOOK_ENGINE", engine)
        per_path = {}
        for path in paths:
            with DataStream(path, sheet="Data") as stream:
                # The readers differ in *Python types* (int vs float, date vs
                # datetime); what must be identical is what the validator sees.
                result = validate_stream(
                    stream.headers, stream.records(stream.headers), MAPPING,
                    keep_rows=True)
                per_path[path.name] = (
                    stream.header_row, list(stream.headers),
                    result.rows,
                    [i.as_dict() for i in result.issues],
                )
        outputs[engine] = per_path

    assert outputs["openpyxl"] == outputs["calamine"]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validate_stream_matches_validate_records() -> None:
    records = [
        {"Symbol": "AAA", "Date": "2025-01-01", "OPEN": 10, "HIGH": 12,
         "LOW": 9, "CLOSE": 11, "TOTTRDQTY": 100, "__row__": 2},
        {"Symbol": "", "Date": "2025-01-02", "CLOSE": 11, "__row__": 3},
        {"Symbol": "BBB", "Date": "not a date", "CLOSE": 1, "__row__": 4},
        {"Symbol": "CCC", "Date": "2025-01-03", "OPEN": 5, "HIGH": 4,
         "LOW": 6, "CLOSE": 5, "__row__": 5},
    ]

    materialised = validate_records(HEADERS, records, MAPPING)
    streamed = validate_stream(HEADERS, iter(records), MAPPING, keep_rows=True)

    assert streamed.rows == materialised.rows
    assert [(i.row_number, i.severity, i.column, i.message)
            for i in streamed.issues] == \
           [(i.row_number, i.severity, i.column, i.message)
            for i in materialised.issues]
    assert streamed.error_count == materialised.error_count
    assert streamed.warning_count == materialised.warning_count
    assert streamed.rejected_rows == materialised.rejected_rows


def test_issue_sampling_keeps_exact_counters() -> None:
    records = [
        {"Symbol": f"S{i}", "Date": "nonsense", "__row__": i + 2}
        for i in range(500)
    ]
    result = validate_stream(HEADERS, iter(records), MAPPING, max_issues=10)

    assert result.issues_truncated is True
    assert len(result.issues) == 10
    assert result.records_seen == 500
    assert result.error_count == 500          # counter, not len(issues)
    assert result.rejected_rows == 500
    assert result.rows == []                  # keep_rows defaults to False


def test_streaming_validation_does_not_keep_rows() -> None:
    records = [{"Symbol": f"S{i}", "Date": "2025-01-01", "CLOSE": 1.0,
                "__row__": i + 2} for i in range(100)]
    seen = []
    result = validate_stream(HEADERS, iter(records), MAPPING,
                             keep_rows=False, on_row=seen.append)

    assert result.rows == []
    assert len(seen) == 100
    assert result.records_seen == 100


# ---------------------------------------------------------------------------
# performance smoke test
# ---------------------------------------------------------------------------


def test_reading_stays_linear_in_row_count(tmp_path: Path) -> None:
    """20 000 rows used to take minutes because of a per-row sheet scan."""
    path = make_workbook(tmp_path / "big.xlsx", rows=20_000)

    started = time.monotonic()
    with DataStream(path, sheet="Data") as stream:
        count = sum(1 for _ in stream.records(stream.headers))
    elapsed = time.monotonic() - started

    assert count == 20_000
    assert elapsed < 25.0, f"reading 20k rows took {elapsed:.1f}s"
