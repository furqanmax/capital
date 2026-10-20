#!/usr/bin/env python3
"""Import a workbook into the database from the command line.

    python3 scripts/import_workbook.py
    python3 scripts/import_workbook.py --sheet Data --policy update
    python3 scripts/import_workbook.py --workbook other.xlsm --db /tmp/test.db

By default the importer runs in a single transaction: if anything fails, the
database is left exactly as it was.  Pass ``--mode bulk`` for very large files:
it commits every chunk and can resume an interrupted run.  Exit code is 2 when
rows were rejected for validation reasons.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from _common import add_common_arguments, add_workbook_arguments, banner, fail

from database.db import Database
from services.excel_import import (
    ImportService,
    auto_map_columns,
    list_sheets,
    preview_sheet,
)
from services.workbook_reader import DATE_COLUMN, SYMBOL_COLUMN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    add_workbook_arguments(parser)
    parser.add_argument("--sheet", default=None,
                        help="worksheet to import (default: the preferred/Data sheet)")
    parser.add_argument("--policy", default="skip",
                        choices=("skip", "update", "new_batch"),
                        help="what to do with an existing (symbol, date) row")
    parser.add_argument("--header-row", type=int, default=None,
                        help="1-based header row (default: auto-detected)")
    parser.add_argument("--map", default=None,
                        help='override a mapping, e.g. --map \'{"delivery_pct": "DELIV%%"}\'')
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report without writing anything")
    parser.add_argument("--mode", default="strict", choices=("strict", "bulk"),
                        help="strict = one transaction (all or nothing); "
                             "bulk = commit every chunk and resume after a crash")
    parser.add_argument("--no-resume", action="store_true",
                        help="bulk mode: start a new batch instead of resuming "
                             "an interrupted one for the same file")
    args = parser.parse_args(argv)

    path = Path(args.workbook)
    if not path.exists():
        return fail(f"workbook not found: {path}")

    db = Database(args.db) if args.db else Database()
    db.init_schema()

    sheets = list_sheets(path)
    sheet = args.sheet or next(
        (s for s in ("Data", "data", "Sheet1") if s in sheets), sheets[0])

    banner(f"Importing {path.name} [{sheet}] into {db.path}")
    payload = preview_sheet(path, sheet, rows=5)
    mapping = auto_map_columns(payload["headers"])
    if args.map:
        try:
            mapping.update(json.loads(args.map))
        except json.JSONDecodeError as exc:
            return fail(f"--map is not valid JSON: {exc}")

    header_row = args.header_row or payload["header_row"]
    print(f"Header row     : {header_row}")
    print(f"Columns        : {len(payload['headers'])}")
    print("Mapping:")
    for canonical, source in sorted(mapping.items()):
        print(f"  {canonical:<16} <- {source}")
    unmapped = [h for h in payload["headers"] if h not in mapping.values()]
    if unmapped:
        print(f"Unmapped       : {', '.join(map(str, unmapped))}  (kept in price_extras)")

    for required in (SYMBOL_COLUMN, DATE_COLUMN):
        if required not in mapping:
            return fail(f"the {required!r} column could not be mapped; use --map to set it")

    if args.dry_run:
        from services.excel_import import read_records
        from services.validation import validate_records

        payload = read_records(path, sheet, header_row)
        result = validate_records(payload["headers"], payload["records"], mapping)
        print(f"\nDry run        : {result.summary()}")
        for issue in result.issues[:20]:
            print(f"  row {issue.row_number}: {issue.severity}: {issue.message}")
        return 2 if result.error_count else 0

    def progress(current: int, total: int, message: str = "") -> None:
        if total and (current == total or current % max(1, total // 10) == 0):
            print(f"  {current:,}/{total:,} rows  {message}")

    report = ImportService(db).run(path, sheet, mapping,
                                   duplicate_policy=args.policy,
                                   header_row=header_row, progress=progress,
                                   mode=args.mode,
                                   resume=(args.mode == "bulk" and not args.no_resume))

    banner("Result")
    print(f"Batch          : #{report.batch_id} ({report.status})")
    print(f"Rows read      : {report.rows_read:,}")
    print(f"Inserted       : {report.rows_inserted:,}")
    print(f"Updated        : {report.rows_updated:,}")
    print(f"Skipped        : {report.rows_skipped:,}")
    print(f"Rejected       : {report.rows_rejected:,}")
    print(f"Errors/warns   : {report.error_count}/{report.warning_count}")
    print(f"Duration       : {report.duration_seconds:.2f}s")
    if report.extra_columns:
        print(f"Extra columns  : {', '.join(report.extra_columns)}")

    summary = db.summary_counts()
    print(f"\nDatabase now   : {summary['stocks']:,} symbols, {summary['records']:,} rows, "
          f"{summary['earliest_date']} → {summary['latest_date']}")

    # Keep the optional columnar mirror warm so the first dashboard view after a
    # CLI import does not pay for the copy.
    from services import query_engine

    if query_engine.duckdb_available():
        backend = query_engine.create_backend(db)
        if backend.name == "duckdb":
            print("Analytics      : updating the DuckDB mirror…", end=" ", flush=True)
            started = time.time()
            backend.invalidate()
            backend.sync()
            print(f"done in {time.time() - started:.1f}s")
            backend.close()
    return 2 if report.rows_rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
