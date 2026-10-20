"""Bulk import mode: identical results to strict mode, plus resumability."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from database.db import Database
from models import ImportBatch
from services import excel_import
from services.excel_import import ImportService

HEADERS = [
    "Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
    "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %",
]
MAPPING = {name: name for name in HEADERS}


def make_workbook(path: Path, rows: int = 30) -> Path:
    """A workbook whose (symbol, date) pairs are all unique."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(HEADERS)
    base = _dt.date(2025, 1, 1)
    for index in range(rows):
        sheet.append([
            f"SYM{index % 5:03d}",
            base + _dt.timedelta(days=index // 5),
            100.0 + index, 110.0 + index, 90.0 + index, 105.0 + index,
            106.0 + index, 100.0 + index, 1_000.0 + index, 105_000.0 + index,
            50.0 + index, 40.0 + index,
        ])
    workbook.save(path)
    return path


def new_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bulk.db")
    database.init_schema()
    return database


def test_bulk_and_strict_imports_produce_identical_rows(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")

    strict_db = new_database(tmp_path / "strict")
    bulk_db = new_database(tmp_path / "bulk")
    strict = ImportService(strict_db).run(path, "Data", MAPPING, mode="strict")
    bulk = ImportService(bulk_db).run(path, "Data", MAPPING, mode="bulk", chunk_size=7)

    for field in ("rows_read", "rows_inserted", "rows_updated", "rows_skipped",
                  "rows_rejected", "error_count", "warning_count"):
        assert getattr(bulk, field) == getattr(strict, field), field
    assert bulk.status == strict.status == "completed"

    strict_frame = strict_db.price_frame()
    bulk_frame = bulk_db.price_frame()
    assert len(strict_frame) == len(bulk_frame) == 30
    # Compare the numbers, not the pandas index (row ids differ per database).
    columns = [c for c in bulk_frame.columns if c != "symbol"]
    assert bulk_frame.drop(columns="symbol").equals(
        strict_frame.drop(columns="symbol"))
    assert list(bulk_frame["symbol"]) == list(strict_frame["symbol"])
    assert columns  # sanity: the comparison covers every non-symbol column


def test_bulk_mode_is_idempotent_with_the_skip_policy(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    first = ImportService(database).run(path, "Data", MAPPING, mode="bulk", chunk_size=8)
    second = ImportService(database).run(path, "Data", MAPPING, mode="bulk", chunk_size=8)

    assert first.rows_inserted == 30
    assert second.rows_inserted == 0
    assert second.rows_skipped == 30
    assert database.summary_counts()["records"] == 30


def test_bulk_mode_resumes_an_interrupted_import(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=30)
    database = new_database(tmp_path)

    original = excel_import._BulkWriter._write_chunk
    calls = {"n": 0}

    def flaky(writer):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")
        return original(writer)

    monkeypatch.setattr(excel_import._BulkWriter, "_write_chunk", flaky)
    with pytest.raises(RuntimeError):
        ImportService(database).run(path, "Data", MAPPING, mode="bulk", chunk_size=5)
    monkeypatch.undo()

    # Two chunks of five rows were committed before the crash.
    assert database.summary_counts()["records"] == 10
    with database.session() as session:
        interrupted = session.scalars(
            select(ImportBatch).order_by(ImportBatch.id.desc())).first()
        interrupted_id, interrupted_status = interrupted.id, interrupted.status

    assert interrupted_status == "running"

    resumed = ImportService(database).run(path, "Data", MAPPING, mode="bulk",
                                          chunk_size=5, resume=True)

    assert resumed.batch_id == interrupted_id        # the same batch was adopted
    assert resumed.status == "completed"
    assert database.summary_counts()["records"] == 30
    assert resumed.rows_read == 30                   # 10 replayed + 20 new
    assert resumed.rows_inserted == 20


def test_resume_does_not_adopt_a_completed_batch(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=10)
    database = new_database(tmp_path)
    first = ImportService(database).run(path, "Data", MAPPING, mode="bulk", chunk_size=4)
    second = ImportService(database).run(path, "Data", MAPPING, mode="bulk",
                                         chunk_size=4, resume=True)

    assert second.batch_id != first.batch_id
    assert database.summary_counts()["records"] == 10


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=3)
    database = new_database(tmp_path)
    with pytest.raises(ValueError):
        ImportService(database).run(path, "Data", MAPPING, mode="turbo")


def test_bulk_mode_keeps_validation_counts(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=6)
    database = new_database(tmp_path)
    report = ImportService(database).run(path, "Data", MAPPING, mode="bulk", chunk_size=2)

    assert report.rows_read == 6
    assert report.rows_inserted == 6
    assert report.error_count == 0
    assert report.issues == []
