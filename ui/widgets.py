"""Reusable widgets: cards, KPI tiles, pandas-backed tables and filter bars."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, List, Optional, Sequence

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QDateEdit, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableView, QVBoxLayout, QWidget

from .theme import palette, scheme_version, signal

from services.formatting import round_display
from services.palette import GROUP_LABELS, default_palette


# --------------------------------------------------------------------------
# Basic building blocks
# --------------------------------------------------------------------------


class Card(QFrame):
    """A panel with a subtle border; the standard container for content."""

    def __init__(self, parent: Optional[QWidget] = None, padding: int = 12):
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(8)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)


class PageHeader(QWidget):
    """Title + subtitle + right-hand actions."""

    def __init__(self, title: str, subtitle: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        left.addWidget(self.title)
        if subtitle:
            left.addWidget(self.subtitle)
        layout.addLayout(left, 1)
        self.actions = QHBoxLayout()
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


class KpiCard(Card):
    """A single headline number with a caption and an optional status colour."""

    def __init__(self, title: str, theme: str = "dark", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumWidth(160)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("CardTitle")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("CardValue")
        # The 22px stylesheet font is taller than the label's default hint; keep
        # room for it so a squeezed grid row cannot clip the headline number.
        self.value_label.setMinimumHeight(30)
        self.setMinimumHeight(108)
        self.caption_label = QLabel("")
        self.caption_label.setObjectName("Muted")
        self.body().addWidget(self.title_label)
        self.body().addWidget(self.value_label)
        self.body().addWidget(self.caption_label)

    def set_value(self, text: str, caption: str = "", status: str = "neutral") -> None:
        c = palette(self.theme)
        self.value_label.setText(text)
        self.caption_label.setText(caption)
        colour = {
            "good": c["good"], "warning": c["warning"],
            "danger": c["danger"], "neutral": c["text"],
        }.get(status)
        if colour is not None:
            self.value_label.setStyleSheet(f"color: {colour};")
            return
        # A signal name ("d_ind:Low") or a bare classification label paints the
        # value as a workbook cell.
        group, _, label = str(status).partition(":")
        entry = signal(group, label) if label else None
        if entry is None:
            for candidate in ("d_ind", "dv2", "dv1", "range"):
                entry = signal(candidate, str(status))
                if entry is not None:
                    break
        if entry is not None:
            self.value_label.setStyleSheet(
                f"color: {entry['text']}; background: {entry['fill']};"
                "border-radius: 5px; padding: 0 6px;")
        else:
            self.value_label.setStyleSheet(f"color: {c['text']};")


class Badge(QLabel):
    """A small coloured pill used for classifications.

    The colour comes from the workbook scheme (``group`` + label); the legacy
    semantic palette is used only when no scheme is active.
    """

    COLORS = {
        "GOLD": "warning", "SILVER": "neutral", "BRONZE": "#B87333",
        "V.High": "danger", "High": "warning", "Above": "good", "Low": "neutral",
        "JACKPOT": "danger", "Spl": "danger", "Trpl": "warning", "Dbl": "accent",
        "J": "good", "--": "neutral", "^": "accent",
        "80% Plus": "good", "60% Plus": "accent", "50% Plus": "neutral",
        "40% Plus": "warning", "Low Del": "danger",
    }

    #: Where a bare label belongs when the caller does not pass ``group``.
    GROUPS = {
        "V.High": "d_ind", "High": "d_ind", "Above": "d_ind", "Low": "d_ind",
        "GOLD": "dv1", "SILVER": "dv1", "BRONZE": "dv1",
        "80% Plus": "dv2", "60% Plus": "dv2", "50% Plus": "dv2",
        "40% Plus": "dv2", "Low Del": "dv2",
        "Below": "range",
        "J": "v1", "Spl": "v1", "Trpl": "v1", "Dbl": "v1", "--": "v1", "^": "v1",
    }

    def __init__(self, text: str = "", theme: str = "dark", group: Optional[str] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.theme = theme
        self.group = group
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.PlainText)
        self.set_class(text, group)

    def set_class(self, text: str, group: Optional[str] = None) -> None:
        self.setText(text or "")
        if group is not None:
            self.group = group
        entry = None
        if text:
            entry = signal(self.group or self.GROUPS.get(text, "d_ind"), text)
        if entry is not None:
            fill, ink = entry["fill"], entry["text"]
            self.setStyleSheet(
                f"background: {fill}; color: {ink}; border: 1px solid {fill};"
                "border-radius: 7px; padding: 1px 7px; font-weight: 600;"
                "font-size: 10px;"
            )
        else:
            self._classic(text)
        self.setVisible(bool(text))

    def _classic(self, text: str) -> None:
        c = palette(self.theme)
        colour = self.COLORS.get(text, "neutral")
        key = colour if colour in c else None
        resolved = c.get(colour, colour) if key is None else c[key]
        if colour == "accent":
            resolved = c["accent"]
        self.setStyleSheet(
            f"color: {resolved}; border: 1px solid {resolved}; border-radius: 7px;"
            f"padding: 1px 7px; font-weight: 600; font-size: 10px;"
        )

    def refresh(self) -> None:
        self.set_class(self.text(), self.group)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class DataFrameModel(QAbstractTableModel):
    """A Qt table model over a pandas DataFrame (read-only, formatted)."""

    #: Columns whose values carry a workbook classification colour.  Keys are
    #: normalised column names, so both ``d_ind`` and the display label
    #: ``D Ind.`` resolve to the same group.
    SIGNAL_GROUPS = {
        "d ind": "d_ind", "dind": "d_ind", "trade indicator": "d_ind",
        "dv1": "dv1", "dv2": "dv2", "v1": "v1",
        "vol badge": "dv1", "badge gold": "dv1", "badge silver": "dv1",
        "badge bronze": "dv1",
        "vol level": "range",
    }

    @staticmethod
    def _normalise_column(name: Any) -> str:
        # Report previews mark calculated columns with a trailing " *".
        text = str(name).strip().lower().replace("_", " ")
        text = text.replace(".", "").replace("*", "")
        return " ".join(text.split())

    def __init__(self, frame: Optional[pd.DataFrame] = None, theme: str = "dark",
                 float_format: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._frame = frame if frame is not None else pd.DataFrame()
        self.theme = theme
        self.float_format = float_format
        self._column_groups: Dict[int, str] = {}
        self._signal_cache: Dict[tuple, Optional[tuple]] = {}
        self._signal_version = -1
        self._index_groups()

    def _index_groups(self) -> None:
        self._column_groups = {}
        for index, name in enumerate(self._frame.columns):
            group = self.SIGNAL_GROUPS.get(self._normalise_column(name))
            if group:
                self._column_groups[index] = group

    def _signal_colour(self, column: int, label: str, background: bool):
        group = self._column_groups.get(column)
        if not group:
            return None
        version = scheme_version()
        if version != self._signal_version:
            self._signal_cache.clear()
            self._signal_version = version
        key = (group, label, bool(background))
        if key in self._signal_cache:
            return self._signal_cache[key]
        entry = signal(group, label)
        colour = None
        if entry is not None:
            colour = QColor(entry["fill"] if background else entry["text"])
        self._signal_cache[key] = colour
        return colour

    # -- Qt plumbing ------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame.columns)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if section < len(self._frame.columns):
                return str(self._frame.columns[section])
            return None
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self._frame.iat[index.row(), index.column()]
        if role == Qt.UserRole:
            return value
        if role == Qt.TextAlignmentRole:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role in (Qt.ForegroundRole, Qt.BackgroundRole):
            if isinstance(value, str):
                return self._signal_colour(
                    index.column(), value, role == Qt.BackgroundRole)
            return None
        if role == Qt.FontRole and index.row() == 0 and False:  # pragma: no cover
            return QFont()
        if role != Qt.DisplayRole:
            return None
        return self.format(value)

    def format(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            if value != value:
                return ""
            if self.float_format is None:
                return round_display(value, 2)
            return self.float_format.format(value)
        if isinstance(value, _dt.datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        if isinstance(value, _dt.date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    # -- API --------------------------------------------------------------

    def set_frame(self, frame: Optional[pd.DataFrame]) -> None:
        self.beginResetModel()
        self._frame = frame.copy() if frame is not None else pd.DataFrame()
        self._index_groups()
        self._signal_cache.clear()
        self.endResetModel()

    def frame(self) -> pd.DataFrame:
        return self._frame

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:  # noqa: N802
        if self._frame.empty or column >= len(self._frame.columns):
            return
        name = self._frame.columns[column]
        self.beginResetModel()
        self._frame = self._frame.sort_values(
            name, ascending=(order == Qt.AscendingOrder), kind="mergesort"
        ).reset_index(drop=True)
        self.endResetModel()

    def value_at(self, row: int, column: int) -> Any:
        return self._frame.iat[row, column]

    def invalidate_colours(self) -> None:
        """Drop the resolved scheme cache after a colour change."""
        self._signal_version = -1
        self._signal_cache.clear()

    def export_frame(self) -> pd.DataFrame:
        return self._frame.copy()


class DataTable(QWidget):
    """A ``QTableView`` bundled with its model and simple paging controls."""

    rowActivated = Signal(int)

    def __init__(self, theme: str = "dark", page_size: int = 200,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme
        self.page_size = max(1, page_size)
        self._page = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.model = DataFrameModel(theme=theme, parent=self)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setDefaultSectionSize(22)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.horizontalHeader().setSectionsMovable(True)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.doubleClicked.connect(
            lambda index: self.rowActivated.emit(index.row()))
        layout.addWidget(self.view, 1)

        self.footer = QWidget(self)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.count_label = QLabel("0 rows")
        self.count_label.setObjectName("Muted")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        self.prev_button = QPushButton("‹ Prev")
        self.next_button = QPushButton("Next ›")
        self.page_label = QLabel("1 / 1")
        for widget in (self.prev_button, self.page_label, self.next_button):
            footer.addWidget(widget)
        self.prev_button.clicked.connect(lambda: self.set_page(self._page - 1))
        self.next_button.clicked.connect(lambda: self.set_page(self._page + 1))
        layout.addWidget(self.footer)

        self._full = pd.DataFrame()
        self._workbook_header = False

    # -- API --------------------------------------------------------------

    def set_frame(self, frame: Optional[pd.DataFrame], page_size: Optional[int] = None) -> None:
        self._full = frame if frame is not None else pd.DataFrame()
        if page_size:
            self.page_size = max(1, int(page_size))
        self._page = 0
        self._apply_page()

    def frame(self) -> pd.DataFrame:
        return self._full

    def selected_frame(self) -> pd.DataFrame:
        rows = sorted({index.row() for index in self.view.selectionModel().selectedRows()})
        visible = self.model.frame()
        if not rows:
            return visible.iloc[0:0]
        return visible.iloc[rows]

    def set_page(self, page: int) -> None:
        pages = max(1, (len(self._full) + self.page_size - 1) // self.page_size)
        self._page = max(0, min(pages - 1, page))
        self._apply_page()

    def _apply_page(self) -> None:
        total = len(self._full)
        pages = max(1, (total + self.page_size - 1) // self.page_size)
        start = self._page * self.page_size
        pageframe = self._full.iloc[start:start + self.page_size].reset_index(drop=True)
        self.model.set_frame(pageframe)
        self.count_label.setText(
            f"{total:,} rows" if total <= self.page_size
            else f"{start + 1:,}–{min(start + self.page_size, total):,} of {total:,} rows"
        )
        self.page_label.setText(f"{self._page + 1} / {pages}")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < pages - 1)
        self.footer.setVisible(total > self.page_size)
        self._autosize()

    def _autosize(self) -> None:
        frame = self.model.frame()
        for column in range(len(frame.columns)):
            width = max(72, min(260, len(str(frame.columns[column])) * 9 + 26))
            self.view.setColumnWidth(column, width)
        self.view.resizeRowsToContents()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.model.theme = theme
        self.model.invalidate_colours()
        self.model.layoutChanged.emit()
        self._apply_header_style()

    def set_workbook_header(self, enabled: bool = True) -> None:
        """Paint the header with the workbook's gold band."""
        self._workbook_header = bool(enabled)
        self._apply_header_style()

    def _apply_header_style(self) -> None:
        header = self.view.horizontalHeader()
        if not self._workbook_header:
            header.setStyleSheet("")
            return
        c = palette(self.theme)
        header.setStyleSheet(
            "QHeaderView::section {"
            f" background: {c['table_header']};"
            f" color: {c['table_header_text']};"
            f" border: none; border-right: 1px solid {c['border']};"
            " padding: 5px 6px; font-weight: 600; }"
        )


