#!/usr/bin/env python3
"""Verify the analytics engine against the workbook's cached values.

    python3 scripts/parity_check.py
    python3 scripts/parity_check.py --mode corrected --tolerance 1e-9
    python3 scripts/parity_check.py --no-db          # workbook only, no database needed

The workbook's cached results are the oracle: every calculated cell in
``Analysis`` and ``Main`` is recomputed by the application and compared.  In the
default ``original`` mode the check must be perfect; ``corrected`` mode is
expected to differ (that is the whole point of the mode) and is therefore
reported but never treated as a failure.

Exit code: 0 when the check passes, 1 when it fails, 2 when it could not run.
Writes ``docs/parity_report.md`` unless ``--no-report`` is given.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import DOCS_DIR, add_common_arguments, add_workbook_arguments, banner, fail

from database.db import Database
from services.analytics import MODE_CORRECTED, MODE_ORIGINAL
from services.parity import DEFAULT_TOLERANCE, run_all


def _write_report(reports, path: Path, workbook: str, mode: str, tolerance: float) -> None:
    lines = [
        "# Parity report",
        "",
        f"* Workbook: `{workbook}`",
        f"* Mode: **{mode}**",
        f"* Tolerance: `{tolerance}`",
        "",
        "| Scope | Compared | Matched | Unavailable | Failed | Match rate | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        verdict = "PASS" if report.passed else "FAIL"
        lines.append(
            f"| {report.name} | {report.compared:,} | {report.matched:,} | "
            f"{report.unavailable:,} | {report.failed:,} | "
            f"{report.match_rate * 100:.4f}% | **{verdict}** |")
    total = sum(r.compared for r in reports)
    matched = sum(r.matched for r in reports)
    lines += ["", f"Total cells compared: **{total:,}**; matched: **{matched:,}**."]
    for report in reports:
        if report.notes:
            lines += ["", f"## {report.name} notes", ""]
            lines += [f"* {note}" for note in report.notes]
        if report.mismatches:
            lines += ["", f"## {report.name} mismatches (first 50)", "",
                      "| Cell | Expected | Actual | Delta | Kind |", "|---|---|---|---|---|"]
            for mismatch in report.mismatches[:50]:
                item = mismatch.as_dict()
                lines.append(f"| `{item['cell']}` | {item['expected']} | {item['actual']} | "
                             f"{item.get('delta', '')} | {item.get('kind', '')} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    add_workbook_arguments(parser)
    parser.add_argument("--mode", default=MODE_ORIGINAL,
                        choices=(MODE_ORIGINAL, MODE_CORRECTED))
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--no-db", action="store_true",
                        help="skip the database (Analysis parity only)")
    parser.add_argument("--limit", type=int, default=25,
                        help="how many mismatches to print")
    parser.add_argument("--no-report", action="store_true",
                        help="do not write docs/parity_report.md")
    args = parser.parse_args(argv)

    path = Path(args.workbook)
    if not path.exists():
        return fail(f"workbook not found: {path}")

    banner(f"Parity check — mode={args.mode}, tolerance={args.tolerance}")

    data_frame = None
    if not args.no_db:
        db = Database(args.db) if args.db else Database()
        db.init_schema()
        data_frame = db.price_frame()
        if data_frame.empty:
            print("The database has no market data; importing it from the workbook first.")
            from services.excel_import import ImportService, auto_map_columns, preview_sheet

            payload = preview_sheet(path, "Data", rows=1)
            ImportService(db).run(path, "Data", auto_map_columns(payload["headers"]),
                                  duplicate_policy="skip", header_row=payload["header_row"])
            data_frame = db.price_frame()

    try:
        reports = run_all(path, data_frame, mode=args.mode, tolerance=args.tolerance)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return fail(f"parity check could not run: {type(exc).__name__}: {exc}")

    for report in reports:
        print(f"\n[{report.name}]")
        print(f"  compared     : {report.compared:,}")
        print(f"  matched      : {report.matched:,}")
        print(f"  unavailable  : {report.unavailable:,}")
        print(f"  failed       : {report.failed:,}")
        print(f"  match rate   : {report.match_rate * 100:.4f}%")
        for note in report.notes:
            print(f"  note         : {note}")
        for mismatch in report.mismatches[:args.limit]:
            item = mismatch.as_dict()
            print(f"  MISMATCH {item['cell']}: expected {item['expected']!r} "
                  f"got {item['actual']!r} (delta {item.get('delta')})")
        if report.failed > args.limit:
            print(f"  … and {report.failed - args.limit:,} more")

    if not args.no_report:
        target = Path(DOCS_DIR) / "parity_report.md"
        _write_report(reports, target, str(path), args.mode, args.tolerance)
        print(f"\nWrote {target}")

    failed = [r for r in reports if not r.passed]
    banner("Verdict")
    if args.mode == MODE_ORIGINAL:
        if failed:
            print("FAIL — the original mode must reproduce the workbook exactly.")
            return 1
        total = sum(r.compared for r in reports)
        print(f"PASS — {total:,} calculated cells match the workbook exactly.")
        return 0

    print(f"Corrected mode differs on {sum(r.failed for r in reports):,} cells "
          f"(expected — the corrections change the result).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
