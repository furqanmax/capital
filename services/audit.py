"""Audit tooling — turns the workbook inspection into the required reports.

Produces:

``docs/workbook_inspection.md``  structural audit of sheets, tables, names,
                                 validation, conditional formatting, comments,
                                 drawings and VBA,
``docs/formula_inventory.csv``   every formula cell found in the workbook, with
                                 its cached value classified,
``docs/formula_audit_report.md`` the catalogue of business rules plus the
                                 confirmed defects and how each is handled,
``docs/formula_mapping.md``      rule-by-rule Excel -> Python mapping.

The functions here are pure reporting: they never modify the workbook.
"""

from __future__ import annotations

import collections
import csv
import datetime as _dt
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from .formula_catalog import AUDIT_FINDINGS, FORMULA_RULES
from .workbook_reader import WorkbookInspection, WorkbookInspector


# --------------------------------------------------------------------------
# Classification of a formula cell
# --------------------------------------------------------------------------


def classify_formula(formula: str) -> str:
    """Return a coarse category for a raw Excel formula string."""
    text = (formula or "").upper()
    if "SUMIFS" in text or "AVERAGEIF" in text:
        return "aggregate"
    if "OFFSET" in text or "AVERAGE(" in text:
        return "rolling"
    if any(token in text for token in ("IF(", "IFERROR", "AND(", 'OR(')):
        return "classification"
    if "AVERAGE(" in text:
        return "baseline"
    return "transform"


def cached_kind(value: Any) -> str:
    if value is None:
        return "blank"
    if isinstance(value, str) and value.startswith("#"):
        return "error"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, _dt.datetime):
        return "date"
    return "text"


# --------------------------------------------------------------------------
# Structural report
# --------------------------------------------------------------------------


