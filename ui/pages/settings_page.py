"""Settings page — application preferences, database maintenance and the
dashboard builder."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from services.palette import (
    CHROME_LABELS, GROUP_LABELS, auto_text, default_palette, is_hex, normalise_hex,
)
from services.settings_service import EDITABLE_SECTIONS, VALID_DUPLICATE_POLICIES, VALID_EXPORT_FORMATS, VALID_FORMULA_MODES, VALID_THEMES, load_defaults

from ui.dialogs import ComponentEditorDialog
from ui.widgets import Badge, Card, DataTable, ScreenerLegend

from .base import BasePage

#: editable settings and how to render each one.
#: dotted key -> (kind, extra) where extra is choices / (min, max, step) / hint
FIELD_KINDS: Dict[str, Any] = {
    "database.path": ("text", "Take effect after a restart"),
    "database.backup_dir": ("text", "Where backup copies are written"),

    "import.duplicate_policy": ("combo", VALID_DUPLICATE_POLICIES),
    "import.sheet_priority": ("list", "Comma separated sheet names, tried in order"),
    "import.auto_map_columns": ("bool", None),
    "import.store_extra_columns": ("bool", None),
    "import.max_error_samples": ("int", (0, 10000, 5)),

    "display.theme": ("combo", VALID_THEMES),
    "display.date_format": ("text", "e.g. %Y-%m-%d"),
    "display.number_format": ("text", "e.g. #,##0.00"),
    "display.percent_format": ("text", "e.g. 0.00"),
    "display.grid_lines": ("bool", None),

    "filters.default_date_range_days": ("int", (0, 3650, 1)),
    "filters.page_size": ("int", (10, 5000, 10)),

    "analytics.duckdb": ("bool", None),
    "analytics.duckdb_path": ("text", "Blank = next to the database file"),
    "analytics.large_dataset_rows": ("int", (0, 100_000_000, 50_000)),

    "formulas.mode": ("combo", VALID_FORMULA_MODES),
    "formulas.moving_average_days": ("int", (2, 250, 1)),
    "formulas.delivery_baseline_days": ("int", (2, 250, 1)),

    "dashboard.default_landing_page": ("text", "dashboard | explorer | history | reports"),
    "dashboard.refresh_on_import": ("bool", None),

    "logging.level": ("combo", ("DEBUG", "INFO", "WARNING", "ERROR")),
    "logging.file": ("text", "Relative to the project folder"),
    "logging.max_bytes": ("int", (65536, 100_000_000, 65536)),
    "logging.backup_count": ("int", (0, 50, 1)),

    "export.directory": ("text", "Default folder for exports"),
    "export.default_format": ("combo", VALID_EXPORT_FORMATS),
}

MODE_HELP = {
    "original": ("Reproduces the workbook exactly, including its known defects. "
                 "Use this to match the Excel file."),
    "corrected": ("Applies the documented corrections: the delivery/volume averages use "
                  "the full window, the V1 fallback codes are reachable and the "
                  "GOLD/SILVER thresholds follow the legend."),
}


class ColourRow(QWidget):
    """One editable palette entry: fill swatch + hex, optional text colour."""

    changed = Signal()

    def __init__(self, label: str, entry: Optional[Dict[str, Any]] = None,
                 default: Optional[Dict[str, Any]] = None, with_text: bool = True,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        entry = entry or {}
        self.default = default
        self.with_text = with_text
        self._fill = entry.get("fill")
        self._text = entry.get("text")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        name = QLabel(label)
        name.setMinimumWidth(130)
        row.addWidget(name)

        self.fill_button = QPushButton()
        self.fill_button.setFixedSize(44, 22)
        self.fill_button.setToolTip("Pick the fill colour")
        self.fill_button.clicked.connect(lambda: self._pick("fill"))
        row.addWidget(self.fill_button)

        self.fill_edit = QLineEdit(self._fill or "")
        self.fill_edit.setFixedWidth(96)
        self.fill_edit.setPlaceholderText("#RRGGBB")
        self.fill_edit.editingFinished.connect(self._on_typed)
        row.addWidget(self.fill_edit)

        self.auto_check: Optional[QCheckBox] = None
        self.text_button: Optional[QPushButton] = None
        self.text_edit: Optional[QLineEdit] = None
        if with_text:
            self.auto_check = QCheckBox("Auto text")
            self.auto_check.setToolTip(
                "Pick black or white automatically so the label stays readable")
            self.auto_check.setChecked(self._text is None)
            self.auto_check.toggled.connect(self._on_auto)
            row.addWidget(self.auto_check)

            self.text_button = QPushButton()
            self.text_button.setFixedSize(44, 22)
            self.text_button.setToolTip("Pick the text colour")
            self.text_button.clicked.connect(lambda: self._pick("text"))
            row.addWidget(self.text_button)

            self.text_edit = QLineEdit(self._text or "")
            self.text_edit.setFixedWidth(96)
            self.text_edit.setPlaceholderText("auto")
            self.text_edit.editingFinished.connect(self._on_typed)
            row.addWidget(self.text_edit)

        self.reset_button = QPushButton("↺")
        self.reset_button.setFixedWidth(30)
        self.reset_button.setToolTip("Reset to the workbook colour")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        row.addStretch(1)

        self._sync()

    # -- editing ---------------------------------------------------------

    def _pick(self, which: str) -> None:
        current = self._fill if which == "fill" else (
            self._text or (auto_text(self._fill) if self._fill else "#FFFFFF"))
        colour = QColorDialog.getColor(QColor(current or "#FFFFFF"), self,
                                       "Pick a colour")
        if not colour.isValid():
            return
        value = colour.name().upper()
        if which == "fill":
            self._fill = value
            self.fill_edit.setText(value)
        else:
            self._text = value
            if self.text_edit is not None:
                self.text_edit.setText(value)
            if self.auto_check is not None:
                self.auto_check.blockSignals(True)
                self.auto_check.setChecked(False)
                self.auto_check.blockSignals(False)
        self._sync()
        self.changed.emit()

    def _on_auto(self, checked: bool) -> None:
        if checked:
            self._text = None
            if self.text_edit is not None:
                self.text_edit.clear()
        elif not self._text and self._fill:
            self._text = auto_text(self._fill)
            if self.text_edit is not None:
                self.text_edit.setText(self._text)
        self._sync()
        self.changed.emit()

    def _on_typed(self) -> None:
        ok = True
        fill_text = self.fill_edit.text().strip()
        if fill_text and not is_hex(fill_text):
            self.fill_edit.setStyleSheet("border: 1px solid #F85149;")
            ok = False
        else:
            self.fill_edit.setStyleSheet("")
            self._fill = normalise_hex(fill_text) if fill_text else None
        if self.text_edit is not None and self.auto_check is not None \
                and not self.auto_check.isChecked():
            text_text = self.text_edit.text().strip()
            if text_text and not is_hex(text_text):
                self.text_edit.setStyleSheet("border: 1px solid #F85149;")
                ok = False
            else:
                self.text_edit.setStyleSheet("")
                self._text = normalise_hex(text_text) if text_text else None
        if ok:
            self._sync()
            self.changed.emit()

    # -- state -----------------------------------------------------------

    def _sync(self) -> None:
        fill = self._fill
        resolved_text = self._text
        if fill and resolved_text is None:
            resolved_text = auto_text(fill)
        if fill:
            self.fill_button.setText("Aa")
            self.fill_button.setStyleSheet(
                f"background: {fill}; color: {resolved_text}; border: 1px solid {fill};"
                "border-radius: 4px; font-weight: 600;")
        else:
            self.fill_button.setText("—")
            self.fill_button.setStyleSheet(
                "border: 1px dashed #8B949E; border-radius: 4px;")
        if self.text_button is not None and self.auto_check is not None:
            auto = self.auto_check.isChecked()
            self.text_button.setEnabled(not auto)
            if self.text_edit is not None:
                self.text_edit.setEnabled(not auto)
            if auto or not self._text:
                self.text_button.setText("auto" if auto else "—")
                self.text_button.setStyleSheet(
                    "border: 1px dashed #8B949E; border-radius: 4px;")
            else:
                self.text_button.setText("Aa")
                self.text_button.setStyleSheet(
                    f"background: {self._text}; color: {fill or '#FFFFFF'};"
                    f" border: 1px solid {self._text}; border-radius: 4px;")

    def value(self) -> Dict[str, Optional[str]]:
        auto = self.auto_check is None or self.auto_check.isChecked()
        text = None
        if not auto and self._text:
            text = normalise_hex(self._text)
        fill = normalise_hex(self._fill) if self._fill else None
        return {"fill": fill, "text": text}

    def set_entry(self, entry: Optional[Dict[str, Any]],
                  default: Optional[Dict[str, Any]] = None) -> None:
        entry = entry or {}
        self.default = default
        self._fill = entry.get("fill")
        self._text = entry.get("text")
        self.fill_edit.setText(self._fill or "")
        if self.auto_check is not None:
            self.auto_check.blockSignals(True)
            self.auto_check.setChecked(self._text is None)
            self.auto_check.blockSignals(False)
        if self.text_edit is not None:
            self.text_edit.setText(self._text or "")
        self._sync()

    def reset(self) -> None:
        self.set_entry(self.default or {})
        self.changed.emit()


class SettingsPage(BasePage):
    nav_title = "Settings"
    nav_icon = "⚙"

    themeChanged = Signal(str)
    settingsChanged = Signal()
    dataChanged = Signal()
    coloursChanged = Signal()

    def page_subtitle(self) -> str:
        return ("Preferences are stored in the database and survive restarts. "
                "Destructive actions always ask for confirmation first.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.save_button = QPushButton("Save changes")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save_current_tab)
        self.reset_button = QPushButton("Reset this section")
        self.reset_button.clicked.connect(self._reset_current_tab)
        self.header.add_action(self.reset_button)
        self.header.add_action(self.save_button)

        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)

        self._fields: Dict[str, Dict[str, QWidget]] = {}

        self._build_settings_tab("General", "display")
        self._build_settings_tab("Import", "import")
        self._build_settings_tab("Formulas", "formulas")
        self._build_settings_tab("Filters", "filters")
        self._build_settings_tab("Analytics", "analytics")
        self._build_colours_tab()
        self._build_settings_tab("Export", "export")
        self._build_settings_tab("Logging", "logging")
        self._build_dashboard_tab()
        self._build_database_tab()

    # ------------------------------------------------------------------
    # generic settings tabs
    # ------------------------------------------------------------------

    def _build_settings_tab(self, title: str, section: str) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)

        card = Card(padding=12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        widgets: Dict[str, QWidget] = {}
        for dotted, label in EDITABLE_SECTIONS.get(section, {}).items():
            spec = FIELD_KINDS.get(dotted)
            if spec is None:
                continue
            widget, row = self._make_widget(dotted, spec)
            widgets[dotted] = widget
            form.addRow(label, row)
        card.add_layout(form)

        if section == "formulas":
            note = QLabel("Formula mode controls every calculated metric.\n\n" + "\n\n".join(
                f"• {name}: {text}" for name, text in MODE_HELP.items()))
            note.setObjectName("Muted")
            note.setWordWrap(True)
            card.add(note)

        outer.addWidget(card)
        outer.addStretch(1)
        self._fields[section] = widgets
        self.tabs.addTab(page, title)

    def _make_widget(self, dotted: str, spec) -> tuple:
        kind, extra = spec
        current = self.ctx.settings.get(dotted)
        hint = extra if isinstance(extra, str) else None

        if kind == "combo":
            widget = QComboBox()
            for choice in extra:
                widget.addItem(str(choice).upper() if isinstance(choice, str) and choice.islower()
                               and dotted.endswith("theme") else str(choice), choice)
            index = widget.findData(current)
            if index >= 0:
                widget.setCurrentIndex(index)
            if dotted == "formulas.mode":
                widget.currentIndexChanged.connect(self._on_mode_changed)
        elif kind == "bool":
            widget = QCheckBox("Enabled")
            widget.setChecked(bool(current))
        elif kind == "int":
            low, high, step = extra
            widget = QSpinBox()
            widget.setRange(int(low), int(high))
            widget.setSingleStep(int(step))
            widget.setValue(int(current if current is not None else 0))
        elif kind == "float":
            low, high, step = extra
            widget = QDoubleSpinBox()
            widget.setRange(float(low), float(high))
            widget.setSingleStep(float(step))
            widget.setValue(float(current or 0))
        elif kind == "list":
            widget = QLineEdit(", ".join(current) if isinstance(current, list) else str(current))
        else:
            widget = QLineEdit("" if current is None else str(current))

        if hint is None:
            return widget, widget
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(widget)
        caption = QLabel(hint)
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        return widget, holder

    def _read_widget(self, widget: QWidget) -> Any:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return None

    def current_section(self) -> Optional[str]:
        index = self.tabs.currentIndex()
        names = list(self._fields.keys())
        if index < len(names):
            return names[index]
        return None

    def _save_current_tab(self) -> None:
        section = self.current_section()
        if section == "colors":
            self._save_colours()
            return
        if section is None:
            self.warn("Nothing to save here",
                      "This tab has its own actions (dashboard builder / database).")
            return
        widgets = self._fields.get(section, {})
        values: Dict[str, Any] = {}
        for dotted, widget in widgets.items():
            if dotted == "import.sheet_priority":
                values[dotted] = [s.strip() for s in
                                  self._read_widget(widget).split(",") if s.strip()]
            else:
                values[dotted] = self._read_widget(widget)
        try:
            self.ctx.settings.update(values)
        except Exception as exc:  # noqa: BLE001
            self.warn("Could not save settings", f"{type(exc).__name__}: {exc}")
            return
        self.ctx.reload_settings()
        self.settingsChanged.emit()
        self.dataChanged.emit()

        if "display.theme" in values:
            self.themeChanged.emit(str(values["display.theme"]))
            self.theme_selector_sync(str(values["display.theme"]))
        self.notify(f"Saved the {section} settings")
        self.info("Settings saved", "Your changes were written to the database.")

    def _reset_current_tab(self) -> None:
        section = self.current_section()
        if section == "colors":
            self._restore_colours()
            return
        if section is None:
            return
        if not self.confirm("Reset section",
                            f"Restore the default {section} settings?", danger=True):
            return
        self.ctx.settings.reset(section)
        self.ctx.reload_settings()
        defaults = load_defaults().get(section, {})
        for dotted, widget in self._fields.get(section, {}).items():
            value = defaults.get(dotted.split(".", 1)[1])
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value or 0))
            elif isinstance(widget, QLineEdit):
                widget.setText(", ".join(value) if isinstance(value, list)
                               else ("" if value is None else str(value)))
        self.settingsChanged.emit()
        self.dataChanged.emit()
        self.notify(f"Reset the {section} settings")

    def _on_mode_changed(self) -> None:
        mode = self._read_widget(self._fields["formulas"]["formulas.mode"])
        self.notify(f"Formula mode set to {mode} — remember to save")

    def theme_selector_sync(self, theme: str) -> None:
        widget = self._fields.get("display", {}).get("display.theme")
        if isinstance(widget, QComboBox):
            index = widget.findData(theme)
            if index >= 0:
                widget.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # colours
    # ------------------------------------------------------------------

    def _build_colours_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        options = Card(padding=12)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Scheme"))
        self.colour_scheme_combo = QComboBox()
        self.colour_scheme_combo.addItem("Workbook colours", "workbook")
        self.colour_scheme_combo.addItem("Classic (theme colours)", "classic")
        row.addWidget(self.colour_scheme_combo)

        self.auto_text_check = QCheckBox("Readable text (auto contrast)")
        self.auto_text_check.setToolTip(
            "Keeps the workbook fills and picks black or white text automatically.\n"
            "Turn off to reproduce the workbook's white text exactly.")
        row.addWidget(self.auto_text_check)

        self.legend_check = QCheckBox("Show the legend above the screener")
        row.addWidget(self.legend_check)
        row.addStretch(1)

        self.restore_colours_button = QPushButton("Restore workbook colours")
        self.restore_colours_button.clicked.connect(lambda: self._restore_colours())
        self.export_colours_button = QPushButton("Export…")
        self.export_colours_button.clicked.connect(self._export_colours)
        self.import_colours_button = QPushButton("Import…")
        self.import_colours_button.clicked.connect(self._import_colours)
        for button in (self.restore_colours_button, self.export_colours_button,
                       self.import_colours_button):
            row.addWidget(button)
        options.add_layout(row)

        hint = QLabel(
            "Defaults come from the sample workbook (Main!N18:N29 and the "
            "conditional-formatting rules behind it). Only differences from "
            "those defaults are stored.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        options.add(hint)
        outer.addWidget(options)

        preview_card = Card(padding=10)
        title = QLabel("Preview")
        title.setObjectName("SectionTitle")
        preview_card.add(title)
        self.colour_preview = QWidget()
        preview_layout = QVBoxLayout(self.colour_preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(2)
        self._preview_badges = {}
        defaults = default_palette()
        for group in ("d_ind", "dv1", "dv2", "range"):
            labels = list(((defaults.get("groups") or {}).get(group) or {}).keys())
            if not labels:
                continue
            preview_row = QHBoxLayout()
            preview_row.setSpacing(5)
            caption = QLabel(GROUP_LABELS.get(group, group))
            caption.setObjectName("Muted")
            caption.setMinimumWidth(170)
            preview_row.addWidget(caption)
            for label in labels:
                badge = Badge(label, self.ctx.theme, group=group)
                badge.setVisible(False)
                preview_row.addWidget(badge)
                self._preview_badges[(group, label)] = badge
            preview_row.addStretch(1)
            preview_layout.addLayout(preview_row)
        preview_card.add(self.colour_preview)
        outer.addWidget(preview_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(8)

        self._colour_rows: Dict[tuple, ColourRow] = {}
        self._chrome_rows: Dict[str, ColourRow] = {}
        self._series_rows: list = []
        entries = self.ctx.palette.entries()

        for group in ("d_ind", "dv1", "dv2", "range", "v1"):
            labels = (defaults.get("groups") or {}).get(group) or {}
            card = Card(padding=10)
            heading = QLabel(GROUP_LABELS.get(group, group))
            heading.setObjectName("SectionTitle")
            card.add(heading)
            if group == "v1":
                note = QLabel("The workbook leaves this column uncoloured. "
                              "Pick a colour if you want the badge visible.")
                note.setObjectName("Muted")
                note.setWordWrap(True)
                card.add(note)
            for label, default_entry in labels.items():
                entry = ((entries.get("groups") or {}).get(group) or {}).get(label)
                widget = ColourRow(label, entry, default=default_entry)
                widget.changed.connect(self._sync_preview)
                self._colour_rows[(group, label)] = widget
                card.add(widget)
            holder_layout.addWidget(card)

        chrome_card = Card(padding=10)
        chrome_title = QLabel("Headers and chrome")
        chrome_title.setObjectName("SectionTitle")
        chrome_card.add(chrome_title)
        for key, label in CHROME_LABELS.items():
            value = (entries.get("chrome") or {}).get(key)
            widget = ColourRow(label, {"fill": value},
                               default={"fill": (defaults.get("chrome") or {}).get(key)},
                               with_text=False)
            self._chrome_rows[key] = widget
            chrome_card.add(widget)
        holder_layout.addWidget(chrome_card)

        series_card = Card(padding=10)
        series_title = QLabel("Chart colourway")
        series_title.setObjectName("SectionTitle")
        series_card.add(series_title)
        series = list(entries.get("series") or defaults.get("series") or [])
        for index in range(len(defaults.get("series") or [])):
            value = series[index] if index < len(series) else None
            widget = ColourRow(f"Series {index + 1}", {"fill": value}, with_text=False)
            self._series_rows.append(widget)
            series_card.add(widget)
        holder_layout.addWidget(series_card)

        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self.tabs.addTab(page, "Colours")
        self._fields["colors"] = {}
        self._reload_colour_editors()

    def _reload_colour_editors(self) -> None:
        entries = self.ctx.palette.entries()
        defaults = default_palette()
        for (group, label), widget in self._colour_rows.items():
            entry = ((entries.get("groups") or {}).get(group) or {}).get(label)
            default = ((defaults.get("groups") or {}).get(group) or {}).get(label)
            widget.set_entry(entry, default=default)
        for key, widget in self._chrome_rows.items():
            widget.set_entry(
                {"fill": (entries.get("chrome") or {}).get(key)},
                default={"fill": (defaults.get("chrome") or {}).get(key)})
        series = list(entries.get("series") or defaults.get("series") or [])
        for index, widget in enumerate(self._series_rows):
            widget.set_entry({"fill": series[index] if index < len(series) else None})

        index = self.colour_scheme_combo.findData(self.ctx.palette.name())
        if index >= 0:
            self.colour_scheme_combo.setCurrentIndex(index)
        self.auto_text_check.setChecked(self.ctx.palette.text_mode() == "auto")
        self.legend_check.setChecked(self.ctx.palette.show_legend())
        self._sync_preview()

    def _sync_preview(self) -> None:
        for key, badge in self._preview_badges.items():
            widget = self._colour_rows.get(key)
            if widget is None:
                continue
            value = widget.value()
            fill = value.get("fill")
            if not fill:
                badge.setVisible(False)
                continue
            ink = value.get("text") or auto_text(fill)
            badge.setText(key[1])
            badge.setVisible(True)
            badge.setStyleSheet(
                f"background: {fill}; color: {ink}; border: 1px solid {fill};"
                "border-radius: 7px; padding: 1px 7px; font-weight: 600;"
                "font-size: 10px;")

    def _collect_colours(self) -> tuple:
        groups: Dict[str, Dict[str, Any]] = {}
        for (group, label), widget in self._colour_rows.items():
            groups.setdefault(group, {})[label] = widget.value()
        chrome = {key: widget.value().get("fill")
                  for key, widget in self._chrome_rows.items()}
        series = [widget.value().get("fill") for widget in self._series_rows]
        return groups, chrome, series

    def _save_colours(self) -> None:
        groups, chrome, series = self._collect_colours()
        try:
            self.ctx.palette.save_edits(
                scheme_name=str(self.colour_scheme_combo.currentData() or "workbook"),
                text_mode="auto" if self.auto_text_check.isChecked() else "exact",
                show_legend=self.legend_check.isChecked(),
                groups=groups, chrome=chrome, series=series,
            )
        except ValueError as exc:
            self.warn("Invalid colour", str(exc))
            return
        self.ctx.palette.apply()
        self.coloursChanged.emit()
        self.notify("Saved the colour scheme")

    def _restore_colours(self, confirm: bool = True) -> None:
        if confirm and not self.confirm(
                "Restore workbook colours",
                "Discard your colour edits and restore the palette extracted from "
                "the sample workbook?", danger=True):
            return
        self.ctx.palette.reset()
        self.ctx.palette.apply()
        self._reload_colour_editors()
        self.coloursChanged.emit()
        self.notify("Restored the workbook colours")

    def _export_colours(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export colour scheme", "capital-colours.json", "JSON (*.json)")
        if not path:
            return
        try:
            self.ctx.palette.export_json(path)
        except OSError as exc:
            self.warn("Could not export", str(exc))
            return
        self.notify("Colour scheme exported")

    def _import_colours(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import colour scheme", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.ctx.palette.import_json(path)
        except (OSError, ValueError) as exc:
            self.warn("Could not import", str(exc))
            return
        self.ctx.palette.apply()
        self._reload_colour_editors()
        self.coloursChanged.emit()
        self.notify("Colour scheme imported")

    # ------------------------------------------------------------------
    # dashboard builder
    # ------------------------------------------------------------------

    def _build_dashboard_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # layout controls
        layout_card = Card(padding=12)
        row = QHBoxLayout()
        row.addWidget(QLabel("Layout"))
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumWidth(180)
        self.layout_combo.currentTextChanged.connect(self._on_layout_selected)
        row.addWidget(self.layout_combo)
        self.new_layout_button = QPushButton("New layout…")
        self.new_layout_button.clicked.connect(self._new_layout)
        self.delete_layout_button = QPushButton("Delete layout")
        self.delete_layout_button.setObjectName("Danger")
        self.delete_layout_button.clicked.connect(self._delete_layout)
        self.restore_button = QPushButton("Restore default")
        self.restore_button.clicked.connect(self._restore_default)
        self.import_layout_button = QPushButton("Import layout…")
        self.import_layout_button.clicked.connect(self._import_layout)
        self.export_layout_button = QPushButton("Export layout…")
        self.export_layout_button.clicked.connect(self._export_layout)
        row.addWidget(self.new_layout_button)
        row.addWidget(self.delete_layout_button)
        row.addWidget(self.restore_button)
        row.addWidget(self.import_layout_button)
        row.addWidget(self.export_layout_button)
        row.addStretch(1)
        row.addWidget(QLabel("Landing page"))
        self.landing_combo = QComboBox()
        self.landing_combo.addItems(["dashboard", "explorer", "history", "formula_lab",
                                     "reports", "import", "settings", "audit"])
        row.addWidget(self.landing_combo)
        apply_landing = QPushButton("Set as landing page")
        apply_landing.clicked.connect(self._set_landing)
        row.addWidget(apply_landing)
        layout_card.add_layout(row)
        outer.addWidget(layout_card)

        # component table
        card = Card(padding=10)
        self._card = card
        header = QHBoxLayout()
        title = QLabel("Components")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.add_button = QPushButton("Add component…")
        self.add_button.setObjectName("Primary")
        self.add_button.clicked.connect(self._add_component)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit_component)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("Danger")
        self.remove_button.clicked.connect(self._remove_component)
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self._move(1))
        self.visibility_button = QPushButton("Show / hide")
        self.visibility_button.clicked.connect(self._toggle_visible)
        for button in (self.add_button, self.edit_button, self.remove_button,
                       self.up_button, self.down_button, self.visibility_button):
            header.addWidget(button)
        card.add_layout(header)

        self.components_table = DataTable(theme=self.ctx.theme, page_size=50)
        card.add(self.components_table)
        outer.addWidget(card, 1)
        self.tabs.addTab(page, "Dashboard Builder")

    def _reload_dashboard(self) -> None:
        names = self.ctx.dashboard.layouts()
        active = self.ctx.dashboard.active_layout()
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        self.layout_combo.addItems(names)
        index = self.layout_combo.findText(active)
        if index >= 0:
            self.layout_combo.setCurrentIndex(index)
        self.layout_combo.blockSignals(False)

        landing = self.ctx.settings.get("dashboard.default_landing_page", "dashboard")
        index = self.landing_combo.findText(str(landing))
        if index < 0:
            index = 0
        self.landing_combo.setCurrentIndex(index)

        components = self.ctx.dashboard.list_components(active)
        rows = [{
            "position": c.position,
            "kind": c.kind,
            "title": c.title,
            "metric": c.metric,
            "group by": c.dimension or "",
            "aggregation": c.aggregation,
            "source": c.source,
            "visible": c.visible,
        } for c in components]
        self.components_table.set_frame(
            pd.DataFrame(rows) if rows else pd.DataFrame(
                columns=["position", "kind", "title", "metric", "group by",
                         "aggregation", "source", "visible"]))
        self.delete_layout_button.setEnabled(len(names) > 1)

    def _on_layout_selected(self, name: str) -> None:
        if not name:
            return
        try:
            self.ctx.dashboard.set_active_layout(name)
        except ValueError:
            return
        self._reload_dashboard()
        self.dataChanged.emit()

    def _new_layout(self) -> None:
        name, ok = _ask_text(self, "New layout", "Name for the new dashboard layout:",
                             f"Layout {len(self.ctx.dashboard.layouts()) + 1}")
        if not ok or not name.strip():
            return
        copy = _ask_yes_no(self, "Copy components?",
                           f"Start the new layout “{name}” with a copy of "
                           f"“{self.ctx.dashboard.active_layout()}”?")
        base = self.ctx.dashboard.active_layout() if copy else None
        try:
            self.ctx.dashboard.create_layout(name.strip(), copy_from=base)
            self.ctx.dashboard.set_active_layout(name.strip())
        except Exception as exc:  # noqa: BLE001
            self.warn("Could not create the layout", str(exc))
            return
        self._reload_dashboard()
        self.dataChanged.emit()
        self.notify(f"Created layout {name}")

    def _delete_layout(self) -> None:
        name = self.ctx.dashboard.active_layout()
        if not self.confirm("Delete layout",
                            f"Delete the layout “{name}” and all of its components?",
                            danger=True):
            return
        try:
            self.ctx.dashboard.delete_layout(name)
        except Exception as exc:  # noqa: BLE001
            self.warn("Could not delete the layout", str(exc))
            return
        self._reload_dashboard()
        self.dataChanged.emit()

    def _restore_default(self) -> None:
        name = self.ctx.dashboard.active_layout()
        if not self.confirm("Restore default layout",
                            f"Replace every component in “{name}” with the shipped "
                            "default set?", danger=True):
            return
        count = self.ctx.dashboard.restore_default(name)
        self._reload_dashboard()
        self.dataChanged.emit()
        self.info("Default restored", f"{count} default components were written to “{name}”.")

    def _export_layout(self) -> None:
        import json

        name = self.ctx.dashboard.active_layout()
        target, _ = QFileDialog.getSaveFileName(
            self, "Export dashboard layout", f"{name}.json", "JSON files (*.json)")
        if not target:
            return
        payload = self.ctx.dashboard.export_config(name)
        try:
            Path(target).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self.warn("Could not export the layout", str(exc))
            return
        self.notify(f"Exported “{name}” to {Path(target).name}")

    def _import_layout(self) -> None:
        import json

        target, _ = QFileDialog.getOpenFileName(
            self, "Import dashboard layout", "", "JSON files (*.json)")
        if not target:
            return
        try:
            payload = json.loads(Path(target).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.warn("Could not read the layout file", f"{Path(target).name}: {exc}")
            return

        suggested = str(payload.get("layout") or "Imported")
        name, ok = _ask_text(self, "Import layout", "Name for the imported layout:",
                             suggested)
        name = name.strip()
        if not ok or not name:
            return
        if name in self.ctx.dashboard.layouts() and not self.confirm(
                "Replace layout",
                f"A layout called “{name}” already exists. Replace all of its components?",
                danger=True):
            return
        try:
            self.ctx.dashboard.import_config(payload, layout=name)
        except ValueError as exc:
            self.warn("Could not import the layout", str(exc))
            return
        self.ctx.dashboard.set_active_layout(name)
        self.ctx.invalidate("dashboard layout imported")
        self._reload_dashboard()
        self.dataChanged.emit()
        self.notify(f"Imported “{name}”")

    def _set_landing(self) -> None:
        self.ctx.settings.set("dashboard.default_landing_page",
                              self.landing_combo.currentText())
        self.settingsChanged.emit()
        self.notify(f"Landing page set to {self.landing_combo.currentText()}")

    def _selected_component_id(self) -> Optional[int]:
        selected = self.components_table.selected_frame()
        if selected.empty:
            return None
        position = int(selected.iloc[0]["position"])
        title = selected.iloc[0]["title"]
        for component in self.ctx.dashboard.list_components():
            if component.position == position and component.title == title:
                return component.id
        return None

    def _add_component(self) -> None:
        if ComponentEditorDialog(self, self.ctx).exec():
            self._reload_dashboard()
            self.dataChanged.emit()
            self.notify("Component added")

    def _edit_component(self) -> None:
        component_id = self._selected_component_id()
        if component_id is None:
            self.warn("No component selected", "Pick a row in the component table.")
            return
        component = self.ctx.dashboard.get_component(component_id)
        if component is None:
            return
        if ComponentEditorDialog(self, self.ctx, component=component).exec():
            self._reload_dashboard()
            self.dataChanged.emit()

    def _remove_component(self) -> None:
        component_id = self._selected_component_id()
        if component_id is None:
            return
        component = self.ctx.dashboard.get_component(component_id)
        if component is None:
            return
        if not self.confirm("Remove component",
                            f"Remove “{component.title}” from the dashboard?", danger=True):
            return
        self.ctx.dashboard.remove_component(component_id)
        self._reload_dashboard()
        self.dataChanged.emit()
        self.notify("Component removed")

    def _move(self, offset: int) -> None:
        component_id = self._selected_component_id()
        if component_id is None:
            return
        self.ctx.dashboard.move(component_id, offset)
        self._reload_dashboard()
        self.dataChanged.emit()

    def _toggle_visible(self) -> None:
        component_id = self._selected_component_id()
        if component_id is None:
            return
        component = self.ctx.dashboard.get_component(component_id)
        if component is None:
            return
        self.ctx.dashboard.set_visible(component_id, not component.visible)
        self._reload_dashboard()
        self.dataChanged.emit()

    # ------------------------------------------------------------------
    # database tab
    # ------------------------------------------------------------------

    def _build_database_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        info = Card(padding=12)
        self.db_info = QLabel("")
        self.db_info.setWordWrap(True)
        info.add(self.db_info)

        row = QHBoxLayout()
        backup_button = QPushButton("Back up now…")
        backup_button.setObjectName("Primary")
        backup_button.clicked.connect(self._backup)
        restore_button = QPushButton("Restore from backup…")
        restore_button.clicked.connect(self._restore)
        vacuum_button = QPushButton("Optimise (VACUUM)")
        vacuum_button.clicked.connect(self._vacuum)
        clear_button = QPushButton("Delete all market data…")
        clear_button.setObjectName("Danger")
        clear_button.clicked.connect(self._clear_data)
        for button in (backup_button, restore_button, vacuum_button, clear_button):
            row.addWidget(button)
        row.addStretch(1)
        info.add_layout(row)
        outer.addWidget(info)

        self.backups_table = DataTable(theme=self.ctx.theme, page_size=50)
        card = Card(padding=10)
        title = QLabel("Existing backups")
        title.setObjectName("SectionTitle")
        card.add(title)
        card.add(self.backups_table)
        outer.addWidget(card, 1)
        self.tabs.addTab(page, "Database")

    def _reload_database(self) -> None:
        summary = self.ctx.data.summary()
        size = _human_size(self.ctx.db.path.stat().st_size) if self.ctx.db.path.exists() else "—"
        self.db_info.setText(
            f"Database file: {self.ctx.db.path} ({size})\n"
            f"{summary['stocks']:,} symbols · {summary['records']:,} session records · "
            f"{summary['batches']:,} import batches\n"
            f"Date range: {summary['earliest_date'] or '—'} → {summary['latest_date'] or '—'}"
        )
        backups = sorted(self.ctx.settings.backup_dir().glob("*.db"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        rows = [{
            "file": path.name,
            "size": _human_size(path.stat().st_size),
            "modified": _dt.datetime.fromtimestamp(path.stat().st_mtime),
        } for path in backups[:100]]
        self.backups_table.set_frame(pd.DataFrame(rows) if rows
                                     else pd.DataFrame(columns=["file", "size", "modified"]))

    def _backup(self) -> None:
        default = self.ctx.settings.backup_dir() / (
            f"capital-{_dt.datetime.now():%Y%m%d-%H%M%S}.db")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save backup as", str(default), "SQLite database (*.db)")
        if not selected:
            return
        try:
            path = self.ctx.db.backup(selected)
        except Exception as exc:  # noqa: BLE001
            self.warn("Backup failed", f"{type(exc).__name__}: {exc}")
            return
        self.ctx.db.log("maintenance", f"Backup written to {path.name}")
        self._reload_database()
        self.info("Backup complete", f"Saved to:\n{path}")

    def _restore(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose a backup to restore", str(self.ctx.settings.backup_dir()),
            "SQLite database (*.db)")
        if not selected:
            return
        if not self.confirm(
                "Restore database",
                f"Every current row will be replaced by the contents of:\n"
                f"{Path(selected).name}\n\nA safety copy of the current database is taken "
                "first. Continue?", danger=True):
            return
        try:
            safety = self.ctx.db.restore(selected)
        except Exception as exc:  # noqa: BLE001
            self.warn("Restore failed", f"{type(exc).__name__}: {exc}")
            return
        self.ctx.db.init_schema()
        self.ctx.invalidate("database restored")
        self._reload_all()
        self.info("Restore complete",
                  f"The database was replaced.\nSafety copy: {safety}")

    def _vacuum(self) -> None:
        try:
            self.ctx.data.vacuum()
        except Exception as exc:  # noqa: BLE001
            self.warn("Optimise failed", f"{type(exc).__name__}: {exc}")
            return
        self.ctx.db.log("maintenance", "VACUUM completed")
        self._reload_database()
        self.notify("Database optimised")

    def _clear_data(self) -> None:
        if not self.confirm(
                "Delete all market data",
                "This deletes every imported symbol, session record and archived value.\n"
                "Formulas, settings, dashboard layouts and the audit log are kept.\n\n"
                "Take a backup first — this cannot be undone.", danger=True):
            return
        result = self.ctx.data.clear_prices()
        self.ctx.db.log("maintenance",
                        f"Deleted {result['prices_removed']} price rows", severity="warning")
        self._reload_all()
        self.dataChanged.emit()
        self.info("Market data deleted",
                  f"{result['prices_removed']:,} session records were removed.")

    # ------------------------------------------------------------------

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._reload_dashboard()
        self._reload_database()

    def show_dashboard_builder(self) -> None:
        """Select the Dashboard Builder tab (used by “Manage layout…”)."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == "Dashboard Builder":
                self.tabs.setCurrentIndex(index)
                break
        self._reload_dashboard()

    def _reload_all(self) -> None:
        self.ctx.invalidate("settings page reload")
        self._reload_dashboard()
        self._reload_database()

    def on_theme_changed(self, theme: str) -> None:
        self.components_table.set_theme(theme)
        self.backups_table.set_theme(theme)
        for badge in getattr(self, "_preview_badges", {}).values():
            badge.theme = theme
        self._sync_preview()


def _human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _ask_text(parent, title: str, label: str, default: str = "") -> tuple:
    from PySide6.QtWidgets import QInputDialog

    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text, ok


def _ask_yes_no(parent, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text,
                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    return answer == QMessageBox.Yes
