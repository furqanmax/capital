"""Chart widgets.

Line, bar and pie charts are rendered by Plotly inside a ``QWebEngineView`` so
the application gets real interactive charts (hover, zoom, legend toggling) with
no server and no internet access — the Plotly bundle is written once to
``data/web_assets/plotly.min.js`` and loaded from disk.

If QtWebEngine is unavailable (for example in a minimal deployment), the widget
transparently falls back to a native ``QPainter`` chart, so a chart is *always*
drawn.  Every chart can also be exported to a standalone HTML file and to CSV.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, QUrl, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from database.db import PROJECT_ROOT

from .theme import palette, plotly_config, plotly_layout, series_color

ASSETS_DIR = PROJECT_ROOT / "data" / "web_assets"
PLOTLY_JS = ASSETS_DIR / "plotly.min.js"

_WEBENGINE_ERROR: Optional[str] = None


def webengine_available() -> bool:
    global _WEBENGINE_ERROR
    if _WEBENGINE_ERROR is not None:
        return False
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True
    except Exception as exc:  # pragma: no cover - deployment dependent
        _WEBENGINE_ERROR = str(exc)
        return False


def ensure_plotly_bundle() -> Optional[Path]:
    """Write the bundled plotly.js to disk once and return its path."""
    if PLOTLY_JS.exists() and PLOTLY_JS.stat().st_size > 100_000:
        return PLOTLY_JS
    try:
        from plotly.offline import get_plotlyjs

        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        PLOTLY_JS.write_text(get_plotlyjs(), encoding="utf-8")
        return PLOTLY_JS
    except Exception:  # pragma: no cover - plotly missing
        return None


# --------------------------------------------------------------------------
# Native fallback painting
# --------------------------------------------------------------------------


class NativeChart(QWidget):
    """Minimal, dependency-free chart painter (line / bar / pie)."""

    def __init__(self, theme: str = "dark", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme
        self.kind = "line"
        self.points: List[Tuple[str, float]] = []
        self.slices: List[Tuple[str, float]] = []
        self.y_label = ""
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_line(self, points: Sequence[Tuple[str, float]], y_label: str = "") -> None:
        self.kind, self.points, self.y_label = "line", list(points), y_label
        self.update()

    def set_bar(self, points: Sequence[Tuple[str, float]], y_label: str = "") -> None:
        self.kind, self.points, self.y_label = "bar", list(points), y_label
        self.update()

    def set_pie(self, slices: Sequence[Tuple[str, float]]) -> None:
        self.kind, self.slices = "pie", list(slices)
        self.update()

    def clear(self) -> None:
        self.points, self.slices = [], []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        c = palette(self.theme)
        painter.fillRect(self.rect(), QColor(c["panel"]))
        if self.kind == "pie":
            self._paint_pie(painter, c)
        else:
            self._paint_axes(painter, c)
        painter.end()

    # -- helpers ---------------------------------------------------------

    def _paint_pie(self, painter: QPainter, c: Dict[str, str]) -> None:
        if not self.slices:
            self._paint_empty(painter, c)
            return
        total = sum(max(0.0, value or 0.0) for _, value in self.slices) or 1.0
        rect = QRectF(12, 12, min(self.height(), self.width() - 180) - 24,
                      self.height() - 24)
        start = 90 * 16
        for index, (label, value) in enumerate(self.slices):
            span = int(-(max(0.0, value or 0.0) / total) * 360 * 16)
            painter.setBrush(QColor(series_color(index, self.theme)))
            painter.setPen(QPen(QColor(c["border"]), 1))
            painter.drawPie(rect, start, span)
            start += span
        painter.setPen(QColor(c["text"]))
        x = rect.right() + 18
        y = 24
        for index, (label, value) in enumerate(self.slices):
            painter.setBrush(QColor(series_color(index, self.theme)))
            painter.setPen(Qt.NoPen)
            painter.drawRect(int(x), int(y - 8), 10, 10)
            painter.setPen(QColor(c["text"]))
            painter.drawText(int(x + 16), int(y), f"{label}  ({value:,.0f})")
            y += 18
            if y > self.height() - 10:
                break

    def _paint_empty(self, painter: QPainter, c: Dict[str, str]) -> None:
        painter.setPen(QColor(c["text_dim"]))
        painter.drawText(self.rect(), Qt.AlignCenter, "No data for the current filters")

    def _paint_axes(self, painter: QPainter, c: Dict[str, str]) -> None:
        if not self.points:
            self._paint_empty(painter, c)
            return
        left, top, right, bottom = 58, 14, 14, 30
        plot = QRectF(left, top, max(10, self.width() - left - right),
                      max(10, self.height() - top - bottom))
        values = [v for _, v in self.points if v is not None]
        if not values:
            self._paint_empty(painter, c)
            return
        low, high = min(values), max(values)
        if high == low:
            high, low = high + 1, low - 1

        painter.setPen(QPen(QColor(c["grid"]), 1))
        for step in range(5):
            y = plot.bottom() - plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(c["text_dim"]))
            painter.setFont(QFont("DejaVu Sans", 8))
            value = low + (high - low) * step / 4
            painter.drawText(QRectF(0, y - 8, left - 6, 16),
                             Qt.AlignRight | Qt.AlignVCenter, f"{value:,.2f}")
            painter.setPen(QPen(QColor(c["grid"]), 1))

        count = len(self.points)
        step_x = plot.width() / max(1, count - 1 if self.kind == "line" else count)
        painter.setPen(QColor(c["text_dim"]))
        painter.setFont(QFont("DejaVu Sans", 8))
        label_stride = max(1, count // 8)
        for index, (label, _value) in enumerate(self.points):
            if index % label_stride:
                continue
            x = plot.left() + step_x * (index if self.kind == "line" else index + 0.5)
            painter.drawText(QRectF(x - 40, plot.bottom() + 4, 80, 18),
                             Qt.AlignHCenter | Qt.AlignTop, str(label))

        if self.kind == "bar":
            width = max(2.0, step_x * 0.62)
            for index, (_label, value) in enumerate(self.points):
                if value is None:
                    continue
                height = (value - low) / (high - low) * plot.height()
                x = plot.left() + step_x * index + (step_x - width) / 2
                painter.setBrush(QColor(series_color(0, self.theme)))
                painter.setPen(Qt.NoPen)
                painter.drawRect(QRectF(x, plot.bottom() - height, width, height))
        else:
            polygon = QPolygonF()
            for index, (_label, value) in enumerate(self.points):
                if value is None:
                    continue
                x = plot.left() + step_x * index
                y = plot.bottom() - (value - low) / (high - low) * plot.height()
                polygon.append(QPointF(x, y))
            painter.setPen(QPen(QColor(series_color(0, self.theme)), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(polygon)

        painter.setPen(QColor(c["border"]))
        painter.drawRect(plot)
        if self.y_label:
            painter.setPen(QColor(c["text_dim"]))
            painter.drawText(QRectF(0, 0, left - 8, 14), Qt.AlignRight, self.y_label[:12])


# --------------------------------------------------------------------------
# Plotly-backed chart
# --------------------------------------------------------------------------


class PlotlyView(QWidget):
    """Hosts a Plotly figure in a web view, with a native fallback."""

    def __init__(self, theme: str = "dark", height: int = 260,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme
        self.height = height
        self._figure: Optional[Dict[str, Any]] = None
        self._title = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        self.native = NativeChart(theme, self)
        self.native.setMinimumHeight(height)
        self._stack.addWidget(self.native)

        self.view = None
        self.bundle: Optional[Path] = None
        if webengine_available():
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView

                self.view = QWebEngineView(self)
                self.view.setMinimumHeight(height)
                self._stack.addWidget(self.view)
                self.bundle = ensure_plotly_bundle()
            except Exception:  # pragma: no cover - deployment dependent
                self.view = None

        if self.view is None or self.bundle is None:
            self._stack.setCurrentWidget(self.native)
        else:
            self._stack.setCurrentWidget(self.view)

    # -- public API -------------------------------------------------------

    def set_line(self, title: str, points: Sequence[Tuple[str, float]],
                 y_label: str = "") -> None:
        self._title = title
        x = [str(p[0]) for p in points]
        y = [_num(p[1]) for p in points]
        figure = {
            "data": [{
                "type": "scatter", "mode": "lines+markers", "x": x, "y": y,
                "name": title, "line": {"width": 2}, "marker": {"size": 4},
                "hovertemplate": "%{x}<br>%{y:,.2f}<extra></extra>",
            }],
            "layout": plotly_layout(self.theme, self.height),
        }
        figure["layout"]["yaxis"]["title"] = {"text": y_label}
        self._render(figure, "line", points, y_label)

    def set_bar(self, title: str, points: Sequence[Tuple[str, float]],
                y_label: str = "") -> None:
        self._title = title
        x = [str(p[0]) for p in points]
        y = [_num(p[1]) for p in points]
        figure = {
            "data": [{
                "type": "bar", "x": x, "y": y, "name": title,
                "marker": {"color": series_color(0, self.theme)},
                "hovertemplate": "%{x}<br>%{y:,.2f}<extra></extra>",
            }],
            "layout": plotly_layout(self.theme, self.height),
        }
        figure["layout"]["yaxis"]["title"] = {"text": y_label}
        self._render(figure, "bar", points, y_label)

    def set_pie(self, title: str, slices: Sequence[Tuple[str, float]]) -> None:
        self._title = title
        labels = [str(s[0]) for s in slices]
        values = [_num(s[1]) for s in slices]
        figure = {
            "data": [{
                "type": "pie", "labels": labels, "values": values, "hole": 0.42,
                "textinfo": "label+percent", "textfont": {"size": 10},
                "hovertemplate": "%{label}<br>%{value:,.0f} (%{percent})<extra></extra>",
            }],
            "layout": plotly_layout(self.theme, self.height),
        }
        figure["layout"]["hovermode"] = "closest"
        self._render(figure, "pie", slices, "")

    def set_message(self, text: str) -> None:
        self._figure = None
        self._title = text
        self.native.clear()
        if self.view is not None:
            self.view.setHtml(self._empty_html(text))

    def clear(self) -> None:
        self.set_message("No data for the current filters")

    def current_figure(self) -> Optional[Dict[str, Any]]:
        return self._figure

    def export_html(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._figure is None:
            raise ValueError("there is no chart to export")
        from plotly.offline import get_plotlyjs

        path.write_text(
            "<!doctype html><meta charset='utf-8'><title>"
            + html.escape(self._title)
            + "</title><body style='margin:0;background:"
            + palette(self.theme)["panel"] + "'>"
            + f"<script>{get_plotlyjs()}</script>"
            + "<div id='chart' style='width:100%;height:100vh'></div>"
            + "<script>Plotly.newPlot('chart', "
            + json.dumps(self._figure["data"])
            + ", " + json.dumps(self._figure["layout"]) + ", "
            + json.dumps(plotly_config()) + ");</script></body>",
            encoding="utf-8",
        )
        return path

    def export_csv(self, path: str | Path) -> Path:
        import csv

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if self._figure and self._figure["data"] and self._figure["data"][0]["type"] == "pie":
                writer.writerow(["label", "value"])
                for label, value in zip(self._figure["data"][0]["labels"],
                                        self._figure["data"][0]["values"]):
                    writer.writerow([label, value])
            elif self._figure and self._figure["data"]:
                writer.writerow(["x", "value"])
                for x, y in zip(self._figure["data"][0]["x"], self._figure["data"][0]["y"]):
                    writer.writerow([x, y])
        return path

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.native.theme = theme
        if self._figure is not None:
            self._figure["layout"].update(plotly_layout(theme, self.height))
            self._reload()
        self.native.update()

    # -- internals --------------------------------------------------------

    def _render(self, figure: Dict[str, Any], kind: str,
                points: Sequence[Tuple[str, Any]], y_label: str) -> None:
        self._figure = figure
        if kind == "pie":
            self.native.set_pie(points)
        elif kind == "bar":
            self.native.set_bar(points, y_label)
        else:
            self.native.set_line(points, y_label)

        if self.view is None:
            self._stack.setCurrentWidget(self.native)
            return
        self._reload()

    def _reload(self) -> None:
        if self.view is None or self._figure is None:
            return
        base = QUrl.fromLocalFile(str(ASSETS_DIR) + "/")
        self.view.setHtml(self._html(), base)

    def _html(self) -> str:
        payload = json.dumps({"data": self._figure["data"], "layout": self._figure["layout"]})
        config = json.dumps(plotly_config())
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<script src='{PLOTLY_JS.name}'></script>"
            "<style>html,body{margin:0;padding:0;background:"
            + palette(self.theme)["panel"] + ";overflow:hidden}</style>"
            "</head><body><div id='chart' style='width:100vw;height:100vh'></div>"
            "<script>const spec=" + payload + ";"
            "Plotly.newPlot('chart', spec.data, spec.layout, " + config + ")"
            ".catch(function(e){document.body.innerHTML="
            "'<p style=\"color:#F85149;font-family:sans-serif;padding:12px\">Chart error: '+e+'</p>';});"
            "</script></body></html>"
        )

    def _empty_html(self, text: str) -> str:
        return ("<!doctype html><meta charset='utf-8'><body style='margin:0;background:"
                + palette(self.theme)["panel"] + ";color:" + palette(self.theme)["text_dim"]
                + ";font-family:sans-serif;display:flex;align-items:center;"
                "justify-content:center;height:100vh'>" + html.escape(text) + "</body>")


class ChartCard(QWidget):
    """A titled card wrapping a :class:`PlotlyView` with export buttons."""

    def __init__(self, title: str, theme: str = "dark", height: int = 240,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        header.addWidget(label)
        header.addStretch(1)
        self.html_button = QPushButton("HTML")
        self.html_button.setToolTip("Open this chart as a standalone interactive HTML file")
        self.csv_button = QPushButton("CSV")
        self.csv_button.setToolTip("Export the chart's data points as CSV")
        for button in (self.html_button, self.csv_button):
            button.setFixedWidth(58)
            header.addWidget(button)
        layout.addLayout(header)

        self.chart = PlotlyView(theme, height, self)
        layout.addWidget(self.chart, 1)

        self.html_button.clicked.connect(self._export_html)
        self.csv_button.clicked.connect(self._export_csv)

    def set_line(self, points, y_label: str = "") -> None:
        self.chart.set_line(self.title, points, y_label)

    def set_bar(self, points, y_label: str = "") -> None:
        self.chart.set_bar(self.title, points, y_label)

    def set_pie(self, slices) -> None:
        self.chart.set_pie(self.title, slices)

    def set_message(self, text: str) -> None:
        self.chart.set_message(text)

    def _export_html(self, *_args) -> None:
        from services.export_service import default_export_path

        target = default_export_path(self.title, "html")
        try:
            path = self.chart.export_html(target)
        except ValueError as exc:
            QMessageBox.information(self, "Nothing to export", str(exc))
            return
        QMessageBox.information(self, "Chart exported", f"Saved to:\n{path}")

    def _export_csv(self, *_args) -> None:
        from services.export_service import default_export_path

        target = default_export_path(self.title, "csv")
        try:
            path = self.chart.export_csv(target)
        except ValueError as exc:
            QMessageBox.information(self, "Nothing to export", str(exc))
            return
        QMessageBox.information(self, "Chart exported", f"Saved to:\n{path}")


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:  # NaN
            return None
        return number
    except (TypeError, ValueError):
        return None


__all__ = [
    "PlotlyView", "NativeChart", "ChartCard",
    "webengine_available", "ensure_plotly_bundle", "ASSETS_DIR",
]
