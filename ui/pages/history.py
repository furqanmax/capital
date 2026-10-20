"""Historical Data page — the imported session records, filtered and exportable."""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PySide6.QtWidgets import QLabel, QPushButton

from services.data_service import DataFilters

from ui.widgets import DataTable, FilterBar

from .base import BasePage


class HistoricalDataPage(BasePage):
    nav_title = "Historical Data"
    nav_icon = "▤"

    def page_subtitle(self) -> str:
        return ("The session records exactly as imported. Calculated columns are appended "
                "on the right and always marked with an asterisk in exports.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.calculated = False
        self.show_metrics_button = QPushButton("Show calculated columns")
        self.show_metrics_button.setCheckable(True)
        self.show_metrics_button.toggled.connect(self._toggle_calculated)
        self.export_button = QPushButton("Export…")
        self.export_button.setObjectName("Primary")
        self.export_button.clicked.connect(self._export)
        self.header.add_action(self.show_metrics_button)
        self.header.add_action(self.export_button)

        self.filters = FilterBar(parent=self)
        self.filters.changed.connect(self.refresh)
        self.body.addWidget(self.filters)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.body.addWidget(self.summary)

        self.table = DataTable(theme=ctx.theme, page_size=ctx.settings.page_size())
        self.body.addWidget(self.table, 1)

        self._frame: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------

    def load(self) -> None:
        dates = self.ctx.data.dates()
        if dates:
            self.filters.set_dates(dates)
            self.filters.set_default_range(self.ctx.settings.default_date_range_days())
        self.refresh()

    def current_filters(self) -> DataFilters:
        return DataFilters(
            symbols=self.filters.symbols(),
            start_date=self.filters.start_date(),
            end_date=self.filters.end_date(),
        ).normalised()

    #: The table pages through whatever it is given, but the frame itself is
    #: materialised — so cap it.  Without a date filter a multi-million-row
    #: database would otherwise be pulled into memory just to show 200 rows.
    MAX_ROWS = 200_000

    def refresh(self) -> None:
        filters = self.current_filters()
        frame = self.ctx.data.frame(filters, limit=self.MAX_ROWS + 1)
        truncated = len(frame) > self.MAX_ROWS
        if truncated:
            frame = frame.iloc[: self.MAX_ROWS]

        if frame.empty:
            self.summary.setText("No rows match the current filters.")
            self.table.set_frame(pd.DataFrame())
            self._frame = None
            return

        frame = frame.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
        if self.calculated:
            frame, results = self.ctx.formulas.apply(frame)
            failed = [r["formula"] for r in results if not r["ok"]]
            if failed:
                self.notify(f"Formula(s) skipped: {', '.join(failed)}")

        self._frame = frame
        note = (f" · showing the first {self.MAX_ROWS:,} — narrow the filters "
                "to see the rest" if truncated else "")
        self.summary.setText(
            f"{len(frame):,} rows · {frame['symbol'].nunique():,} symbols · "
            f"{_date(frame['trade_date'].min())} → {_date(frame['trade_date'].max())} · "
            f"{len(frame.columns)} columns{note}"
        )
        self.table.set_frame(frame, page_size=self.ctx.settings.page_size())
        self.notify("Historical data loaded")

    def _toggle_calculated(self, checked: bool) -> None:
        self.calculated = checked
        self.show_metrics_button.setText(
            "Hide calculated columns" if checked else "Show calculated columns")
        self.refresh()

    def _export(self) -> None:
        if self._frame is None or self._frame.empty:
            self.warn("Nothing to export", "Load some rows first.")
            return
        from ui.dialogs import ExportDialog

        ExportDialog(self, ctx=self.ctx, title="Historical Data",
                     suggested_kind="history", filters=self.current_filters()).exec()

    def on_theme_changed(self, theme: str) -> None:
        self.table.set_theme(theme)


def _date(value) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")
