"""Excel import engine.

Workflow implemented here (the UI wraps it):

1. choose file  → :func:`list_sheets`
2. choose sheet → :func:`preview_sheet`
3. auto/map columns → :func:`auto_map_columns`
4. validate → :func:`services.validation.validate_stream`
5. import → :meth:`ImportService.run`

Duplicate handling policies
---------------------------

``skip``       existing (symbol, date) rows are left untouched,
``update``     existing rows are overwritten with the incoming values,
``new_batch``  identical to ``update`` but the *previous* values are archived
               into ``price_extras`` so the superseded snapshot is preserved.

Import modes
------------

``strict`` (default)
    The whole import runs in one transaction: a failure leaves the database
    exactly as it was.  Rows are read, validated and written in a single
    streaming pass, so memory stays flat no matter how many rows the file has.
``bulk``
    Opt-in fast path for very large files: the batch is committed up front and
    rows are committed in chunks (``chunk_size``, default 100 000).  A failure
    leaves the chunks that already committed in place, the batch status records
    that, and a re-run with ``resume=True`` continues from the last committed
    source row.  Bulk mode also relaxes ``synchronous`` for the load.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple,
)

from database.db import Database

from .validation import (
    SEVERITY_ERROR,
    ValidationResult,
    validate_stream,
)
from .workbook_reader import (
    DATA_COLUMNS,
    DataStream,
    WorkbookInspector,
    is_blank_row,
    match_canonical_column,
    sheet_names,
)

ProgressCallback = Callable[[int, int, str], None]

#: Rows buffered before an ``executemany`` write (strict mode) or a commit (bulk).
DEFAULT_CHUNK_SIZE = 100_000

#: How many individual issues a validation pass keeps in memory.  Counters stay
#: exact; only the retained *samples* are capped (a million-row file can produce
#: a million warnings, which must not be held in a list).
DEFAULT_ISSUE_LIMIT = 100_000

IMPORT_MODES = ("strict", "bulk")


CANONICAL_TO_DB: Dict[str, str] = {
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "LAST": "last",
    "PREVCLOSE": "prevclose",
    "TOTTRDQTY": "tottrdqty",
    "TOTTRDVAL": "tottrdval",
    "TOTALTRADES": "totaltrades",
    "Delivery %": "delivery_pct",
}

DB_TO_CANONICAL = {v: k for k, v in CANONICAL_TO_DB.items()}


# --------------------------------------------------------------------------
# Result containers
# --------------------------------------------------------------------------


@dataclass
class ImportReport:
    batch_id: Optional[int] = None
    source_file: str = ""
    sheet_name: str = ""
    duplicate_policy: str = "skip"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_rejected: int = 0
    error_count: int = 0
    warning_count: int = 0
    extra_columns: List[str] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    column_mapping: Dict[str, str] = field(default_factory=dict)
    started_at: _dt.datetime = field(default_factory=_dt.datetime.now)
    finished_at: Optional[_dt.datetime] = None
    status: str = "pending"

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or _dt.datetime.now()
        return (end - self.started_at).total_seconds()

    def summary(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "rows_read": self.rows_read,
            "inserted": self.rows_inserted,
            "updated": self.rows_updated,
            "skipped": self.rows_skipped,
            "rejected": self.rows_rejected,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "extra_columns": len(self.extra_columns),
            "duration_seconds": round(self.duration_seconds, 2),
        }


# --------------------------------------------------------------------------
# Reading / previewing
# --------------------------------------------------------------------------


def list_sheets(path: str | Path) -> List[str]:
    """Sheet names in workbook order (macros are never executed)."""
    return sheet_names(path)


def preview_sheet(path: str | Path, sheet: str, rows: int = 25) -> Dict[str, Any]:
    """Headers + a small preview used by the import dialog.

    The worksheet is streamed: only the header row plus up to ``rows`` data rows
    are ever read, so the dialog opens instantly even for a million-row file.
    ``max_row`` is the worksheet's declared extent and is ``None`` when the
    workbook omits it (``openpyxl``'s write-only mode does).
    """
    preview: List[List[Any]] = []
    blank_streak = 0
    with DataStream(path, sheet=sheet) as stream:
        headers = list(stream.headers)
        header_row = stream.header_row or 1
        max_column = stream.max_column or stream.width
        max_row = stream.max_row
        for _row_number, values in stream.rows():
            if is_blank_row(values):
                blank_streak += 1
                if blank_streak > 50 and preview:
                    break
                continue
            blank_streak = 0
            preview.append([_jsonable(value) for value in values])
            if len(preview) >= rows:
                break
    return {
        "sheet": sheet,
        "header_row": header_row,
        "headers": headers,
        "preview": preview,
        "max_row": max_row,
        "max_column": max_column,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def auto_map_columns(headers: Sequence[str]) -> Dict[str, str]:
    """Best-effort canonical mapping: canonical column -> source header."""
    mapping: Dict[str, str] = {}
    used: set[str] = set()
    for canonical in DATA_COLUMNS:
        # exact / alias match first
        for header in headers:
            if header in used:
                continue
            if match_canonical_column(header) == canonical:
                mapping[canonical] = header
                used.add(header)
                break
    return mapping


def read_records(path: str | Path, sheet: str, header_row: Optional[int] = None) -> Dict[str, Any]:
    """Read every populated row of a worksheet with its original headers.

    Materialises the whole sheet; for very large files use :func:`iter_records`
    (or :class:`~services.workbook_reader.DataStream`) instead.
    """
    inspector = WorkbookInspector(path)
    headers, records, issues = inspector.read_data_records(sheet=sheet, header_row=header_row)
    return {"headers": headers, "records": records, "issues": issues}


def iter_records(path: str | Path, sheet: str,
                 header_row: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """Stream raw worksheet rows one at a time (constant memory).

    The workbook is opened for the duration of the iteration and closed when the
    generator is exhausted or closed.
    """
    with DataStream(path, sheet=sheet, header_row=header_row) as stream:
        headers = list(stream.headers)
        for record in stream.records(headers):
            yield record


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Import service
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Import service
# --------------------------------------------------------------------------

#: Canonical -> ``prices`` column, in tuple order.
_MARKET_COLUMNS: Tuple[Tuple[str, str], ...] = tuple(CANONICAL_TO_DB.items())
_CANONICAL_KEYS = tuple(canonical for canonical, _column in _MARKET_COLUMNS)
_DB_COLUMNS = tuple(column for _canonical, column in _MARKET_COLUMNS)

_PRICE_COLUMNS = ("stock_id", "trade_date", *_DB_COLUMNS,
                  "batch_id", "source_row", "created_at", "updated_at")
_PRICE_COLUMN_SQL = ", ".join(f'"{name}"' for name in _PRICE_COLUMNS)
_PRICE_PLACEHOLDERS = ", ".join("?" for _ in _PRICE_COLUMNS)

_PRICE_INSERT_OR_IGNORE = (
    f"INSERT OR IGNORE INTO prices ({_PRICE_COLUMN_SQL}) VALUES ({_PRICE_PLACEHOLDERS})"
)
#: Upsert: ``created_at`` is deliberately left alone — the row keeps its origin.
_PRICE_INSERT_UPSERT = (
    f"INSERT INTO prices ({_PRICE_COLUMN_SQL}) VALUES ({_PRICE_PLACEHOLDERS}) "
    "ON CONFLICT(stock_id, trade_date) DO UPDATE SET "
    + ", ".join(f'"{name}" = excluded."{name}"'
                for name in (*_DB_COLUMNS, "batch_id", "source_row", "updated_at"))
)
_EXTRAS_INSERT = ("INSERT INTO price_extras "
                  "(symbol, trade_date, column_name, value_text, batch_id) "
                  "VALUES (?, ?, ?, ?, ?)")
_ERRORS_INSERT = ("INSERT INTO import_errors "
                  "(batch_id, row_number, column_name, severity, message, raw_value) "
                  "VALUES (?, ?, ?, ?, ?, ?)")
_BATCH_INSERT = (
    "INSERT INTO import_batches "
    "(source_file, source_hash, sheet_name, duplicate_policy, status, started_at, rows_read, "
    " rows_inserted, rows_updated, rows_skipped, rows_rejected, error_count, column_mapping, "
    " created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?)"
)
_BATCH_ALLOCATE = ("SELECT id, notes FROM import_batches "
                   "WHERE source_hash = ? AND sheet_name = ? AND duplicate_policy = ? "
                   "AND status IN ('running', 'interrupted') ORDER BY id DESC LIMIT 1")
_TEMP_KEYS = ("CREATE TEMP TABLE IF NOT EXISTS _import_keys "
              "(stock_id INTEGER NOT NULL, trade_date DATE NOT NULL, "
              " PRIMARY KEY (stock_id, trade_date))")

#: SQLite caps a statement at ~32 000 bound variables; 900 ids is comfortably safe.
_ID_CHUNK = 900


def _utcnow() -> _dt.datetime:
    """Naive UTC, matching the ``TimestampMixin`` defaults in :mod:`models`."""
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class _BulkWriter:
    """Streams validated rows into SQLite without a round trip per row.

    One connection is held for the whole import so connection-scoped ``TEMP``
    tables and PRAGMAs survive across chunks.  Rows are buffered and handed to
    ``executemany``; the duplicate policy is decided by SQLite
    (``INSERT OR IGNORE`` / ``ON CONFLICT DO UPDATE``) instead of a ``SELECT``
    per row, and a ``TEMP`` key table gives the exact inserted/updated/skipped
    counts without loading existing keys into Python.
    """

    def __init__(self, database: Database, policy: str, mode: str = "strict",
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 progress: Optional[ProgressCallback] = None,
                 total_hint: int = 0):
        self.db = database
        self.policy = policy
        self.mode = mode
        self.chunk_size = max(1, int(chunk_size))
        self.progress = progress
        self.total_hint = max(0, int(total_hint))

        self.connection: Any = None
        self.batch_id: Optional[int] = None
        self.resume_after_row = 0
        self.stock_ids: Dict[str, int] = {}
        self.touched: set[int] = set()

        self.inserted = 0
        self.updated = 0
        self.skipped = 0
        self.accepted = 0

        self._prices: List[tuple] = []
        self._keys: List[tuple] = []
        self._chunk_seen: set[tuple] = set()
        self._extras: List[tuple] = []
        self._last_source_row = 0
        self._stocks_loaded = False

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> "_BulkWriter":
        self.connection = self.db.engine.connect()
        if self.mode == "bulk":
            # Durability is already guaranteed by the batch/chunk commits; the
            # per-row fsync is what makes a naive import crawl.
            self.connection.exec_driver_sql("PRAGMA synchronous=OFF")
            self.connection.exec_driver_sql("PRAGMA temp_store=MEMORY")
            self.connection.commit()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None

    # -- batch bookkeeping ----------------------------------------------

    def prepare(self, source_file: str, source_hash: str, sheet: str,
                mapping: Mapping[str, str], resume: bool = False) -> int:
        """Create (or adopt) the ``import_batches`` row and return its id."""
        if resume and self.mode == "bulk":
            found = self._find_interrupted_batch(source_hash, sheet)
            if found is not None:
                self.batch_id, self.resume_after_row = found
                return self.batch_id
        now = _utcnow()
        self.connection.exec_driver_sql(
            _BATCH_INSERT,
            (source_file, source_hash, sheet, self.policy, "running", now, 0,
             json.dumps(dict(mapping)), now, now))
        self.batch_id = int(self.connection.exec_driver_sql(
            "SELECT last_insert_rowid()").scalar())
        if self.mode == "bulk":
            self.connection.commit()
        return self.batch_id

    def _find_interrupted_batch(self, source_hash: str, sheet: str):
        row = self.connection.exec_driver_sql(
            _BATCH_ALLOCATE, (source_hash, sheet, self.policy)).fetchone()
        if row is None:
            return None
        watermark = 0
        if row[1]:
            try:
                watermark = int(json.loads(row[1]).get("resume_after_row") or 0)
            except (ValueError, TypeError, AttributeError):
                watermark = 0
        return int(row[0]), watermark

    # -- row intake ------------------------------------------------------

    def add(self, row: Mapping[str, Any]) -> None:
        """Accept one validated row (called by :func:`validate_stream`)."""
        symbol = row["symbol"]
        trade_date = row["trade_date"]
        stock_id = self._stock_id(symbol)
        self.touched.add(stock_id)

        iso_date = trade_date.isoformat() if isinstance(trade_date, _dt.date) else str(trade_date)
        key = (stock_id, iso_date)
        first_in_chunk = key not in self._chunk_seen
        self._chunk_seen.add(key)
        self._keys.append(key)

        values = tuple(row.get(canonical) for canonical in _CANONICAL_KEYS)
        source_row = row.get("__row__")
        if isinstance(source_row, int):
            self._last_source_row = source_row
        now = _utcnow()
        self._prices.append((stock_id, iso_date, *values, self.batch_id, source_row, now, now))

        for column, value in (row.get("__extra__") or {}).items():
            if value is None or str(value).strip() == "":
                continue
            self._extras.append((symbol, iso_date, str(column), str(value), self.batch_id,
                                 key, first_in_chunk))

        self.accepted += 1
        if self.progress is not None:
            interval = max(200, (self.total_hint // 100) if self.total_hint else 200)
            if self.accepted % interval == 0:
                # ``total`` is 0 when the workbook does not record its size; the
                # caller then shows an indeterminate bar rather than a false 100%.
                self.progress(self.accepted, self.total_hint,
                              f"imported {self.accepted:,} rows")
        if len(self._prices) >= self.chunk_size:
            self._drain()

    def _stock_id(self, symbol: str) -> int:
        """Resolve (or create) a stock id.  The dimension is loaded once."""
        if not self._stocks_loaded:
            rows = self.connection.exec_driver_sql("SELECT id, symbol FROM stocks").fetchall()
            self.stock_ids = {str(name): int(stock_id) for stock_id, name in rows}
            self._stocks_loaded = True
        stock_id = self.stock_ids.get(symbol)
        if stock_id is not None:
            return stock_id
        now = _utcnow()
        result = self.connection.exec_driver_sql(
            "INSERT INTO stocks (symbol, record_count, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (symbol, now, now))
        stock_id = int(result.lastrowid)
        self.stock_ids[symbol] = stock_id
        return stock_id

    # -- writing ---------------------------------------------------------

    def _drain(self) -> None:
        if not self._prices and not self._extras:
            return
        if self.mode == "bulk":
            # One commit per chunk: resumable and durable at the chunk boundary,
            # without a per-row fsync.  The connection's implicit transaction
            # already spans everything since the previous commit.
            self._write_chunk()
            self._persist_progress()
            self.connection.commit()
        else:
            self._write_chunk()

    def _write_chunk(self) -> None:
        rows, keys, extras = self._prices, self._keys, self._extras
        self._prices, self._keys, self._extras, self._chunk_seen = [], [], [], set()
        if not rows:
            self._store_extras(extras, set(), keep_all=True)
            return

        existing = self._existing_keys(keys)
        inserted_keys = set(keys) - existing

        if self.policy == "skip":
            self.connection.exec_driver_sql(_PRICE_INSERT_OR_IGNORE, rows)
            self.skipped += len(rows) - len(inserted_keys)
        else:
            if self.policy == "new_batch" and existing:
                self._archive_superseded()
            self.connection.exec_driver_sql(_PRICE_INSERT_UPSERT, rows)
            self.updated += len(rows) - len(inserted_keys)
        self.inserted += len(inserted_keys)
        self._store_extras(extras, inserted_keys, keep_all=self.policy != "skip")

    def _existing_keys(self, keys: Sequence[tuple]) -> set:
        """The subset of ``keys`` that already has a row in ``prices``."""
        if not keys:
            return set()
        self.connection.exec_driver_sql(_TEMP_KEYS)
        self.connection.exec_driver_sql("DELETE FROM _import_keys")
        self.connection.exec_driver_sql(
            "INSERT OR IGNORE INTO _import_keys (stock_id, trade_date) VALUES (?, ?)", keys)
        rows = self.connection.exec_driver_sql(
            "SELECT k.stock_id, k.trade_date FROM _import_keys k "
            "WHERE EXISTS (SELECT 1 FROM prices p "
            "              WHERE p.stock_id = k.stock_id AND p.trade_date = k.trade_date)"
        ).fetchall()
        return {(int(stock_id), str(trade_date)) for stock_id, trade_date in rows}

    def _store_extras(self, extras: Sequence[tuple], inserted_keys: set,
                      keep_all: bool) -> None:
        if not extras:
            return
        payload = []
        for symbol, iso_date, column, value, batch_id, key, first_in_chunk in extras:
            # A row that was skipped as a duplicate is not stored at all, and an
            # in-file duplicate is only stored once — exactly like the row-by-row
            # implementation this replaces.
            if not keep_all and not (first_in_chunk and key in inserted_keys):
                continue
            payload.append((symbol, iso_date, column, value, batch_id))
        if payload:
            self.connection.exec_driver_sql(_EXTRAS_INSERT, payload)

    def _archive_superseded(self) -> None:
        """Copy the superseded values into ``price_extras`` (``new_batch`` policy)."""
        suffix = f" (superseded by batch {self.batch_id})"
        for canonical, column in _MARKET_COLUMNS:
            quoted = f'"{column}"'
            self.connection.exec_driver_sql(
                "INSERT INTO price_extras "
                "(price_id, batch_id, symbol, trade_date, column_name, value_text) "
                f"SELECT p.id, p.batch_id, s.symbol, p.trade_date, ?, CAST(p.{quoted} AS TEXT) "
                "FROM prices p "
                "JOIN _import_keys k ON p.stock_id = k.stock_id AND p.trade_date = k.trade_date "
                "JOIN stocks s ON s.id = p.stock_id "
                f"WHERE p.{quoted} IS NOT NULL",
                (f"{canonical}{suffix}",))

    def _persist_progress(self) -> None:
        """Record the resume watermark so an interrupted bulk import can continue."""
        self.connection.exec_driver_sql(
            "UPDATE import_batches SET rows_inserted = ?, rows_updated = ?, rows_skipped = ?, "
            "notes = ?, updated_at = ? WHERE id = ?",
            (self.inserted, self.updated, self.skipped,
             json.dumps({"resume_after_row": self._last_source_row}), _utcnow(),
             self.batch_id))

    def _persist_errors(self, validation: ValidationResult) -> None:
        payload = [
            (self.batch_id, issue.row_number, issue.column, issue.severity, issue.message,
             None if issue.raw_value is None else str(issue.raw_value))
            for issue in validation.issues if issue.severity == SEVERITY_ERROR
        ]
        if payload:
            self.connection.exec_driver_sql(_ERRORS_INSERT, payload)

    def _refresh_stock_aggregates(self) -> None:
        """One ``GROUP BY`` for every touched symbol instead of one query each."""
        ids = sorted(self.touched)
        if not ids:
            return
        now = _utcnow()
        for chunk in _chunks(ids, _ID_CHUNK):
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.connection.exec_driver_sql(
                f"SELECT stock_id, min(trade_date), max(trade_date), count(*) FROM prices "
                f"WHERE stock_id IN ({placeholders}) GROUP BY stock_id", tuple(chunk)).fetchall()
            payload = [(first, last, int(count), now, int(stock_id))
                       for stock_id, first, last, count in rows]
            if payload:
                self.connection.exec_driver_sql(
                    "UPDATE stocks SET first_date = ?, last_date = ?, record_count = ?, "
                    "updated_at = ? WHERE id = ?", payload)

    def finalize(self, report: ImportReport, validation: ValidationResult) -> None:
        """Flush, persist the trail and close out the batch row."""
        def work() -> None:
            self._write_chunk()
            self._persist_errors(validation)
            self._refresh_stock_aggregates()
            status = "completed" if validation.error_count == 0 else "completed_with_errors"
            now = _utcnow()
            self.connection.exec_driver_sql(
                "UPDATE import_batches SET rows_read = ?, rows_inserted = ?, rows_updated = ?, "
                "rows_skipped = ?, rows_rejected = ?, error_count = ?, status = ?, finished_at = ?, "
                "notes = NULL, updated_at = ? WHERE id = ?",
                (report.rows_read, self.inserted, self.updated, self.skipped,
                 validation.rejected_rows, validation.error_count, status, now, now,
                 self.batch_id))
            report.rows_inserted = self.inserted
            report.rows_updated = self.updated
            report.rows_skipped = self.skipped
            report.rows_rejected = validation.rejected_rows
            report.error_count = validation.error_count
            report.warning_count = validation.warning_count
            report.extra_columns = list(validation.extra_columns)
            report.issues = [issue.as_dict() for issue in validation.issues]
            report.status = status

        if self.mode == "bulk":
            work()
            self.connection.commit()
        else:
            work()


class ImportService:
    """Reads a worksheet, validates it and writes it into SQLite.

    ``strict`` mode (the default) is a single transaction: a failure leaves the
    database exactly as it was.  ``bulk`` mode commits the batch first and then
    commits every ``chunk_size`` rows so a multi-million-row file is resumable
    (``resume=True`` continues an interrupted batch for the same file hash).
    Both modes stream the worksheet, so memory does not grow with row count.
    """

    def __init__(self, database: Database):
        self.db = database

    # -- public ----------------------------------------------------------

    def run(
        self,
        path: str | Path,
        sheet: str,
        mapping: Mapping[str, str],
        duplicate_policy: str = "skip",
        header_row: Optional[int] = None,
        progress: Optional[ProgressCallback] = None,
        mode: str = "strict",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        issue_limit: int = DEFAULT_ISSUE_LIMIT,
        resume: bool = False,
        require_delivery: bool = False,
    ) -> ImportReport:
        path = Path(path)
        if duplicate_policy not in ("skip", "update", "new_batch"):
            raise ValueError(f"unknown duplicate policy {duplicate_policy!r}")
        if mode not in IMPORT_MODES:
            raise ValueError(f"unknown import mode {mode!r}")

        report = ImportReport(source_file=str(path), sheet_name=sheet,
                              duplicate_policy=duplicate_policy,
                              column_mapping=dict(mapping))
        # A missing file must fail before any row is written; hashing it first is
        # also cheaper than a second pass over a multi-gigabyte sheet.
        source_hash = file_digest(path)

        with DataStream(path, sheet=sheet, header_row=header_row) as stream:
            headers = list(stream.headers)
            total_hint = 0
            if stream.max_row:
                total_hint = max(0, int(stream.max_row) - int(stream.header_row or 0))

            writer = _BulkWriter(self.db, duplicate_policy, mode=mode,
                                 chunk_size=chunk_size, progress=progress,
                                 total_hint=total_hint)
            with writer:
                transaction = writer.connection.begin() if mode == "strict" else None
                try:
                    writer.prepare(str(path), source_hash, sheet, mapping, resume=resume)
                    report.batch_id = writer.batch_id
                    resume_after = writer.resume_after_row
                    resumed_rows = 0

                    def rows() -> Iterator[Dict[str, Any]]:
                        nonlocal resumed_rows
                        for record in stream.records(headers):
                            if resume_after and int(record.get("__row__") or 0) <= resume_after:
                                resumed_rows += 1
                                continue
                            yield record

                    validation = validate_stream(
                        headers, rows(), mapping,
                        require_delivery=require_delivery,
                        max_issues=issue_limit,
                        keep_rows=False,
                        on_row=writer.add,
                    )
                    report.rows_read = validation.records_seen + resumed_rows
                    writer.finalize(report, validation)
                    if transaction is not None:
                        transaction.commit()
                except BaseException:
                    if transaction is not None and transaction.is_active:
                        transaction.rollback()
                    raise

        report.finished_at = _dt.datetime.now()
        if progress:
            accepted = report.rows_inserted + report.rows_updated + report.rows_skipped
            progress(accepted, accepted, "done")
        self.db.log(
            "import",
            f"Imported {report.rows_inserted} new / {report.rows_updated} updated rows from "
            f"{path.name}[{sheet}]",
            severity="warning" if report.error_count else "info",
            entity=str(report.batch_id),
            payload=report.summary(),
        )
        return report


__all__ = [
    "ImportService",
    "ImportReport",
    "list_sheets",
    "preview_sheet",
    "auto_map_columns",
    "read_records",
    "iter_records",
    "file_digest",
    "CANONICAL_TO_DB",
    "DB_TO_CANONICAL",
]
