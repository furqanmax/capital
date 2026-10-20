#!/usr/bin/env python3
"""Write the formula audit report and the formula mapping document.

    python3 scripts/audit_formulas.py
    python3 scripts/audit_formulas.py --workbook other.xlsm --out docs

Produces:

* ``docs/formula_audit_report.md`` — every rule in the catalogue, with the Excel
  formula, the Python equivalent, the validation applied and the defects found
  in the source workbook.
* ``docs/formula_mapping.md`` — the Excel-to-application column mapping and the
  original/corrected behaviour of each metric.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import DOCS_DIR, add_workbook_arguments, banner, fail

from services.audit import audit_markdown, mapping_markdown
from services.formula_catalog import FORMULA_RULES
from services.workbook_reader import WorkbookInspector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_workbook_arguments(parser)
    parser.add_argument("--out", default=str(DOCS_DIR), help="output folder for the reports")
    parser.add_argument("--no-inspection", action="store_true",
                        help="skip re-reading the workbook (audit report only)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    inspection = None
    if not args.no_inspection:
        path = Path(args.workbook)
        if not path.exists():
            return fail(f"workbook not found: {path}")
        banner(f"Reading {path.name}")
        inspection = WorkbookInspector(path).inspect()

    banner("Formula catalogue")
    groups: dict[str, int] = {}
    for rule in FORMULA_RULES:
        groups[rule["group"]] = groups.get(rule["group"], 0) + 1
    for group, count in sorted(groups.items()):
        print(f"  {group:<20} {count:>3} rule(s)")
    defect_count = sum(1 for r in FORMULA_RULES if r.get("findings"))
    print(f"  rules with recorded defects: {defect_count}")

    audit_path = out_dir / "formula_audit_report.md"
    audit_path.write_text(audit_markdown(inspection), encoding="utf-8")
    mapping_path = out_dir / "formula_mapping.md"
    mapping_path.write_text(mapping_markdown(), encoding="utf-8")

    print(f"\nWrote {audit_path}")
    print(f"Wrote {mapping_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
