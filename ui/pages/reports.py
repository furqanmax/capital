"""Reports page — build a report, preview it, then export it."""

from __future__ import annotations


import pandas as pd
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton

from services.data_service import DataFilters
from services.report_service import REPORT_KINDS, REPORT_TITLES

from ui.dialogs import ExportDialog
from ui.widgets import Card, DataTable, FilterBar, ScreenerLegend

from .base import BasePage


class ReportsPage(BasePage):
    nav_title = "Reports"
    nav_icon = "⤓"

    def page_subtitle(self) -> str:
        return ("Excel, CSV and PDF exports. Every file records when it was generated, "
                "the filters in force and which columns were calculated.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.kind_combo = QComboBox()
        for kind in REPORT_KINDS:
            self.kind_combo.addItem(REPORT_TITLES[kind], kind)
        self.kind_combo.currentIndexChanged.connect(self.refresh)
        self.header.add_action(QLabel("Report"))
        self.header.add_action(self.kind_combo)

        self.preview_button = QPushButton("Refresh preview")
        self.preview_button.clicked.connect(self.refresh)
        self.export_button = QPushButton("Export…")
        self.export_button.setObjectName("Primary")
        self.export_button.clicked.connect(self._export)
        self.header.add_action(self.preview_button)
        self.header.add_action(self.export_button)

        self.filters = FilterBar(parent=self)
        self.filters.changed.connect(self.refresh)
        self.body.addWidget(self.filters)

        # ---- quick export row -------------------------------------------
        quick = Card(padding=10)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Quick export:"))
        for fmt in ("xlsx", "csv", "pdf"):
            button = QPushButton(fmt.upper())
            button.clicked.connect(lambda _checked=False, f=fmt: self._quick_export(f))
            row.addWidget(button)
        row.addStretch(1)
        self.scope_label = QLabel("")
        self.scope_label.setObjectName("Muted")
        row.addWidget(self.scope_label)
        quick.add_layout(row)
        self.body.addWidget(quick)

        # ---- summary + preview ------------------------------------------
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)

        self.notes_label = QLabel("")
        self.notes_label.setObjectName("Muted")
        self.notes_label.setWordWrap(True)
        self.body.addWidget(self.notes_label)

        self.table = DataTable(theme=ctx.theme, page_size=100)
        self.table.set_workbook_header(True)
        self.legend = ScreenerLegend(ctx.theme)
        self.legend.setVisible(ctx.settings.show_colour_legend())
        self.body.addWidget(self.legend)
        self.body.addWidget(self.table, 1)

        self._report = None

    # ------------------------------------------------------------------

    def load(self) -> None:
        dates = self.ctx.data.dates()
        if dates:
            self.filters.set_dates(dates)
            self.filters.set_default_range(self.ctx.settings.default_date_range_days())
        self.refresh()

    def current_kind(self) -> str:
        return self.kind_combo.currentData() or "screener"

    def current_filters(self) -> DataFilters:
        return DataFilters(
            symbols=self.filters.symbols(),
            start_date=self.filters.start_date(),
            end_date=self.filters.end_date(),
            as_of=self.filters.as_of(),
        ).normalised()

    def refresh(self) -> None:
        filters = self.current_filters()
        kind = self.current_kind()
        described = filters.describe()
        self.scope_label.setText(" · ".join(f"{k}: {v}" for k, v in described.items()))

        needs_data = kind != "classification" or self.ctx.data.symbols()
        if not self.ctx.data.summary().get("records") and needs_data:
            self.summary.setText("No data has been imported yet — import a workbook first.")
            self.table.set_frame(pd.DataFrame())
            self._report = None
            return

        try:
            report = self.ctx.reports.build(kind, filters)
        except Exception as exc:  # noqa: BLE001 - shown inline, never a crash
            self.summary.setText(f"Could not build the report: {type(exc).__name__}: {exc}")
            self.table.set_frame(pd.DataFrame())
            self._report = None
            return

        self._report = report
        columns = {str(column) for column in report.frame.columns}
        self.legend.setVisible(
            self.ctx.settings.show_colour_legend()
            and bool(columns & {"d_ind", "dv1", "dv2"}))
        self.summary.setText(
            f"{report.title} · {report.rows:,} rows · {len(report.frame.columns)} columns · "
            f"formula mode: {self.ctx.settings.formula_mode()}"
        )
        self.notes_label.setText(report.notes)
        self.table.set_frame(self.ctx.reports.preview(report, rows=200))
        self.notify(f"Previewed {report.title}")

    # ------------------------------------------------------------------

    def _export(self) -> None:
        if self._report is None:
            self.warn("Nothing to export", "Build a report first.")
            return
        ExportDialog(self, ctx=self.ctx, title=self._report.title,
                     suggested_kind=self.current_kind(),
                     filters=self.current_filters()).exec()
        self.notify("Export finished")

    def _quick_export(self, fmt: str) -> None:
        if self._report is None:
            self.warn("Nothing to export", "Build a report first.")
            return
        from services.export_service import default_export_path

        target = self.ctx.settings.export_dir() / default_export_path(
            self._report.title, fmt).name
        try:
            result = self.ctx.reports.export(self._report, path=target, fmt=fmt)
        except Exception as exc:  # noqa: BLE001
            self.warn("Export failed", f"{type(exc).__name__}: {exc}")
            return
        self.info("Export complete",
                  f"{result.rows:,} rows written to:\n{result.path}\n\n"
                  f"Generated at {result.generated_at:%Y-%m-%d %H:%M:%S}.")

    def on_theme_changed(self, theme: str) -> None:
        self.table.set_theme(theme)
        self.legend.set_theme(theme)
