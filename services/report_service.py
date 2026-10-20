"""Report construction and export orchestration.

Each report is a named, filter-aware DataFrame plus provenance metadata.  The
actual file writing lives in :mod:`services.export_service`; this module decides
*what* goes into a report and makes sure calculated metrics are always labelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from database.db import Database
from .data_service import DataFilters, DataService
from .export_service import (
    EXPORT_FORMATS,
    ExportRequest,
    ExportResult,
    column_labels,
    default_export_path,
    display_frame,
    export,
    provenance_frame,
)
from .formula_service import FormulaService
from .settings_service import SettingsService

REPORT_KINDS = (
    "screener",
    "history",
    "lookback",
    "classification",
    "formulas",
    "dashboard",
)

REPORT_TITLES = {
    "screener": "Screener Report",
    "history": "Historical Data Report",
    "lookback": "Lookback Summary Report",
    "classification": "Classification Summary Report",
    "formulas": "Formula Lab Results",
    "dashboard": "Dashboard Snapshot",
}


@dataclass
class ReportDefinition:
    kind: str
    title: str
    frame: pd.DataFrame
    filters: Dict[str, Any]
    notes: str = ""

    @property
    def rows(self) -> int:
        return len(self.frame)


class ReportService:
    """Builds report frames and exports them to Excel, CSV or PDF."""

    def __init__(self, database: Database, data: Optional[DataService] = None,
                 formulas: Optional[FormulaService] = None,
                 settings: Optional[SettingsService] = None):
        self.db = database
        self.settings = settings or SettingsService(database)
        self.data = data or DataService(database, self.settings)
        self.formulas = formulas or FormulaService(database)

    # ------------------------------------------------------------------
    # report builders
    # ------------------------------------------------------------------

    def build(self, kind: str, filters: Optional[DataFilters] = None,
              **kwargs: Any) -> ReportDefinition:
        filters = (filters or DataFilters()).normalised()
        builder = {
            "screener": self._screener,
            "history": self._history,
            "lookback": self._lookback,
            "classification": self._classification,
            "formulas": self._formulas,
            "dashboard": self._dashboard,
        }.get(kind)
        if builder is None:
            raise ValueError(f"unknown report {kind!r}; expected one of {', '.join(REPORT_KINDS)}")
        return builder(filters, **kwargs)

    def _provenance(self, filters: DataFilters, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "formula_mode": self.settings.formula_mode(),
            "moving_average_days": self.settings.moving_average_days(),
        }
        payload.update(filters.describe())
        if extra:
            payload.update(extra)
        return payload

    def _screener(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        universe = kwargs.get("universe")
        frame = self.data.screener(filters, universe=universe)
        if not frame.empty:
            frame = frame.drop(columns=["trade_date"], errors="ignore")
        return ReportDefinition(
            kind="screener", title=REPORT_TITLES["screener"], frame=frame,
            filters=self._provenance(filters, {"universe": "imported symbols" if not universe
                                               else f"{len(list(universe))} symbols"}),
            notes="One row per symbol for the selected date. Volume/Delivery averages use "
                  "every stored session for that symbol.",
        )

    def _history(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        frame = self.data.frame(filters)
        note = "Raw imported session records, one row per symbol and date."
        if kwargs.get("include_calculated") and not frame.empty:
            frame, results = self.formulas.apply(frame)
            note += (" Formula Lab columns are appended and marked with an asterisk. "
                     + "; ".join(f"{r['formula']}: {'ok' if r['ok'] else r['error']}"
                                 for r in results))
        if not frame.empty:
            frame = frame.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
            symbol_col = frame.pop("symbol")
            frame.insert(0, "symbol", symbol_col)
        return ReportDefinition(
            kind="history", title=REPORT_TITLES["history"], frame=frame,
            filters=self._provenance(filters),
            notes=note,
        )

    def _lookback(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        symbols = filters.symbols or self.data.symbols()
        rows: List[Dict[str, Any]] = []
        for symbol in symbols:
            for entry in self.data.lookback(symbol):
                row = {"symbol": symbol}
                row.update(entry)
                rows.append(row)
        frame = pd.DataFrame.from_records(rows) if rows else pd.DataFrame(
            columns=["symbol", "window", "cmp", "avg_price", "pct_change", "delivery_pct",
                     "action", "cmp_verdict"])
        return ReportDefinition(
            kind="lookback", title=REPORT_TITLES["lookback"], frame=frame,
            filters=self._provenance(filters, {"symbols": len(symbols)}),
            notes="CMP and Delivery % use the workbook's four-session window in original mode; "
                  "Avg Price, % Change and Action use the full window.",
        )

    def _classification(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        counts = self.data.classification(filters)
        rows = [{"classification": group, "value": label, "stocks": count}
                for group, values in counts.items() for label, count in values.items()]
        frame = pd.DataFrame.from_records(rows) if rows else pd.DataFrame(
            columns=["classification", "value", "stocks"])
        return ReportDefinition(
            kind="classification", title=REPORT_TITLES["classification"], frame=frame,
            filters=self._provenance(filters),
            notes="Counts of D. Ind, DV1 and DV2 bands across the screener result.",
        )

    def _formulas(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        frame = self.data.frame(filters)
        results: List[Dict[str, Any]] = []
        if not frame.empty:
            applied, results = self.formulas.apply(frame)
            frame = applied
        note = "; ".join(
            f"{r['formula']}: {'ok' if r['ok'] else r['error']}" for r in results
        ) or "No enabled formula."
        return ReportDefinition(
            kind="formulas", title=REPORT_TITLES["formulas"], frame=frame,
            filters=self._provenance(filters, {"formulas": len(results)}),
            notes=f"Formula Lab outputs appended to the imported columns. {note}",
        )

    def _dashboard(self, filters: DataFilters, **kwargs: Any) -> ReportDefinition:
        from .dashboard_service import DashboardService

        service = DashboardService(self.db, self.data)
        rows: List[Dict[str, Any]] = []
        for payload in service.render_all(filters, visible_only=True):
            if payload.kind == "kpi":
                rows.append({"component": payload.title, "kind": "kpi",
                             "value": payload.text, "status": payload.status})
            elif payload.frame is not None:
                for record in payload.frame.head(50).itertuples(index=False):
                    rows.append({"component": payload.title, "kind": payload.kind,
                                 "value": " | ".join(str(v) for v in record), "status": ""})
            else:
                for point in payload.series[:50]:
                    label = point.get("label") or point.get("x")
                    rows.append({"component": payload.title, "kind": payload.kind,
                                 "value": f"{label}: {point.get('value') or point.get('y')}",
                                 "status": ""})
        frame = pd.DataFrame.from_records(rows) if rows else pd.DataFrame(
            columns=["component", "kind", "value", "status"])
        return ReportDefinition(
            kind="dashboard", title=REPORT_TITLES["dashboard"], frame=frame,
            filters=self._provenance(filters),
            notes="Snapshot of every visible dashboard component.",
        )

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    def export(self, report: ReportDefinition, path: Optional[str | Path] = None,
               fmt: Optional[str] = None, extra_sheets: Optional[Dict[str, pd.DataFrame]] = None
               ) -> ExportResult:
        fmt = (fmt or self.settings.get("export.default_format", "xlsx")).lower()
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"unsupported format {fmt!r}")
        if path is None:
            base = self.settings.export_dir()
            default = default_export_path(report.title, fmt)
            path = base / default.name
        request = ExportRequest(
            title=report.title,
            frame=report.frame,
            path=path,
            format=fmt,
            filters=report.filters,
            notes=report.notes,
            generated_by="Capital Analytics",
            extra_sheets=extra_sheets or {},
        )
        result = export(request)
        self.db.log("export", f"Exported {report.title} ({fmt}) to {result.path.name}",
                    payload=result.summary())
        return result

    def preview(self, report: ReportDefinition, rows: int = 200) -> pd.DataFrame:
        """Display-ready head of a report (labels applied, calculated marked)."""
        frame = display_frame(report.frame.head(rows))
        frame.columns = column_labels(report.frame)[:len(frame.columns)]
        return frame

    def provision_frame(self, report: ReportDefinition) -> pd.DataFrame:
        return provenance_frame(ExportRequest(
            title=report.title, frame=report.frame, path=".",
            format="xlsx", filters=report.filters, notes=report.notes))


__all__ = ["ReportService", "ReportDefinition", "REPORT_KINDS", "REPORT_TITLES"]
