#!/usr/bin/env python3
"""Import a whole folder of daily workbooks into one database.

    python3 scripts/import_folder.py --folder /data/daily
    python3 scripts/import_folder.py --folder /data/daily --pattern '*.xlsm' --policy update

Files are imported in name order (``2025-01-01.xlsx`` before ``2025-01-02.xlsx``
when names sort that way).  Each file is one import batch; a file that fails does
not stop the run, it is reported at the end.  Bulk mode is the default here: the
point of this script is a large multi-file backfill, and a resumed run continues
where the interrupted batch stopped instead of re-reading everything.

Exit code is 1 when a file could not be imported at all and 2 when rows were
rejected for validation reasons (the files themselves were still imported).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from _common import add_common_arguments, banner, fail

from database.db import Database
from services.excel_import import (
    ImportService,
    auto_map_columns,
    list_sheets,
    preview_sheet,
)
from services.workbook_reader import DATE_COLUMN, SYMBOL_COLUMN


def _pick_sheet(path: Path, requested: str | None) -> str:
    sheets = list_sheets(path)
    if not sheets:
        raise ValueError("workbook has no worksheets")
    return requested or next(
        (s for s in ("Data", "data", "Sheet1") if s in sheets), sheets[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--folder", required=True,
                        help="folder holding the workbooks")
    parser.add_argument("--pattern", default="*.xlsx",
                        help="glob selecting the workbooks (default: *.xlsx)")
    parser.add_argument("--sheet", default=None,
                        help="worksheet to import (default: the preferred/Data sheet)")
    parser.add_argument("--policy", default="skip",
                        choices=("skip", "update", "new_batch"),
                        help="what to do with an existing (symbol, date) row")
    parser.add_argument("--mode", default="bulk", choices=("strict", "bulk"),
                        help="bulk (default) commits per chunk and can resume")
    parser.add_argument("--no-resume", action="store_true",
                        help="start a new batch instead of resuming an interrupted one")
    parser.add_argument("--dry-run", action="store_true",
                        help="list the files and their row counts without writing")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        return fail(f"folder not found: {folder}")
    files = sorted(p for p in folder.glob(args.pattern) if p.is_file())
    if not files:
        return fail(f"no files matching {args.pattern!r} in {folder}")

    db = Database(args.db) if args.db else Database()
    db.init_schema()

    banner(f"Importing {len(files)} workbook(s) from {folder} into {db.path}")
    started = time.time()
    total_read = total_new = total_updated = total_skipped = total_rejected = 0
    rejected_any = False
    failures: list[tuple[Path, str]] = []

    for number, path in enumerate(files, start=1):
        try:
            sheet = _pick_sheet(path, args.sheet)
            payload = preview_sheet(path, sheet, rows=1)
            mapping = auto_map_columns(payload["headers"])
            missing = [c for c in (SYMBOL_COLUMN, DATE_COLUMN)
                       if c not in mapping]
            if missing:
                raise ValueError(f"could not map {', '.join(missing)}")

            if args.dry_run:
                print(f"[{number:>3}/{len(files)}] {path.name:<40} "
                      f"sheet {sheet!r}, {len(payload['headers'])} columns")
                continue

            report = ImportService(db).run(
                path, sheet, mapping,
                duplicate_policy=args.policy,
                header_row=payload["header_row"],
                mode=args.mode,
                resume=(args.mode == "bulk" and not args.no_resume),
            )
            total_read += report.rows_read
            total_new += report.rows_inserted
            total_updated += report.rows_updated
            total_skipped += report.rows_skipped
            total_rejected += report.rows_rejected
            rejected_any = rejected_any or bool(report.rows_rejected)
            print(f"[{number:>3}/{len(files)}] {path.name:<40} "
                  f"{report.rows_read:>8,} read  {report.rows_inserted:>8,} new  "
                  f"{report.rows_updated:>6,} upd  {report.rows_skipped:>8,} skip  "
                  f"{report.duration_seconds:6.1f}s")
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            failures.append((path, f"{type(exc).__name__}: {exc}"))
            print(f"[{number:>3}/{len(files)}] {path.name:<40} FAILED — {exc}")

    elapsed = time.time() - started
    banner("Result")
    if args.dry_run:
        print(f"{len(files)} file(s) would be imported in {elapsed:.1f}s")
        return 0 if not failures else 1

    print(f"Files imported : {len(files) - len(failures):,} of {len(files):,}")
    print(f"Rows read      : {total_read:,}")
    print(f"Inserted       : {total_new:,}")
    print(f"Updated        : {total_updated:,}")
    print(f"Skipped        : {total_skipped:,}")
    print(f"Rejected       : {total_rejected:,}")
    print(f"Duration       : {elapsed:.1f}s"
          + (f" ({total_read / elapsed:,.0f} rows/s)" if elapsed > 0 else ""))

    summary = db.summary_counts()
    print(f"Database now   : {summary['stocks']:,} symbols, {summary['records']:,} rows, "
          f"{summary['earliest_date']} → {summary['latest_date']}")

    if failures:
        print(f"\n{len(failures)} file(s) failed:")
        for path, message in failures:
            print(f"  {path.name}: {message}")

    # Warm the optional columnar mirror once, after the whole backfill.
    from services import query_engine

    if query_engine.duckdb_available():
        backend = query_engine.create_backend(db)
        if backend.name == "duckdb":
            print("Analytics      : updating the DuckDB mirror…", end=" ", flush=True)
            mirror_started = time.time()
            try:
                backend.invalidate()
                backend.sync()
                print(f"done in {time.time() - mirror_started:.1f}s")
            except Exception as exc:  # noqa: BLE001 - analytics is optional
                print(f"skipped ({exc})")
            finally:
                backend.close()

    if failures:
        return 1
    return 2 if rejected_any else 0


if __name__ == "__main__":
    raise SystemExit(main())
