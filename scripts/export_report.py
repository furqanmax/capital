#!/usr/bin/env python3
"""Build and export a report from the command line.

    python3 scripts/export_report.py --kind screener --format xlsx
    python3 scripts/export_report.py --kind history --symbol ABB --format csv
    python3 scripts/export_report.py --kind lookback --format pdf --out /tmp/lookback.pdf

Reports: screener, history, lookback, classification, formulas, dashboard.
"""

from __future__ import annotations

import argparse
import datetime as _dt

from _common import add_common_arguments, banner, fail

from database.db import Database
from services.data_service import DataFilters
from services.export_service import EXPORT_FORMATS
from services.report_service import REPORT_KINDS, ReportService


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--kind", required=True, choices=REPORT_KINDS)
    parser.add_argument("--format", default="xlsx", choices=EXPORT_FORMATS)
    parser.add_argument("--symbol", action="append", default=[],
                        help="restrict to a symbol (repeatable)")
    parser.add_argument("--from", dest="start", type=str, default=None,
                        help="start date YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=str, default=None,
                        help="end date YYYY-MM-DD")
    parser.add_argument("--as-of", dest="as_of", type=str, default=None,
                        help="screener reference date YYYY-MM-DD")
    parser.add_argument("--include-calculated", action="store_true",
                        help="append Formula Lab columns (history report)")
    parser.add_argument("--out", default=None, help="destination file")
    parser.add_argument("--note", default="", help="note recorded inside the file")
    args = parser.parse_args(argv)

    try:
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        as_of = _parse_date(args.as_of)
    except argparse.ArgumentTypeError as exc:
        return fail(str(exc))

    db = Database(args.db) if args.db else Database()
    db.init_schema()
    service = ReportService(db)

    filters = DataFilters(symbols=[s.upper() for s in args.symbol],
                          start_date=start, end_date=end, as_of=as_of).normalised()

    banner(f"{args.kind} report → {args.format}")
    for key, value in filters.describe().items():
        print(f"  {key:<12}: {value}")

    try:
        report = service.build(args.kind, filters,
                               include_calculated=args.include_calculated)
    except Exception as exc:  # noqa: BLE001
        return fail(f"could not build the report: {type(exc).__name__}: {exc}")

    if args.note:
        report.notes = f"{report.notes} {args.note}".strip()

    print(f"  rows        : {report.rows:,}")
    print(f"  columns     : {len(report.frame.columns)}")

    if report.rows == 0:
        print("\nNothing to export — the report is empty for these filters.")
        return 2

    try:
        result = service.export(report, path=args.out, fmt=args.format)
    except Exception as exc:  # noqa: BLE001
        return fail(f"export failed: {type(exc).__name__}: {exc}")

    print(f"\nWrote {result.path}")
    print(f"  rows        : {result.rows:,}")
    print(f"  generated   : {result.generated_at:%Y-%m-%d %H:%M:%S}")
    print("  provenance  : the file records the filters, the formula mode and marks "
          "every calculated column with an asterisk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