# --------------------------------------------------------------------------
# Legend
# --------------------------------------------------------------------------


class ScreenerLegend(QWidget):
    """The workbook's colour key (``Main!N18:N29``).

    Chips are resolved through the same scheme as the table cells, so editing a
    colour in Settings ▸ Colours updates the legend and the data together.
    """

    DEFAULT_GROUPS = ("d_ind", "dv1", "dv2", "range")

    def __init__(self, theme: str = "dark", groups: Optional[Sequence[str]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.theme = theme
        self.groups = tuple(groups or self.DEFAULT_GROUPS)
        self._badges: List[Badge] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.rebuild()

    def rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._badges = []

        groups = (default_palette().get("groups") or {})
        for group in self.groups:
            labels = list((groups.get(group) or {}).keys())
            if not labels:
                continue
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            title = QLabel(GROUP_LABELS.get(group, group))
            title.setObjectName("Muted")
            title.setMinimumWidth(170)
            row.addWidget(title)
            for label in labels:
                badge = Badge(label, self.theme, group=group)
                entry = signal(group, label)
                badge.setToolTip(
                    f"{label}: fill {entry['fill']}, text {entry['text']}"
                    if entry else f"{label}: uncoloured")
                row.addWidget(badge)
                self._badges.append(badge)
            row.addStretch(1)
            self._layout.addWidget(holder)

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        for badge in self._badges:
            badge.theme = theme
            badge.refresh()


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


class FilterBar(Card):
    """Symbol / date-range filter bar shared by the explorer and reports."""

    changed = Signal()

    def __init__(self, theme: str = "dark", allow_symbols: bool = True,
                 allow_dates: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent, padding=10)
        self.theme = theme
        row = QHBoxLayout()
        row.setSpacing(8)

        self.symbol_edit: Optional[QLineEdit] = None
        if allow_symbols:
            row.addWidget(QLabel("Symbols"))
            self.symbol_edit = QLineEdit()
            self.symbol_edit.setPlaceholderText("comma separated, blank = all")
            self.symbol_edit.setMinimumWidth(220)
            row.addWidget(self.symbol_edit, 2)

        self.start_edit: Optional[QDateEdit] = None
        self.end_edit: Optional[QDateEdit] = None
        self.as_of_edit: Optional[QDateEdit] = None
        if allow_dates:
            row.addWidget(QLabel("From"))
            self.start_edit = QDateEdit()
            self.start_edit.setCalendarPopup(True)
            self.start_edit.setDisplayFormat("yyyy-MM-dd")
            row.addWidget(self.start_edit)
            row.addWidget(QLabel("To"))
            self.end_edit = QDateEdit()
            self.end_edit.setCalendarPopup(True)
            self.end_edit.setDisplayFormat("yyyy-MM-dd")
            row.addWidget(self.end_edit)
            row.addWidget(QLabel("As of"))
            self.as_of_edit = QDateEdit()
            self.as_of_edit.setCalendarPopup(True)
            self.as_of_edit.setDisplayFormat("yyyy-MM-dd")
            row.addWidget(self.as_of_edit)

        row.addStretch(1)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("Primary")
        self.reset_button = QPushButton("Reset")
        row.addWidget(self.apply_button)
        row.addWidget(self.reset_button)
        self.add_layout(row)

        self.apply_button.clicked.connect(self.changed.emit)
        self.reset_button.clicked.connect(self.reset)
        for widget in (self.symbol_edit, self.start_edit, self.end_edit, self.as_of_edit):
            if widget is not None:
                widget.editingFinished.connect(self.changed.emit)

    # -- data -------------------------------------------------------------

    def set_dates(self, dates: Sequence[_dt.date]) -> None:
        self._dates = sorted(dates)
        if not self._dates:
            return
        for editor in (self.start_edit, self.end_edit, self.as_of_edit):
            if editor is None:
                continue
            editor.setDateRange(self._to_qdate(self._dates[0]), self._to_qdate(self._dates[-1]))
            editor.setDate(self._to_qdate(self._dates[-1]))

    def set_default_range(self, days: int) -> None:
        dates = getattr(self, "_dates", [])
        if not dates:
            return
        end = dates[-1]
        start = end - _dt.timedelta(days=max(0, int(days)))
        if self.start_edit is not None:
            self.start_edit.setDate(self._to_qdate(max(dates[0], start)))
        if self.end_edit is not None:
            self.end_edit.setDate(self._to_qdate(end))

    def symbols(self) -> List[str]:
        if self.symbol_edit is None:
            return []
        return [s.strip().upper() for s in self.symbol_edit.text().split(",") if s.strip()]

    def set_symbols(self, symbols: Iterable[str]) -> None:
        if self.symbol_edit is not None:
            self.symbol_edit.setText(", ".join(symbols))

    def start_date(self) -> Optional[_dt.date]:
        if self.start_edit is None:
            return None
        return self.start_edit.date().toPython()

    def end_date(self) -> Optional[_dt.date]:
        if self.end_edit is None:
            return None
        return self.end_edit.date().toPython()

    def as_of(self) -> Optional[_dt.date]:
        if self.as_of_edit is None:
            return None
        return self.as_of_edit.date().toPython()

    def reset(self) -> None:
        if self.symbol_edit is not None:
            self.symbol_edit.clear()
        dates = getattr(self, "_dates", [])
        if dates:
            for editor in (self.start_edit, self.end_edit, self.as_of_edit):
                if editor is not None:
                    editor.setDate(self._to_qdate(dates[-1]))
        self.set_default_range(100000)
        self.changed.emit()

    @staticmethod
    def _to_qdate(value: _dt.date):
        from PySide6.QtCore import QDate

        return QDate(value.year, value.month, value.day)


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------


class FormRow(QWidget):
    """A label + widget row used across the settings and formula dialogs."""

    def __init__(self, label: str, widget: QWidget, hint: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        caption = QLabel(label)
        caption.setObjectName("Muted")
        layout.addWidget(caption)
        layout.addWidget(widget)
        if hint:
            help_label = QLabel(hint)
            help_label.setObjectName("Muted")
            help_label.setWordWrap(True)
            layout.addWidget(help_label)


def horizontal(*widgets: QWidget, spacing: int = 6, stretch: bool = False) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch(1)
    return holder


def checkbox(label: str, checked: bool = False) -> QCheckBox:
    box = QCheckBox(label)
    box.setChecked(checked)
    return box


__all__ = [
    "Card", "PageHeader", "KpiCard", "Badge", "DataFrameModel", "DataTable",
    "ScreenerLegend", "FilterBar", "FormRow", "horizontal", "checkbox",
]
