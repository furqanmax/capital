"""Workbook import: mapping, validation, duplicate policies and atomicity."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import openpyxl
import pytest

from database.db import Database
from models import AuditLog, ImportBatch, ImportError_, Price, PriceExtra, Stock
from services.excel_import import (
    CANONICAL_TO_DB,
    DB_TO_CANONICAL,
    ImportService,
    auto_map_columns,
    file_digest,
    list_sheets,
    preview_sheet,
    read_records,
)
from services.validation import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    coerce_date,
    coerce_number,
    coerce_symbol,
    detect_duplicate_policy,
    validate_records,
)
from services.workbook_reader import DATA_COLUMNS, DATE_COLUMN, SYMBOL_COLUMN


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

HEADERS = ["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE",
           "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %"]

DEFAULT_VALUES = ["ACME", _dt.date(2025, 1, 1), 100, 110, 90, 105, 106, 100,
                  1000, 105000, 50, 40]

#: canonical column -> the header that carries it in ``HEADERS``
MAPPING = {column: column for column in DATA_COLUMNS}


def row(**overrides):
    """One source row.  Overrides are keyed by the *header* text."""
    values = dict(zip(HEADERS, DEFAULT_VALUES))
    values.update(overrides)
    return tuple(values[header] for header in HEADERS)


def records(*rows, headers=None):
    """Turn assembled rows into worksheet-style records (1-based row numbers)."""
    headers = headers or HEADERS
    out = []
    for index, values in enumerate(rows, start=2):
        record = {"__row__": index}
        record.update(dict(zip(headers, values)))
        out.append(record)
    return out


def make_workbook(path: Path, sheet: str = "Data", rows=None, headers=None) -> Path:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    headers = headers or HEADERS
    worksheet.append(headers)
    for values in (rows if rows is not None else [row(), row(**{"Symbol": "BETA"})]):
        worksheet.append(list(values))
    workbook.save(path)
    return path


def new_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "db.db")
    database.init_schema()
    return database


# --------------------------------------------------------------------------
# column mapping
# --------------------------------------------------------------------------


def test_auto_map_recognises_the_workbook_headers() -> None:
    mapping = auto_map_columns(HEADERS)
    assert mapping[SYMBOL_COLUMN] == "Symbol"
    assert mapping[DATE_COLUMN] == "Date"
    assert mapping["CLOSE"] == "CLOSE"
    assert mapping["Delivery %"] == "Delivery %"
    assert set(mapping) == set(DATA_COLUMNS)


def test_auto_map_handles_friendly_aliases() -> None:
    mapping = auto_map_columns(["Scrip", "Trading Date", "open price", "high",
                                "low", "close price", "ltp", "previous close",
                                "volume", "turnover", "trades", "delivery"])
    assert mapping[SYMBOL_COLUMN] == "Scrip"
    assert mapping[DATE_COLUMN] == "Trading Date"
    assert mapping["TOTTRDQTY"] == "volume"
    assert mapping["TOTTRDVAL"] == "turnover"
    assert mapping["TOTALTRADES"] == "trades"
    assert mapping["Delivery %"] == "delivery"
    assert mapping["LAST"] == "ltp"


def test_auto_map_never_reuses_one_header_for_two_columns() -> None:
    mapping = auto_map_columns(["Symbol", "Date", "Close", "Close"])
    assert len(set(mapping.values())) == len(mapping)


def test_auto_map_leaves_unknown_headers_out() -> None:
    mapping = auto_map_columns(HEADERS + ["Something New"])
    assert "Something New" not in mapping.values()


def test_auto_map_of_an_unrecognisable_sheet_is_empty() -> None:
    assert auto_map_columns(["alpha", "beta"]) == {}


def test_canonical_to_db_covers_every_numeric_column() -> None:
    for column in DATA_COLUMNS:
        if column in (SYMBOL_COLUMN, DATE_COLUMN):
            assert column not in CANONICAL_TO_DB
        else:
            assert column in CANONICAL_TO_DB, column
    assert len(set(CANONICAL_TO_DB.values())) == len(CANONICAL_TO_DB)
    assert DB_TO_CANONICAL["delivery_pct"] == "Delivery %"


# --------------------------------------------------------------------------
# value coercion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("acme", "ACME"), (" acme ", "ACME"), ("ACME LTD", "ACME LTD"), ("", None),
    (None, None), (123, "123"), ("A.B", "A.B"), ("infy", "INFY"),
])
def test_coerce_symbol(value, expected) -> None:
    assert coerce_symbol(value) == expected


@pytest.mark.parametrize("value,expected", [
    (105, 105.0), ("105.5", 105.5), (None, None), ("", None), ("n/a", None),
    ("--", None), (True, None), ("1,234.5", 1234.5), (0, 0.0),
])
def test_coerce_number(value, expected) -> None:
    assert coerce_number(value) == expected


@pytest.mark.parametrize("value,expected", [
    (_dt.date(2025, 1, 1), _dt.date(2025, 1, 1)),
    (_dt.datetime(2025, 1, 1, 9, 30), _dt.date(2025, 1, 1)),
    ("2025-01-01", _dt.date(2025, 1, 1)),
    ("01/01/2025", _dt.date(2025, 1, 1)),
    ("2025-01-01 09:30:00", _dt.date(2025, 1, 1)),
    (45658, _dt.date(2025, 1, 1)),          # Excel serial date
    (None, None),
    ("", None),
    ("not a date", None),
])
def test_coerce_date(value, expected) -> None:
    assert coerce_date(value) == expected


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_validate_accepts_a_clean_sheet() -> None:
    result = validate_records(HEADERS, records(row(), row(**{"Symbol": "BETA"})), MAPPING)
    assert result.ok
    assert result.error_count == 0
    assert len(result.rows) == 2
    assert result.rows[0]["symbol"] == "ACME"
    assert result.rows[0]["trade_date"] == _dt.date(2025, 1, 1)
    assert result.rows[0]["CLOSE"] == 105.0
    assert result.rows[0]["__row__"] == 2


def test_validate_flags_a_missing_symbol() -> None:
    result = validate_records(HEADERS, records(row(**{"Symbol": None})), MAPPING)
    assert not result.ok
    assert result.error_count == 1
    assert result.issues[0].row_number == 2
    assert result.issues[0].column == SYMBOL_COLUMN
    assert result.issues[0].severity == SEVERITY_ERROR
    assert result.rows == []                       # a bad row is never imported


def test_validate_flags_a_missing_date() -> None:
    result = validate_records(HEADERS, records(row(**{"Date": None})), MAPPING)
    assert not result.ok
    assert any(issue.column == DATE_COLUMN for issue in result.issues)
    assert result.rows == []


def test_validate_accepts_excel_serial_dates() -> None:
    result = validate_records(HEADERS, records(row(**{"Date": 45658})), MAPPING)
    assert result.ok
    assert result.rows[0]["trade_date"] == _dt.date(2025, 1, 1)


def test_validate_accepts_text_dates_and_nullable_numbers() -> None:
    result = validate_records(HEADERS, records(row(**{
        "Date": "2025-03-04", "TOTTRDQTY": None, "Delivery %": None})), MAPPING)
    assert result.ok
    assert result.rows[0]["trade_date"] == _dt.date(2025, 3, 4)
    assert result.rows[0]["TOTTRDQTY"] is None
    assert result.rows[0]["Delivery %"] is None


def test_validate_rejects_a_non_numeric_price() -> None:
    result = validate_records(HEADERS, records(row(**{"CLOSE": "not a number"})), MAPPING)
    assert not result.ok
    assert result.error_count == 1
    assert result.rows == []
    assert "non-numeric" in result.issues[0].message


def test_validate_warns_when_a_price_is_outside_the_high_low_band() -> None:
    result = validate_records(HEADERS, records(row(**{"CLOSE": 500, "LAST": 500})), MAPPING)
    assert result.ok                                   # a warning never blocks
    assert result.warning_count >= 1
    assert any(issue.severity == SEVERITY_WARNING for issue in result.issues)


def test_validate_warns_when_high_is_below_low() -> None:
    result = validate_records(HEADERS, records(row(**{"HIGH": 10, "LOW": 90})), MAPPING)
    assert result.ok
    assert any("HIGH" in issue.message for issue in result.issues)


def test_validate_keeps_unmapped_columns_as_extras() -> None:
    headers = HEADERS + ["Analyst Note"]
    result = validate_records(headers, records(row() + ("watch",), headers=headers), MAPPING)
    assert "Analyst Note" in result.extra_columns
    assert result.rows[0]["__extra__"]["Analyst Note"] == "watch"


def test_validate_ignores_blank_extras() -> None:
    headers = HEADERS + ["Analyst Note"]
    result = validate_records(headers, records(row() + ("",), headers=headers), MAPPING)
    assert "Analyst Note" not in result.rows[0]["__extra__"]


def test_validate_reports_duplicates_inside_the_file() -> None:
    result = validate_records(HEADERS, records(row(), row(**{"CLOSE": 999})), MAPPING)
    assert result.duplicate_keys_in_file == [("ACME", _dt.date(2025, 1, 1))]
    assert result.ok                                   # still a warning
    assert len(result.rows) == 2


def test_validate_warns_about_a_future_date() -> None:
    future = _dt.date.today() + _dt.timedelta(days=10)
    result = validate_records(HEADERS, records(row(**{"Date": future})), MAPPING)
    assert result.ok
    assert any("future" in issue.message for issue in result.issues)


def test_validate_only_warns_about_missing_delivery_when_asked() -> None:
    blank = row(**{"Delivery %": None})
    assert validate_records(HEADERS, records(blank), MAPPING).warning_count == 0
    strict = validate_records(HEADERS, records(blank), MAPPING, require_delivery=True)
    assert strict.warning_count == 1


def test_summary_counts_everything() -> None:
    result = validate_records(HEADERS, records(row(), row(**{"Symbol": None})), MAPPING)
    summary = result.summary()
    assert summary["rows_parsed"] == 1
    assert summary["errors"] >= 1
    assert summary["extra_columns"] == 0


@pytest.mark.parametrize("policy,expected", [
    ("skip", "skip"), ("SKIP", "skip"), (" update ", "update"),
    ("new_batch", "new_batch"), ("NEW_BATCH", "new_batch"),
])
def test_detect_duplicate_policy(policy, expected) -> None:
    assert detect_duplicate_policy(policy) == expected


def test_detect_duplicate_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        detect_duplicate_policy("explode")


# --------------------------------------------------------------------------
# workbook reading
# --------------------------------------------------------------------------


def test_list_sheets(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    assert list_sheets(path) == ["Data"]


def test_preview_returns_headers_and_rows(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    payload = preview_sheet(path, "Data", rows=5)
    assert payload["header_row"] == 1
    assert payload["headers"] == HEADERS
    assert len(payload["preview"]) == 2
    assert payload["preview"][0][0] == "ACME"
    assert payload["max_row"] == 3


def test_preview_skips_leading_blank_rows(tmp_path: Path) -> None:
    path = tmp_path / "gap.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append(["Quarterly report"])
    worksheet.append([None] * len(HEADERS))
    worksheet.append(HEADERS)
    worksheet.append(list(row()))
    workbook.save(path)

    payload = preview_sheet(path, "Data", rows=5)
    assert payload["header_row"] == 3
    assert payload["headers"] == HEADERS
    assert payload["preview"][0][0] == "ACME"


def test_preview_of_a_missing_sheet_raises(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    with pytest.raises(ValueError):
        preview_sheet(path, "Nope")


def test_read_records_returns_raw_rows(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    payload = read_records(path, "Data", 1)
    assert payload["headers"] == HEADERS
    assert payload["issues"] == []
    assert len(payload["records"]) == 2
    assert payload["records"][0]["__row__"] == 2
    assert payload["records"][0]["Symbol"] == "ACME"


def test_file_digest_is_a_stable_sha256(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    assert file_digest(path) == file_digest(path)
    assert len(file_digest(path)) == 64


# --------------------------------------------------------------------------
# importing
# --------------------------------------------------------------------------


def test_import_inserts_rows_and_creates_a_batch(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)

    report = ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")

    assert report.status == "completed"
    assert report.rows_read == 2
    assert report.rows_inserted == 2
    assert report.rows_rejected == 0
    assert report.batch_id is not None
    assert report.duration_seconds >= 0

    counts = database.summary_counts()
    assert counts["stocks"] == 2
    assert counts["records"] == 2
    assert counts["earliest_date"] == counts["latest_date"] == _dt.date(2025, 1, 1)

    with database.session() as session:
        batch = session.get(ImportBatch, report.batch_id)
        assert batch.status == "completed"
        assert batch.duplicate_policy == "skip"
        assert batch.rows_inserted == 2
        assert batch.finished_at is not None
        assert batch.column_mapping == MAPPING


def test_import_is_idempotent_with_the_skip_policy(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)

    first = ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")
    second = ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")

    assert first.rows_inserted == 2
    assert second.rows_inserted == 0
    assert second.rows_skipped == 2
    assert database.summary_counts()["records"] == 2


def test_update_policy_overwrites_in_place(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")

    changed = make_workbook(tmp_path / "changed.xlsx",
                            rows=[row(**{"CLOSE": 999, "LAST": 1000}),
                                  row(**{"Symbol": "GAMMA"})])
    report = ImportService(database).run(changed, "Data", MAPPING, duplicate_policy="update")

    assert report.rows_updated == 1
    assert report.rows_inserted == 1
    assert report.rows_skipped == 0
    assert database.summary_counts()["records"] == 3

    with database.session() as session:
        price = session.query(Price).filter(Price.close == 999.0).one()
        assert price.last == 1000.0


def test_new_batch_policy_archives_the_previous_values(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")

    changed = make_workbook(tmp_path / "changed.xlsx",
                            rows=[row(**{"CLOSE": 999}), row(**{"Symbol": "GAMMA"})])
    ImportService(database).run(changed, "Data", MAPPING, duplicate_policy="new_batch")

    with database.session() as session:
        archived = session.query(PriceExtra).all()
    assert archived, "the superseded values should have been archived"
    assert any("superseded" in item.column_name for item in archived)
    assert any(item.value_text == "105.0" for item in archived)


def test_import_skips_rows_that_fail_validation(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx", rows=[row(**{"Symbol": None})])
    database = new_database(tmp_path)

    report = ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")

    assert report.rows_read == 1
    assert report.rows_rejected == 1
    assert report.rows_inserted == 0
    assert report.status == "completed_with_errors"
    assert database.summary_counts()["records"] == 0


def test_import_persists_rejected_rows_as_errors(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx",
                         rows=[row(), row(**{"Symbol": None}),
                               row(**{"Symbol": "GAMMA", "CLOSE": "bad"})])
    database = new_database(tmp_path)

    report = ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")
    assert report.rows_inserted == 1
    assert report.rows_rejected == 2          # two bad rows, one inserted
    assert report.error_count == 2
    assert report.status == "completed_with_errors"

    with database.session() as session:
        errors = session.query(ImportError_).all()
    assert errors
    assert any(item.row_number == 3 for item in errors)   # missing symbol
    assert any(item.row_number == 4 for item in errors)   # non-numeric close


def test_import_stores_unmapped_columns(tmp_path: Path) -> None:
    headers = HEADERS + ["Analyst Note"]
    path = make_workbook(tmp_path / "book.xlsx", headers=headers,
                         rows=[row() + ("watch closely",), row(**{"Symbol": "BETA"}) + ("",)])
    database = new_database(tmp_path)
    mapping = auto_map_columns(headers)

    report = ImportService(database).run(path, "Data", mapping, duplicate_policy="skip")
    assert "Analyst Note" in report.extra_columns

    with database.session() as session:
        extras = session.query(PriceExtra).filter(
            PriceExtra.column_name == "Analyst Note").all()
    assert len(extras) == 1
    assert extras[0].value_text == "watch closely"
    assert extras[0].symbol == "ACME"


def test_import_progress_callback_is_called(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    seen = []

    ImportService(database).run(
        path, "Data", MAPPING, duplicate_policy="skip",
        progress=lambda current, total, message="": seen.append((current, total, message)))

    assert seen
    assert seen[-1][0] == seen[-1][1] == 2
    assert seen[-1][2] == "done"


def test_import_without_symbol_or_date_columns_rejects_every_row(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)

    report = ImportService(database).run(path, "Data", {"CLOSE": "CLOSE"},
                                         duplicate_policy="skip")
    assert report.rows_inserted == 0
    assert report.rows_rejected == 2          # both rows, not both issues
    assert report.error_count == 4            # symbol + date missing on each row
    assert database.summary_counts()["records"] == 0


def test_import_of_a_missing_file_records_nothing(tmp_path: Path) -> None:
    database = new_database(tmp_path)
    with pytest.raises(FileNotFoundError):
        ImportService(database).run(tmp_path / "nope.xlsx", "Data", MAPPING,
                                    duplicate_policy="skip")
    assert database.summary_counts()["records"] == 0
    with database.session() as session:
        assert session.query(ImportBatch).count() == 0


def test_import_rejects_an_unknown_duplicate_policy(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    with pytest.raises(ValueError):
        ImportService(database).run(path, "Data", MAPPING, duplicate_policy="explode")


def test_import_records_the_source_metadata(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")
    with database.session() as session:
        batch = session.query(ImportBatch).one()
    assert batch.source_hash == file_digest(path)
    assert batch.sheet_name == "Data"
    assert batch.rows_read == 2


def test_import_updates_the_stock_dimension(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")
    with database.session() as session:
        stock = session.query(Stock).filter(Stock.symbol == "ACME").one()
    assert stock.record_count == 1
    assert stock.first_date == _dt.date(2025, 1, 1)
    assert stock.last_date == _dt.date(2025, 1, 1)


def test_import_writes_an_audit_entry(tmp_path: Path) -> None:
    path = make_workbook(tmp_path / "book.xlsx")
    database = new_database(tmp_path)
    ImportService(database).run(path, "Data", MAPPING, duplicate_policy="skip")
    with database.session() as session:
        entries = session.query(AuditLog).filter(AuditLog.category == "import").all()
    assert entries
    assert "Imported 2 new" in entries[0].message


# --------------------------------------------------------------------------
# the bundled workbook
# --------------------------------------------------------------------------


def test_bundled_workbook_imports_completely(filled_db: Database) -> None:
    counts = filled_db.summary_counts()
    assert counts["stocks"] == 191
    assert counts["records"] == 191
    assert counts["earliest_date"] == counts["latest_date"] == _dt.date(2025, 1, 1)


def test_bundled_workbook_mapping_covers_every_column(workbook_path: Path) -> None:
    payload = preview_sheet(workbook_path, "Data", rows=1)
    mapping = auto_map_columns(payload["headers"])
    assert set(mapping) == set(DATA_COLUMNS)


def test_bundled_workbook_reimport_skips_everything(filled_db: Database,
                                                    workbook_path: Path) -> None:
    payload = preview_sheet(workbook_path, "Data", rows=1)
    mapping = auto_map_columns(payload["headers"])
    report = ImportService(filled_db).run(workbook_path, "Data", mapping,
                                           duplicate_policy="skip")
    assert report.rows_inserted == 0
    assert report.rows_skipped == 191
