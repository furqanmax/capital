"""Row-level validation for imported market data.

The validator never mutates the source data. It classifies every incoming row as
``ok``, ``warning`` or ``error`` and reports exactly which cell caused the
problem.  The importer decides what to do with each severity (errors are always
rejected; warnings are imported but recorded).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .workbook_reader import (
    DATE_COLUMN,
    SYMBOL_COLUMN,
    coerce_date,
    coerce_number,
    coerce_symbol,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

#: canonical column -> (numeric, minimum, maximum)
NUMERIC_RULES: Dict[str, Tuple[float, float]] = {
    "OPEN": (-1e12, 1e12),
    "HIGH": (-1e12, 1e12),
    "LOW": (-1e12, 1e12),
    "CLOSE": (-1e12, 1e12),
    "LAST": (-1e12, 1e12),
    "PREVCLOSE": (-1e12, 1e12),
    "TOTTRDQTY": (0.0, 1e15),
    "TOTTRDVAL": (0.0, 1e18),
    "TOTALTRADES": (0.0, 1e12),
    "Delivery %": (0.0, 100.0),
}


@dataclass
class Issue:
    row_number: Optional[int]
    column: Optional[str]
    severity: str
    message: str
    raw_value: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "row_number": self.row_number,
            "column_name": self.column,
            "severity": self.severity,
            "message": self.message,
            "raw_value": None if self.raw_value is None else str(self.raw_value),
        }


@dataclass
class ValidationResult:
    rows: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    duplicate_keys_in_file: List[Tuple[str, _dt.date]] = field(default_factory=list)
    extra_columns: List[str] = field(default_factory=list)
    #: rows dropped because at least one cell failed — a row may raise several
    #: issues, so this is deliberately *not* the same as ``error_count``.
    rejected_rows: int = 0
    #: Exact counters.  ``issues`` may be *sampled* (see ``max_issues``) so the
    #: totals are tracked separately and never recomputed by scanning the list.
    error_total: Optional[int] = None
    warning_total: Optional[int] = None
    duplicate_total: Optional[int] = None
    #: How many source rows were examined (including rejected ones).
    records_seen: int = 0
    #: True when the retained issue samples were capped.
    issues_truncated: bool = False

    @property
    def error_count(self) -> int:
        if self.error_total is not None:
            return self.error_total
        return sum(1 for i in self.issues if i.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        if self.warning_total is not None:
            return self.warning_total
        return sum(1 for i in self.issues if i.severity == SEVERITY_WARNING)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def summary(self) -> Dict[str, Any]:
        duplicates = (self.duplicate_total if self.duplicate_total is not None
                      else len(self.duplicate_keys_in_file))
        return {
            "rows_parsed": len(self.rows),
            "rows_read": self.records_seen,
            "rows_rejected": self.rejected_rows,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "duplicates_in_file": duplicates,
            "extra_columns": len(self.extra_columns),
            "issues_truncated": self.issues_truncated,
        }


def validate_stream(
    headers: Sequence[str],
    records: Iterable[Mapping[str, Any]],
    mapping: Mapping[str, str],
    require_delivery: bool = False,
    max_issues: Optional[int] = None,
    keep_rows: bool = True,
    on_row: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> ValidationResult:
    """Validate rows against the canonical schema, one at a time.

    ``mapping`` maps canonical column name -> source header.  Columns present in
    ``headers`` but absent from ``mapping`` are preserved as "extra" columns so
    that nothing is silently discarded.

    The validator is streaming: ``records`` may be any iterable (including a
    million-row generator) and memory stays flat.  ``max_issues`` caps how many
    :class:`Issue` samples are retained — the counters in
    :meth:`ValidationResult.summary` stay exact either way.  Accepted rows are
    appended to ``result.rows`` unless ``keep_rows`` is false, and are always
    handed to ``on_row`` so an importer can write them without buffering.
    """
    result = ValidationResult()
    mapped_headers = set(mapping.values())
    result.extra_columns = [h for h in headers
                            if h not in mapped_headers and not h.startswith("__")]
    extra_headers = list(result.extra_columns)

    errors = 0
    warnings = 0
    duplicates = 0
    rejected = 0
    seen_count = 0
    duplicate_samples = result.duplicate_keys_in_file
    symbol_source = mapping.get(SYMBOL_COLUMN, "")
    date_source = mapping.get(DATE_COLUMN, "")

    def add_issue(row_number: Optional[int], column: Optional[str], severity: str,
                  message: str, raw: Any) -> None:
        nonlocal errors, warnings
        if severity == SEVERITY_ERROR:
            errors += 1
        elif severity == SEVERITY_WARNING:
            warnings += 1
        if max_issues is None or len(result.issues) < max_issues:
            result.issues.append(Issue(row_number, column, severity, message, raw))
        else:
            result.issues_truncated = True

    seen: Dict[Tuple[str, _dt.date], int] = {}

    for record in records:
        seen_count += 1
        row_number = record.get("__row__")
        parsed: Dict[str, Any] = {}
        row_has_error = False

        # ---- symbol (required) ----------------------------------------
        raw_symbol = record.get(symbol_source)
        symbol = coerce_symbol(raw_symbol)
        if symbol is None:
            add_issue(row_number, SYMBOL_COLUMN, SEVERITY_ERROR, "missing symbol", raw_symbol)
            row_has_error = True
        else:
            parsed["symbol"] = symbol

        # ---- date (required) ------------------------------------------
        raw_date = record.get(date_source)
        trade_date = coerce_date(raw_date)
        if trade_date is None:
            add_issue(row_number, DATE_COLUMN, SEVERITY_ERROR,
                      "invalid or missing date", raw_date)
            row_has_error = True
        else:
            parsed["trade_date"] = trade_date
            if trade_date > _dt.date.today() + _dt.timedelta(days=1):
                add_issue(row_number, DATE_COLUMN, SEVERITY_WARNING,
                          f"date {trade_date} is in the future", raw_date)

        # ---- numeric columns ------------------------------------------
        for canonical, (low, high) in NUMERIC_RULES.items():
            if canonical not in mapping:
                continue
            raw = record.get(mapping[canonical])
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                if canonical == "Delivery %" and require_delivery:
                    add_issue(row_number, canonical, SEVERITY_WARNING,
                              "delivery % is blank", raw)
                parsed[canonical] = None
                continue
            value = coerce_number(raw)
            if value is None:
                add_issue(row_number, canonical, SEVERITY_ERROR,
                          f"non-numeric value for {canonical}", raw)
                row_has_error = True
                parsed[canonical] = None
                continue
            if value < low or value > high:
                add_issue(row_number, canonical, SEVERITY_WARNING,
                          f"{canonical}={value} outside expected range "
                          f"[{low}, {high}]", raw)
            parsed[canonical] = value

        # ---- OHLC sanity ----------------------------------------------
        _check_ohlc(parsed, row_number, add_issue)

        # ---- in-file duplicates ---------------------------------------
        if "symbol" in parsed and "trade_date" in parsed:
            key = (parsed["symbol"], parsed["trade_date"])
            if key in seen:
                duplicates += 1
                if max_issues is None or len(duplicate_samples) < max_issues:
                    duplicate_samples.append(key)
                add_issue(
                    row_number, None, SEVERITY_WARNING,
                    f"duplicate (symbol, date) inside the file; first seen on row {seen[key]}",
                    f"{key[0]} {key[1]}",
                )
            else:
                seen[key] = row_number if row_number is not None else -1

        if row_has_error:
            rejected += 1
            continue

        parsed["__row__"] = row_number
        # Blank extras carry no information; drop them here exactly as the
        # importer drops them, so the two agree on what "preserved" means.
        parsed["__extra__"] = {
            header: record.get(header)
            for header in extra_headers
            if record.get(header) is not None and str(record.get(header)).strip() != ""
        }
        if keep_rows:
            result.rows.append(parsed)
        if on_row is not None:
            on_row(parsed)

    result.rejected_rows = rejected
    result.error_total = errors
    result.warning_total = warnings
    result.duplicate_total = duplicates
    result.records_seen = seen_count
    return result


def validate_records(
    headers: Sequence[str],
    records: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, str],
    require_delivery: bool = False,
    max_issues: Optional[int] = None,
) -> ValidationResult:
    """Validate a materialised list of records (kept for compatibility).

    This is a thin wrapper over :func:`validate_stream` with the rows retained;
    the two can never disagree about a row because there is only one
    implementation.
    """
    return validate_stream(headers, records, mapping,
                           require_delivery=require_delivery,
                           max_issues=max_issues, keep_rows=True)


def _check_ohlc(parsed: Mapping[str, Any], row_number: Optional[int], add_issue) -> None:
    high = parsed.get("HIGH")
    low = parsed.get("LOW")
    if high is not None and low is not None and high < low:
        add_issue(row_number, "HIGH", SEVERITY_WARNING,
                  f"HIGH ({high}) is below LOW ({low})", high)
    for field_name in ("OPEN", "CLOSE", "LAST"):
        value = parsed.get(field_name)
        if value is None or high is None or low is None:
            continue
        if value > high or value < low:
            add_issue(row_number, field_name, SEVERITY_WARNING,
                      f"{field_name} ({value}) outside the HIGH/LOW band "
                      f"[{low}, {high}]", value)


def detect_duplicate_policy(policy: str) -> str:
    normalised = (policy or "skip").strip().lower()
    if normalised not in ("skip", "update", "new_batch"):
        raise ValueError(f"unknown duplicate policy {policy!r}")
    return normalised


__all__ = [
    "Issue",
    "ValidationResult",
    "validate_records",
    "validate_stream",
    "detect_duplicate_policy",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SEVERITY_INFO",
    "NUMERIC_RULES",
]
