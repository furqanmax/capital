"""Dashboard page — renders the active layout's components."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from services.data_service import DataFilters
from services.dashboard_service import Payload

from ui.charts import ChartCard
from ui.widgets import Card, DataTable, FilterBar, KpiCard

from .base import BasePage


class DashboardPage(BasePage):
    nav_title = "Dashboard"
    nav_icon = "▦"

    def page_subtitle(self) -> str:
        return ("Every figure below is computed by the analytics engine from the "
                "stored data — columns the workbook calculated are marked in exports.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._widgets: List[QWidget] = []

        # ---- header actions -------------------------------------------
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumWidth(150)
        self.layout_combo.currentTextChanged.connect(self._on_layout_changed)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("Primary")
        self.refresh_button.clicked.connect(self.refresh)
        self.manage_button = QPushButton("Manage layout…")
        self.manage_button.setToolTip("Open the Dashboard Builder in Settings")
        self.manage_button.clicked.connect(self._open_manager)

        self.header.add_action(QLabel("Layout"))
        self.header.add_action(self.layout_combo)
        self.header.add_action(self.refresh_button)
        self.header.add_action(self.manage_button)

        # ---- filters ----------------------------------------------------
        self.filters = FilterBar(parent=self)
        self.filters.changed.connect(self.refresh)
        self.body.addWidget(self.filters)

        # ---- scrollable canvas -----------------------------------------
        self.canvas = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas)
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas_layout.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidget(self.canvas)
        self.body.addWidget(self.scroll, 1)

        self.empty_label = QLabel(
            "There is no data yet. Use **Import Data** to load a workbook, then come back."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.canvas_layout.addWidget(self.empty_label)

        self._on_manager = None
        self._layout_guard = False

    # ------------------------------------------------------------------

    def set_manager_callback(self, callback) -> None:
        """The main window injects navigation to the Dashboard Builder."""
        self._on_manager = callback

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        dates = self.ctx.data.dates()
        if dates:
            self.filters.set_dates(dates)
        self._reload_layouts()
        self._render()

    def _reload_layouts(self) -> None:
        names = self.ctx.dashboard.layouts()
        active = self.ctx.dashboard.active_layout()
        self._layout_guard = True
        self.layout_combo.clear()
        self.layout_combo.addItems(names)
        if active in names:
            self.layout_combo.setCurrentText(active)
        self._layout_guard = False

    def _on_layout_changed(self, name: str) -> None:
        if self._layout_guard or not name:
            return
        try:
            self.ctx.dashboard.set_active_layout(name)
        except ValueError:
            return
        self._render()
        self.notify(f"Dashboard layout switched to {name}")

    def _open_manager(self) -> None:
        if self._on_manager is not None:
            self._on_manager()

    # ------------------------------------------------------------------

    def current_filters(self) -> DataFilters:
        return DataFilters(
            symbols=self.filters.symbols(),
            start_date=self.filters.start_date(),
            end_date=self.filters.end_date(),
            as_of=self.filters.as_of(),
        ).normalised()

    def _clear_canvas(self) -> None:
        for widget in self._widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._widgets = []
        while self.canvas_layout.count():
            item = self.canvas_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render(self) -> None:
        filters = self.current_filters()
        payloads = self.ctx.dashboard.render_all(filters, visible_only=True)
        self._clear_canvas()

        if not payloads:
            self.empty_label = QLabel("This layout has no visible components. "
                                      "Add some in Settings ▸ Dashboard Builder.")
            self.empty_label.setObjectName("Muted")
            self.empty_label.setAlignment(Qt.AlignCenter)
            self.canvas_layout.addWidget(self.empty_label)
            self._widgets.append(self.empty_label)
            self.notify("Nothing to display")
            return

        kpis = [p for p in payloads if p.kind == "kpi"]
        charts = [p for p in payloads if p.kind in ("line", "bar", "pie")]
        tables = [p for p in payloads if p.kind == "table"]

        if kpis:
            grid = QWidget()
            grid_layout = QGridLayout(grid)
            grid_layout.setContentsMargins(0, 0, 0, 0)
            grid_layout.setSpacing(10)
            columns = max(2, min(4, len(kpis)))
            for index, payload in enumerate(kpis):
                grid_layout.addWidget(self._kpi_widget(payload), index // columns,
                                      index % columns)
            self.canvas_layout.addWidget(grid)
            self._widgets.append(grid)

        for index in range(0, len(charts), 2):
            row = QWidget()
            row_layout = QGridLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            for offset, payload in enumerate(charts[index:index + 2]):
                row_layout.addWidget(self._chart_widget(payload), 0, offset)
            self.canvas_layout.addWidget(row)
            self._widgets.append(row)

        for payload in tables:
            self.canvas_layout.addWidget(self._table_widget(payload))

        self.canvas_layout.addStretch(1)
        self.notify(f"Rendered {len(payloads)} components "
                    f"({len(kpis)} KPIs, {len(charts)} charts, {len(tables)} tables)")

    # -- component widgets ------------------------------------------------

    def _kpi_widget(self, payload: Payload) -> QWidget:
        card = KpiCard(payload.title, self.ctx.theme)
        if payload.error:
            card.set_value("n/a", payload.error, "danger")
        else:
            card.set_value(payload.text or "—",
                           self._kpi_caption(payload.component),
                           payload.status)
        return card

    def _kpi_caption(self, component) -> str:
        from services.dashboard_service import metric_label

        return f"{metric_label(component.metric)} · {component.aggregation}"

    def _chart_widget(self, payload: Payload) -> QWidget:
        card = ChartCard(payload.title, self.ctx.theme, height=250)
        if payload.error:
            card.set_message(f"Could not build this chart: {payload.error}")
            return card
        points = [(p.get("x", ""), p.get("y")) for p in payload.series]
        if payload.kind == "pie":
            card.set_pie([(p.get("label", ""), p.get("value"))
                          for p in payload.series])
        elif payload.kind == "bar":
            card.set_bar(points, payload.component.metric)
        else:
            card.set_line(points, payload.component.metric)
        return card

    def _table_widget(self, payload: Payload) -> QWidget:
        card = Card(padding=10)
        title = QLabel(payload.title)
        title.setObjectName("SectionTitle")
        card.add(title)
        if payload.error:
            message = QLabel(f"Could not build this table: {payload.error}")
            message.setObjectName("Muted")
            card.add(message)
            return card
        table = DataTable(theme=self.ctx.theme, page_size=25)
        table.set_workbook_header(True)
        table.set_frame(payload.frame)
        table.view.setMinimumHeight(240)
        card.add(table)
        return card

    def on_theme_changed(self, theme: str) -> None:
        self._render()