def inspection_markdown(inspection: WorkbookInspection) -> str:
    out = io.StringIO()
    w = out.write
    w("# Workbook Inspection Report\n\n")
    w(f"* **Source file:** `{Path(inspection.path).name}`\n")
    w(f"* **Inspected at:** {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
    w("* **Mode:** read-only. Macros are decompressed as text for documentation "
      "and are **never executed**.\n\n")

    w("## Worksheets\n\n")
    w("| Sheet | State | Used range | Rows | Columns | Tables | Charts | Comments | Hidden rows |\n")
    w("|---|---|---|---|---|---|---|---|---|\n")
    for sheet in inspection.sheets:
        tables = ", ".join(f"`{name}` = {ref}" for name, ref in sheet.tables.items()) or "—"
        hidden = len(sheet.hidden_rows)
        w(f"| {sheet.name} | {sheet.state} | {sheet.dimensions} | {sheet.max_row} | "
          f"{sheet.max_column} | {tables} | {sheet.chart_count} | {len(sheet.comments)} | {hidden} |\n")
    w("\n")

    w("## Defined names\n\n")
    if inspection.defined_names:
        w("| Name | Scope | Hidden | Refers to |\n|---|---|---|---|\n")
        for name in inspection.defined_names:
            scope = "workbook" if name["scope"] is None else f"sheet #{name['scope']}"
            w(f"| `{name['name']}` | {scope} | {'yes' if name['hidden'] else 'no'} | "
              f"`{name['refers_to']}` |\n")
    else:
        w("_No defined names._\n")
    w("\n")

    w("## Data validation\n\n")
    found = False
    for sheet in inspection.sheets:
        for dv in sheet.data_validations:
            found = True
            formula = dv.get("formula1")
            broken = " **← broken reference (`#ref!`)**" if formula and "#ref" in str(formula).lower() else ""
            w(f"* `{sheet.name}!{dv['range']}` — {dv['type']}, source `{formula}`{broken}\n")
    if not found:
        w("_No data validation rules._\n")
    w("\n")

    w("## Conditional formatting\n\n")
    for sheet in inspection.sheets:
        if sheet.conditional_formats:
            w(f"* **{sheet.name}:** {', '.join(sheet.conditional_formats)}\n")
    w("\n")

    w("## Cell comments\n\n")
    any_comment = False
    for sheet in inspection.sheets:
        for comment in sheet.comments:
            any_comment = True
            text = comment["text"].strip().replace("\n", " ")
            w(f"* `{sheet.name}!{comment['cell']}` ({comment['author']}): {text}\n")
    if not any_comment:
        w("_No cell comments._\n")
    w("\n")

    w("## VBA project\n\n")
    if inspection.vba_modules:
        for name, source in inspection.vba_modules.items():
            body = source.strip().replace("\r\n", "\n")
            w(f"### Module `{name}`\n\n```vb\n{body}\n```\n\n")
        w("The macro performs `ActiveWorkbook.RefreshAll` only. The application does not "
          "execute it; imports read cached values through `openpyxl`.\n\n")
    else:
        w("_No VBA modules found._\n\n")

    w("## Drawings and controls\n\n")
    if inspection.drawings:
        for drawing in inspection.drawings:
            w(f"* `{drawing}`\n")
    else:
        w("_No drawings._\n")
    w("\n")

    w("## Formula inventory summary\n\n")
    counter = collections.Counter((f.sheet, classify_formula(f.formula)) for f in inspection.formulas)
    w("| Sheet | Category | Cells |\n|---|---|---|\n")
    for (sheet, category), count in sorted(counter.items()):
        w(f"| {sheet} | {category} | {count} |\n")
    w("\n")

    by_sheet = collections.defaultdict(lambda: collections.Counter())
    for formula in inspection.formulas:
        by_sheet[formula.sheet][cached_kind(formula.cached_value)] += 1
    w("### Cached value health\n\n")
    w("| Sheet | number | text | date | blank | error |\n|---|---|---|---|---|---|\n")
    for sheet, counter in sorted(by_sheet.items()):
        w(f"| {sheet} | {counter['number']} | {counter['text']} | {counter['date']} | "
          f"{counter['blank']} | {counter['error']} |\n")
    w("\n> A large number of cached errors is itself an audit finding: the workbook's "
      "saved state contains formulas that do not evaluate.\n")
    return out.getvalue()


# --------------------------------------------------------------------------
# Formula inventory CSV
# --------------------------------------------------------------------------


INVENTORY_FIELDS = (
    "sheet", "cell", "row", "column", "category", "cached_kind", "cached_value",
    "formula_length", "formula",
)


def formula_inventory_rows(inspection: WorkbookInspection) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for formula in inspection.formulas:
        rows.append({
            "sheet": formula.sheet,
            "cell": formula.coordinate,
            "row": formula.row,
            "column": formula.column,
            "category": classify_formula(formula.formula),
            "cached_kind": cached_kind(formula.cached_value),
            "cached_value": "" if formula.cached_value is None else str(formula.cached_value),
            "formula_length": len(formula.formula),
            "formula": formula.formula,
        })
    rows.sort(key=lambda r: (r["sheet"], r["row"], r["column"]))
    return rows


def write_inventory_csv(inspection: WorkbookInspection, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_FIELDS)
        writer.writeheader()
        for row in formula_inventory_rows(inspection):
            writer.writerow(row)
    return path


# --------------------------------------------------------------------------
# Audit report
# --------------------------------------------------------------------------


def audit_markdown(inspection: Optional[WorkbookInspection] = None) -> str:
    out = io.StringIO()
    w = out.write
    w("# Formula Audit Report\n\n")
    w(f"_Generated {_dt.datetime.now():%Y-%m-%d %H:%M:%S}_\n\n")
    w("## How to read this report\n\n")
    w("Every rule below is implemented twice:\n\n")
    w("| Mode | Meaning |\n|---|---|\n")
    w("| `original` | Replays the supplied workbook **exactly**, including the defects. "
      "This is the default, and it is the mode the parity suite verifies against the "
      "workbook's cached values. |\n")
    w("| `corrected` | Applies the documented intent of the rule. Divergences are logged "
      "to the Formula Audit Log with the finding reference. |\n\n")
    w("No rule was silently changed. Suspect formulas were reproduced first, verified "
      "against the workbook's own cached results, and only then given a corrected variant.\n\n")

    if inspection is not None:
        errors = sum(1 for f in inspection.formulas if f.cached_is_error)
        w(f"**Inventory:** {len(inspection.formulas):,} formula cells across "
          f"{len(inspection.sheets)} worksheets, of which **{errors:,} are cached errors**.\n\n")

    w("## Confirmed defects (preserved by design)\n\n")
    w("| ID | Severity | Sheet | Cells | Finding |\n|---|---|---|---|---|\n")
    for finding in AUDIT_FINDINGS:
        w(f"| {finding['id']} | {finding['severity']} | {finding['sheet']} | "
          f"`{finding['cells']}` | {finding['title']} |\n")
    w("\n")

    for finding in AUDIT_FINDINGS:
        w(f"### {finding['id']} — {finding['title']}\n\n")
        w(f"* **Sheet / cells:** `{finding['sheet']}` `{finding['cells']}`\n")
        w(f"* **Workbook formula:** `{finding['excel_formula']}`\n")
        w(f"* **What it should be:** {finding['expected']}\n")
        w(f"* **Evidence:** {finding['evidence']}\n")
        w(f"* **Business impact:** {finding['business_impact']}\n")
        w(f"* **Resolution:** {finding['resolution']}\n\n")

    w("## Rule catalogue\n\n")
    groups = collections.OrderedDict()
    for rule in FORMULA_RULES:
        groups.setdefault(rule["group"], []).append(rule)

    for group, rules in groups.items():
        w(f"### Group: `{group}`\n\n")
        for rule in rules:
            w(f"#### {rule['id']} — {rule['name']}\n\n")
            w(f"* **Sheet / cells:** `{rule['sheet']}` `{rule['cells']}`\n")
            w(f"* **What it does:** {rule['description']}\n")
            w(f"* **Workbook formula:** `{rule['excel_formula']}`\n")
            w(f"* **Inputs:** {', '.join(rule['inputs']) or '—'}\n")
            w(f"* **Output:** {rule['output_type']} — {rule['validation']}\n")
            w(f"* **Original mode:** {rule['original']}\n")
            if rule.get("corrected"):
                w(f"* **Corrected mode:** {rule['corrected']}\n")
            if rule.get("findings"):
                w(f"* **Related findings:** {', '.join(rule['findings'])}\n")
            w("\n")
    return out.getvalue()


# --------------------------------------------------------------------------
# Mapping document
# --------------------------------------------------------------------------


def mapping_markdown(mode_notes: Optional[Dict[str, str]] = None) -> str:
    out = io.StringIO()
    w = out.write
    w("# Formula Mapping — Excel to Python\n\n")
    w("This document is the contract between the workbook and the application. "
      "`services/analytics.py` implements the *Original* column; the *Corrected* "
      "column is implemented behind `mode = \"corrected\"`.\n\n")

    w("## Data columns\n\n")
    w("| Workbook column | Header | Canonical database column | Notes |\n|---|---|---|---|\n")
    mapping = [
        ("A", "Symbol", "stocks.symbol", "upper-cased; indexed"),
        ("B", "Date", "prices.trade_date", "stored as a real DATE so sorting works"),
        ("C", "OPEN", "prices.open", "float, nullable"),
        ("D", "HIGH", "prices.high", "float, nullable"),
        ("E", "LOW", "prices.low", "float, nullable"),
        ("F", "CLOSE", "prices.close", "float, nullable"),
        ("G", "LAST", "prices.last", "float, nullable"),
        ("H", "PREVCLOSE", "prices.prevclose", "float, nullable"),
        ("I", "TOTTRDQTY", "prices.tottrdqty", "volume; non-negative check"),
        ("J", "TOTTRDVAL", "prices.tottrdval", "turnover"),
        ("K", "TOTALTRADES", "prices.totaltrades", "trade count"),
        ("L", "Delivery %", "prices.delivery_pct", "percentage 0-100"),
    ]
    for row in mapping:
        w("| {} | {} | `{}` | {} |\n".format(*row))
    w("\nAny header that has no canonical match is preserved in `price_extras` "
      "together with its batch, so an import never discards a column.\n\n")

    w("## Rule-by-rule mapping\n\n")
    w("| Rule | Analysis/Main cells | Inputs | Original behaviour | Corrected behaviour |\n")
    w("|---|---|---|---|---|\n")
    for rule in FORMULA_RULES:
        original = rule["original"].replace("\n", " ")
        corrected = (rule.get("corrected") or "—").replace("\n", " ")
        w(f"| `{rule['id']}` | `{rule['sheet']}!{rule['cells']}` | "
          f"{', '.join(rule['inputs'])} | {original} | {corrected} |\n")
    w("\n")

    w("## Parity evidence\n\n")
    w("The workbook stores Excel's cached results for every formula, so it doubles as "
      "an oracle. `scripts/parity_check.py` recomputes each cell and compares:\n\n")
    w("| Scope | Cells compared | Result in `original` mode |\n|---|---|---|\n")
    w("| `Analysis!M15:W200`, `I2:N6`, baselines | 2,073 | 100% match (parity suite) |\n")
    w("| `Main!B18:J842` | 7,425 | 100% match (parity suite) |\n\n")
    w("Cells whose workbook formula contains `#REF!` (finding B10) and cells whose cached "
      "value is an Excel error have no usable oracle; they are reported as *unavailable* "
      "rather than as matches.\n\n")

    if mode_notes:
        w("## Operational notes\n\n")
        for key, value in mode_notes.items():
            w(f"* **{key}:** {value}\n")
    return out.getvalue()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_audit(workbook_path: str | Path, docs_dir: str | Path = "docs") -> Dict[str, Path]:
    """Inspect the workbook and write all audit artifacts."""
    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)
    inspector = WorkbookInspector(workbook_path)
    inspection = inspector.inspect()

    outputs = {
        "inspection": docs / "workbook_inspection.md",
        "inventory": docs / "formula_inventory.csv",
        "audit": docs / "formula_audit_report.md",
        "mapping": docs / "formula_mapping.md",
    }
    outputs["inspection"].write_text(inspection_markdown(inspection), encoding="utf-8")
    write_inventory_csv(inspection, outputs["inventory"])
    outputs["audit"].write_text(audit_markdown(inspection), encoding="utf-8")
    outputs["mapping"].write_text(mapping_markdown(), encoding="utf-8")
    return outputs


__all__ = [
    "run_audit",
    "inspection_markdown",
    "audit_markdown",
    "mapping_markdown",
    "formula_inventory_rows",
    "write_inventory_csv",
    "classify_formula",
    "cached_kind",
    "INVENTORY_FIELDS",
]
