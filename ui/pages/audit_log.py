"""Formula Audit Log page.

Three views:

* **Audit log** — every recorded action (imports, formula changes, exports,
  dashboard edits, maintenance) straight from the ``audit_log`` table.
* **Formula divergences** — a live scan comparing *original* and *corrected*
  metric modes so a difference is never hidden.
* **Documented defects** — the defects found in the supplied workbook, kept
  next to the code that reproduces them.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from services.analytics import AnalyticsParams, compute_symbol_metrics
from services.export_service import ExportRequest, default_export_path, export
from services.formula_catalog import FORMULA_RULES

from ui.widgets import Card, DataTable, KpiCard

from .base import BasePage

#: Columns compared by the divergence scan.
COMPARE_COLUMNS = (
    "vol_ma", "v1", "vol_badge", "badge_bronze", "badge_silver", "badge_gold",
    "vol_level", "trade_indicator",
)


class AuditLogPage(BasePage):
    nav_title = "Formula Audit Log"
    nav_icon = "⚖"

    def page_subtitle(self) -> str:
        return ("Every change is recorded. Divergences between the workbook-exact "
                "“original” mode and the documented “corrected” mode are listed, "
                "never silently applied.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.export_button = QPushButton("Export log…")
        self.export_button.clicked.connect(self._export)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("Primary")
        self.refresh_button.clicked.connect(self.refresh)
        self.header.add_action(self.export_button)
        self.header.add_action(self.refresh_button)

        # ---- KPI strip ----------------------------------------------------
        kpi_row = QHBoxLayout()
        self.kpis = {
            "total": KpiCard("Log entries", ctx.theme),
            "warnings": KpiCard("Warnings", ctx.theme),
            "errors": KpiCard("Errors", ctx.theme),
            "divergences": KpiCard("Divergences", ctx.theme),
        }
        for card in self.kpis.values():
            kpi_row.addWidget(card)
        self.body.addLayout(kpi_row)

        self.tabs = QTabWidget()

        # ---- tab 1: audit log --------------------------------------------
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(10, 10, 10, 10)

        filter_card = Card(padding=10)
        row = QHBoxLayout()
        row.addWidget(QLabel("Category"))
        self.category_combo = QComboBox()
        self.category_combo.addItem("all", "all")
        self.category_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.category_combo)
        row.addWidget(QLabel("Severity"))
        self.severity_combo = QComboBox()
        for value in ("all", "info", "warning", "error"):
            self.severity_combo.addItem(value, value)
        self.severity_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.severity_combo)
        row.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("text in the message")
        self.search_edit.returnPressed.connect(self.refresh)
        row.addWidget(self.search_edit, 1)
        row.addWidget(QLabel("Limit"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(50, 20000)
        self.limit_spin.setValue(500)
        self.limit_spin.setSingleStep(100)
        self.limit_spin.valueChanged.connect(self.refresh)
        row.addWidget(self.limit_spin)
        filter_card.add_layout(row)
        self.audit_hint = QLabel("")
        self.audit_hint.setObjectName("Muted")
        filter_card.add(self.audit_hint)
        log_layout.addWidget(filter_card)

        self.audit_table = DataTable(theme=ctx.theme, page_size=200)
        log_layout.addWidget(self.audit_table, 1)
        self.tabs.addTab(log_page, "Audit log")

        # ---- tab 2: divergences ------------------------------------------
        div_page = QWidget()
        div_layout = QVBoxLayout(div_page)
        div_layout.setContentsMargins(10, 10, 10, 10)
        controls = QHBoxLayout()
        scan_button = QPushButton("Scan now")
        scan_button.setObjectName("Primary")
        scan_button.clicked.connect(self._scan_divergences)
        controls.addWidget(scan_button)
        controls.addWidget(QLabel(
            "Compares every stored symbol in “original” and “corrected” mode."))
        controls.addStretch(1)
        div_layout.addLayout(controls)
        self.divergence_label = QLabel("No scan has run yet.")
        self.divergence_label.setObjectName("Muted")
        self.divergence_label.setWordWrap(True)
        div_layout.addWidget(self.divergence_label)
        self.divergence_table = DataTable(theme=ctx.theme, page_size=100)
        div_layout.addWidget(self.divergence_table, 1)
        self.tabs.addTab(div_page, "Formula divergences")

        # ---- tab 3: documented defects ------------------------------------
        defect_page = QWidget()
        defect_layout = QVBoxLayout(defect_page)
        defect_layout.setContentsMargins(10, 10, 10, 10)
        note = QLabel(
            "These are the defects verified against the supplied workbook. The engine "
            "reproduces them in “original” mode and corrects them in “corrected” mode; "
            "the choice is always explicit and always logged.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        defect_layout.addWidget(note)
        self.defect_table = DataTable(theme=ctx.theme, page_size=50)
        defect_layout.addWidget(self.defect_table, 1)
        self.tabs.addTab(defect_page, "Documented defects")

        self.body.addWidget(self.tabs, 1)

        self._load_defects()
        self._divergence_rows: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------

    def load(self) -> None:
        self._reload_categories()
        self.refresh()

    def refresh(self) -> None:
        if self.category_combo.count() <= 1:
            self._reload_categories()
        category = self.category_combo.currentData()
        severity = self.severity_combo.currentData()
        entries = self.ctx.data.audit_log(
            category=category, severity=severity,
            search=self.search_edit.text().strip(),
            limit=self.limit_spin.value())
        frame = pd.DataFrame(entries) if entries else pd.DataFrame(
            columns=["id", "time", "category", "severity", "entity", "message"])
        if "payload" in frame.columns:
            frame["payload"] = frame["payload"].apply(
                lambda v: "" if v is None else str(v))
        self.audit_table.set_frame(frame, page_size=200)

        counts = self.ctx.data.audit_counts()
        self.kpis["total"].set_value(f"{counts['total']:,}", "all recorded entries")
        self.kpis["warnings"].set_value(
            f"{counts['warnings']:,}", "need review",
            "warning" if counts["warnings"] else "neutral")
        self.kpis["errors"].set_value(
            f"{counts['errors']:,}", "failed actions",
            "danger" if counts["errors"] else "good")
        self.kpis["divergences"].set_value(
            f"{len(self._divergence_rows):,}", "original vs corrected")
        self.audit_hint.setText(
            f"Showing {len(frame):,} of {counts['total']:,} entries "
            f"(filtered by category “{category}” and severity “{severity}”).")
        self.notify("Audit log refreshed")

    def _reload_categories(self) -> None:
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("all", "all")
        for category in self.ctx.data.audit_categories():
            self.category_combo.addItem(category, category)
        index = self.category_combo.findData(current)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        self.category_combo.blockSignals(False)

    # ------------------------------------------------------------------

    def _scan_divergences(self) -> None:
        symbols = self.ctx.data.symbols()
        if not symbols:
            self.divergence_label.setText("No data has been imported, so there is "
                                          "nothing to compare.")
            return

        original_params = AnalyticsParams(mode="original")
        corrected_params = AnalyticsParams(mode="corrected")

        from ui.workers import run_async

        def job(progress, is_cancelled):
            collected: List[Dict[str, Any]] = []
            counts: Dict[str, int] = {}
            total = len(symbols)
            for index, symbol in enumerate(symbols):
                if is_cancelled():
                    break
                if index % 25 == 0:
                    progress(int(index * 100 / max(1, total)),
                             f"{index:,}/{total:,} symbols")
                frame = self.ctx.data.symbol_frame(symbol)
                if frame.empty:
                    continue
                before = compute_symbol_metrics(frame, original_params)
                after = compute_symbol_metrics(frame, corrected_params)
                for column in COMPARE_COLUMNS:
                    if column not in before.columns or column not in after.columns:
                        continue
                    differing = [
                        i for i in range(len(before))
                        if not _same(before[column].iloc[i], after[column].iloc[i])
                    ]
                    if not differing:
                        continue
                    counts[column] = counts.get(column, 0) + len(differing)
                    last = differing[-1]
                    collected.append({
                        "symbol": symbol,
                        "column": column,
                        "sessions differing": len(differing),
                        "latest session": before["trade_date"].iloc[last],
                        "original": before[column].iloc[last],
                        "corrected": after[column].iloc[last],
                    })
            return collected, counts

        def done(result) -> None:
            collected, counts = result
            self._divergence_rows = collected
            self.divergence_table.set_frame(
                pd.DataFrame(collected) if collected else pd.DataFrame(
                    columns=["symbol", "column", "sessions differing", "latest session",
                             "original", "corrected"]))
            if collected:
                breakdown = ", ".join(f"{column}: {n:,}" for column, n in
                                      sorted(counts.items(), key=lambda kv: -kv[1]))
                self.divergence_label.setText(
                    f"{len(collected):,} symbol/column pairs differ between original and "
                    f"corrected mode across {len(symbols):,} symbols. Cells differing — "
                    f"{breakdown}. Switch modes in Settings ▸ Formulas.")
            else:
                self.divergence_label.setText(
                    "No differences: every compared metric is identical in both modes "
                    "for the current data.")
            self.refresh()
            self.notify("Divergence scan finished — see the table below")

        def failed(message: str, detail: str) -> None:
            self.divergence_label.setText(f"Scan failed: {message}")
            self.ctx.db.log("audit", f"Divergence scan failed: {message}", severity="error")

        self.divergence_label.setText("Scanning…")
        run_async(self, job, done, failed)

    # ------------------------------------------------------------------

    def _load_defects(self) -> None:
        rows = []
        for rule in FORMULA_RULES:
            findings = rule.get("findings") or []
            if not findings and not rule.get("corrected"):
                continue
            rows.append({
                "rule": rule["id"],
                "sheet": rule.get("sheet", ""),
                "cells": rule.get("cells", ""),
                "metric": rule.get("name", ""),
                "excel formula": rule.get("excel_formula", ""),
                "original behaviour": rule.get("original", ""),
                "corrected behaviour": rule.get("corrected", ""),
                "findings": " | ".join(findings),
            })
        self.defect_table.set_frame(
            pd.DataFrame(rows) if rows else pd.DataFrame(
                columns=["rule", "metric", "findings"]))

    # ------------------------------------------------------------------

    def _export(self) -> None:
        frame = self.audit_table.frame()
        if frame.empty:
            self.warn("Nothing to export", "The audit log is empty.")
            return
        target = self.ctx.settings.export_dir() / default_export_path(
            "Formula Audit Log", "xlsx").name
        try:
            result = export(ExportRequest(
                title="Formula Audit Log",
                frame=frame,
                path=target,
                format="xlsx",
                filters={"category": self.category_combo.currentData(),
                         "severity": self.severity_combo.currentData(),
                         "search": self.search_edit.text().strip()},
                notes="Every entry recorded by the application, newest first.",
                extra_sheets={},
            ))
        except Exception as exc:  # noqa: BLE001
            self.warn("Export failed", f"{type(exc).__name__}: {exc}")
            return
        self.info("Export complete", f"Log written to:\n{result.path}")

    def on_theme_changed(self, theme: str) -> None:
        self.audit_table.set_theme(theme)
        self.divergence_table.set_theme(theme)
        self.defect_table.set_theme(theme)


def _same(left: Any, right: Any) -> bool:
    left_blank = left is None or (isinstance(left, float) and left != left)
    right_blank = right is None or (isinstance(right, float) and right != right)
    if left_blank and right_blank:
        return True
    if left_blank != right_blank:
        return False
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) < 1e-9
    return str(left) == str(right)
