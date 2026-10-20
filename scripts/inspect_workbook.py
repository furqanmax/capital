#!/usr/bin/env python3
"""Inspect a workbook and write the structural report.

    python3 scripts/inspect_workbook.py
    python3 scripts/inspect_workbook.py --workbook other.xlsm --out docs

Produces:

* ``docs/workbook_inspection.md`` — sheets, tables, defined names, comments,
  data validation, conditional formatting, charts, macro inventory and a sample
  of the formulas found (all read-only; macros are never executed).
* ``docs/formula_inventory.csv`` — every formula cell, classified.

Exit code is 1 when the workbook cannot be read at all.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import DOCS_DIR, add_workbook_arguments, banner, fail

from services.audit import inspection_markdown, write_inventory_csv
from services.workbook_reader import WorkbookInspector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_workbook_arguments(parser)
    parser.add_argument("--out", default=str(DOCS_DIR), help="output folder for the reports")
    parser.add_argument("--max-formula-rows", type=int, default=None,
                        help="limit the number of formula rows collected per sheet")
    args = parser.parse_args(argv)

    path = Path(args.workbook)
    if not path.exists():
        return fail(f"workbook not found: {path}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    banner(f"Inspecting {path.name}")
    inspector = WorkbookInspector(path)
    inspection = inspector.inspect(max_formula_rows=args.max_formula_rows)

    print(f"Sheets          : {', '.join(inspection.sheet_names)}")
    for info in inspection.sheets:
        tables = ", ".join(f"{n}={r}" for n, r in info.tables.items()) or "—"
        print(f"  {info.name:<12} range={info.dimensions:<14} tables={tables}")
    print(f"Formula cells   : {len(inspection.formulas):,}")
    print(f"VBA modules     : {', '.join(inspection.vba_modules) or '—'}")

    if inspection.vba_modules:
        print("\nMacro inventory (text only — never executed):")
        for name, code in inspection.vba_modules.items():
            first = next((line.strip() for line in code.splitlines()
                          if line.strip() and not line.strip().startswith("Attribute")), "")
            print(f"  {name:<20} {first[:70]}")

    markdown_path = out_dir / "workbook_inspection.md"
    markdown_path.write_text(inspection_markdown(inspection), encoding="utf-8")
    csv_path = write_inventory_csv(inspection, out_dir / "formula_inventory.csv")

    print(f"\nWrote {markdown_path}")
    print(f"Wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
