"""Stock Explorer — one symbol in depth."""

from __future__ import annotations

from typing import List, Optional

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTabWidget, QVBoxLayout, QWidget

from ui.charts import ChartCard
from ui.widgets import Badge, DataTable, KpiCard, ScreenerLegend

from services.data_service import DataFilters
from services.formatting import format_number, format_percent

from .base import BasePage


class StockExplorerPage(BasePage):
    nav_title = "Stock Explorer"
    nav_icon = "⌕"

    def page_subtitle(self) -> str:
        return ("Per-session metrics for a single symbol, including the workbook's "
                "calculated columns, its 1D–10D lookback block and the price/volume trend.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        splitter = QSplitter(Qt.Horizontal)

        # ---- symbol list -------------------------------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter symbols…")
        self.search.textChanged.connect(self._filter_symbols)
        left_layout.addWidget(self.search)
        self.symbol_list = QListWidget()
        self.symbol_list.setMinimumWidth(190)
        self.symbol_list.currentItemChanged.connect(self._on_symbol_changed)
        left_layout.addWidget(self.symbol_list, 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        left_layout.addWidget(self.count_label)
        splitter.addWidget(left)

        # ---- detail ------------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.headline = QLabel("Select a symbol")
        self.headline.setObjectName("SectionTitle")
        right_layout.addWidget(self.headline)

        self.kpi_row = QHBoxLayout()
        self.kpis = {
            "last": KpiCard("Close", ctx.theme),
            "pct": KpiCard("% Change", ctx.theme),
            "delivery": KpiCard("Delivery %", ctx.theme),
            "volume": KpiCard("Volume", ctx.theme),
            "trades": KpiCard("Trades", ctx.theme),
            "action": KpiCard("Action", ctx.theme),
        }
        for card in self.kpis.values():
            self.kpi_row.addWidget(card)
        right_layout.addLayout(self.kpi_row)

        self.badges = QWidget()
        badge_layout = QHBoxLayout(self.badges)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.setSpacing(6)
        self.badge_widgets = {
            "v1": Badge("", ctx.theme, group="v1"),
            "d_ind": Badge("", ctx.theme, group="d_ind"),
            "dv1": Badge("", ctx.theme, group="dv1"),
            "dv2": Badge("", ctx.theme, group="dv2"),
            "vol_level": Badge("", ctx.theme, group="range"),
        }
        for key, badge in self.badge_widgets.items():
            caption = {"v1": "V1:", "d_ind": "D.Ind:", "dv1": "DV1:",
                       "dv2": "DV2:", "vol_level": "Vol:"}[key]
            badge_layout.addWidget(QLabel(caption))
            badge_layout.addWidget(badge)
        badge_layout.addStretch(1)
        right_layout.addWidget(self.badges)

        self.charts = QHBoxLayout()
        self.close_chart = ChartCard("Closing Price", ctx.theme, height=210)
        self.volume_chart = ChartCard("Traded Quantity", ctx.theme, height=210)
        self.charts.addWidget(self.close_chart)
        self.charts.addWidget(self.volume_chart)
        right_layout.addLayout(self.charts)

        self.tabs = QTabWidget()
        self.sessions = DataTable(theme=ctx.theme, page_size=50)
        self.sessions.set_workbook_header(True)
        self.lookbacks = DataTable(theme=ctx.theme, page_size=25)

        sessions_page = QWidget()
        sessions_layout = QVBoxLayout(sessions_page)
        sessions_layout.setContentsMargins(0, 0, 0, 0)
        sessions_layout.setSpacing(4)
        self.legend = ScreenerLegend(ctx.theme)
        sessions_layout.addWidget(self.legend)
        sessions_layout.addWidget(self.sessions, 1)

        self.tabs.addTab(sessions_page, "Sessions")
        self.tabs.addTab(self.lookbacks, "Lookback summary")
        right_layout.addWidget(self.tabs, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 900])
        self.body.addWidget(splitter, 1)

        self._symbols: List[str] = []
        self.current_symbol: Optional[str] = None

    # ------------------------------------------------------------------

    def load(self) -> None:
        self._symbols = self.ctx.data.symbols()
        self._populate(self._symbols)
        self.legend.setVisible(self.ctx.settings.show_colour_legend())
        if self._symbols:
            self.symbol_list.setCurrentRow(0)

    def refresh(self) -> None:
        keep = self.current_symbol
        self._symbols = self.ctx.data.symbols()
        self._populate(self._symbols)
        self.legend.setVisible(self.ctx.settings.show_colour_legend())
        if keep and keep in self._symbols:
            self._select(keep)
        elif self._symbols:
            self.symbol_list.setCurrentRow(0)
        else:
            self.headline.setText("No data imported yet")

    # ------------------------------------------------------------------

    def _populate(self, symbols: List[str]) -> None:
        self.symbol_list.blockSignals(True)
        self.symbol_list.clear()
        for symbol in symbols:
            QListWidgetItem(symbol, self.symbol_list)
        self.symbol_list.blockSignals(False)
        self.count_label.setText(f"{len(symbols)} symbols")
        self.detail_visible(bool(symbols))

    def detail_visible(self, visible: bool) -> None:
        for widget in list(self.kpis.values()) + [self.badges]:
            widget.setVisible(visible)

    def _filter_symbols(self, text: str) -> None:
        needle = text.strip().upper()
        filtered = [s for s in self._symbols if needle in s] if needle else self._symbols
        self._populate(filtered)

    def _select(self, symbol: str) -> None:
        for row in range(self.symbol_list.count()):
            item = self.symbol_list.item(row)
            if item.text() == symbol:
                self.symbol_list.setCurrentItem(item)
                return

    def _on_symbol_changed(self, current: Optional[QListWidgetItem], _previous=None) -> None:
        if current is None:
            return
        self.current_symbol = current.text()
        self._show(self.current_symbol)

    # ------------------------------------------------------------------

    def _show(self, symbol: str) -> None:
        frame = self.ctx.data.symbol_frame(symbol)
        metrics = self.ctx.data.metrics(symbol)
        lookback = self.ctx.data.lookback(symbol)

        self.headline.setText(f"{symbol} — {len(frame)} session(s)")

        if frame.empty:
            self.sessions.set_frame(pd.DataFrame())
            self.lookbacks.set_frame(pd.DataFrame())
            return

        latest = metrics.iloc[-1]
        self.kpis["last"].set_value(_fmt(latest.get("close")), "latest close")
        self.kpis["pct"].set_value(_pct(latest.get("pct_change")), "vs previous close")
        self.kpis["delivery"].set_value(_fmt(latest.get("delivery_pct"), 2), "delivery %")
        self.kpis["volume"].set_value(_fmt(latest.get("tottrdqty"), 0), "traded quantity")
        self.kpis["trades"].set_value(_fmt(latest.get("totaltrades"), 0), "trade count")
        self.kpis["action"].set_value(_fmt(latest.get("action"), 2), "quantity per trade")

        self.badge_widgets["v1"].set_class(str(latest.get("v1") or ""))

        # DV1/DV2 are screener columns, not per-session columns — compute the
        # screener row for this symbol at its own latest session.
        screening = self.ctx.data.screener(DataFilters(
            symbols=[symbol], as_of=latest.get("trade_date")))
        screen = screening.iloc[0] if not screening.empty else {}
        self.badge_widgets["d_ind"].set_class(
            str(screen.get("d_ind") or latest.get("trade_indicator") or ""))
        self.badge_widgets["dv1"].set_class(str(screen.get("dv1") or ""))
        self.badge_widgets["dv2"].set_class(str(screen.get("dv2") or ""))
        self.badge_widgets["vol_level"].set_class(str(latest.get("vol_level") or ""))

        points = [(pd.to_datetime(row.trade_date).strftime("%Y-%m-%d"), row.close)
                  for row in frame.itertuples(index=False)]
        self.close_chart.set_line(points, "close")
        volume_points = [(pd.to_datetime(row.trade_date).strftime("%Y-%m-%d"), row.tottrdqty)
                         for row in frame.itertuples(index=False)]
        self.volume_chart.set_bar(volume_points, "quantity")

        self.sessions.set_frame(self._display_sessions(metrics),
                                page_size=self.ctx.settings.page_size())
        self.lookbacks.set_frame(pd.DataFrame(lookback))
        self.notify(f"Loaded {symbol}")

    @staticmethod
    def _display_sessions(metrics: pd.DataFrame) -> pd.DataFrame:
        from services.analytics import COLUMN_LABELS

        frame = metrics.copy()
        frame.columns = [COLUMN_LABELS.get(c, c.replace("_", " ").title()) for c in frame.columns]
        return frame

    def on_theme_changed(self, theme: str) -> None:
        self.legend.set_theme(theme)
        self.sessions.set_theme(theme)
        self.lookbacks.set_theme(theme)
        if self.current_symbol:
            self._show(self.current_symbol)


def _fmt(value, digits: int = 2) -> str:
    return format_number(value, digits)


def _pct(value) -> str:
    return format_percent(value)
