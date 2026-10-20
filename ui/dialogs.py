"""Shared dialogs: export, formula editor, component editor and confirmations."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from services.dashboard_service import (
    AGGREGATIONS, COMPONENT_KINDS, SOURCES, metric_choices, metric_label,
)
from services.data_service import DataFilters
from services.export_service import EXPORT_FORMATS, default_export_path
from services.formula_service import (
    SYNTAX_REFERENCE, VALID_DATA_TYPES, VALID_NULL_POLICIES,
)
from services.report_service import REPORT_KINDS, REPORT_TITLES

from ui.context import AppContext


class ExportDialog(QDialog):
    """Choose a report, a format and a destination, then run the export."""

    def __init__(self, parent: Optional[QWidget], ctx: AppContext, title: str,
                 suggested_kind: str = "screener",
                 filters: Optional[DataFilters] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.filters = filters or DataFilters()
        self.setWindowTitle(f"Export — {title}")
        self.setMinimumWidth(560)
        self.result_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.kind_combo = QComboBox()
        for kind in REPORT_KINDS:
            self.kind_combo.addItem(REPORT_TITLES[kind], kind)
        index = self.kind_combo.findData(suggested_kind)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        form.addRow("Report", self.kind_combo)

        self.format_combo = QComboBox()
        for fmt in EXPORT_FORMATS:
            self.format_combo.addItem(fmt.upper(), fmt)
        default_format = ctx.settings.get("export.default_format", "xlsx")
        index = self.format_combo.findData(default_format)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        form.addRow("Format", self.format_combo)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(browse)
        form.addRow("Destination", path_row)

        self.include_calculated = QCheckBox("Append Formula Lab columns (history report)")
        form.addRow("", self.include_calculated)

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional note recorded in the file")
        form.addRow("Note", self.notes_edit)

        layout.addLayout(form)

        self.scope_label = QLabel(self._scope_text())
        self.scope_label.setObjectName("Muted")
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)

        buttons = QDialogButtonBox()
        self.export_button = buttons.addButton("Export", QDialogButtonBox.AcceptRole)
        self.export_button.setObjectName("Primary")
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._run_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.kind_combo.currentIndexChanged.connect(self._suggest_path)
        self.format_combo.currentIndexChanged.connect(self._suggest_path)
        self._suggest_path()

    # ------------------------------------------------------------------

    def _scope_text(self) -> str:
        described = self.filters.describe()
        return ("Rows: " + ", ".join(f"{k} = {v}" for k, v in described.items())
                + f" · formula mode: {self.ctx.settings.formula_mode()}")

    def _suggest_path(self, *_args) -> None:
        kind = self.kind_combo.currentData()
        fmt = self.format_combo.currentData()
        title = REPORT_TITLES.get(kind, "report")
        default = default_export_path(title, fmt)
        self.path_edit.setText(str(self.ctx.settings.export_dir() / default.name))

    def _browse(self) -> None:
        fmt = self.format_combo.currentData()
        pattern = {"xlsx": "Excel workbook (*.xlsx)", "csv": "CSV file (*.csv)",
                   "pdf": "PDF document (*.pdf)"}.get(fmt, "*.*")
        selected, _ = QFileDialog.getSaveFileName(
            self, "Export to", self.path_edit.text() or str(self.ctx.settings.export_dir()),
            pattern)
        if selected:
            self.path_edit.setText(selected)

    def _run_export(self) -> None:
        kind = self.kind_combo.currentData()
        fmt = self.format_combo.currentData()
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Destination required", "Choose where to write the file.")
            return
        try:
            report = self.ctx.reports.build(
                kind, self.filters,
                include_calculated=self.include_calculated.isChecked())
            if self.notes_edit.text().strip():
                report.notes = (report.notes + " " + self.notes_edit.text().strip()).strip()
            result = self.ctx.reports.export(report, path=path, fmt=fmt)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            QMessageBox.critical(self, "Export failed", f"{type(exc).__name__}: {exc}")
            return
        self.result_path = result.path
        QMessageBox.information(
            self, "Export complete",
            f"{result.rows:,} rows written to:\n{result.path}\n\n"
            f"Generated at {result.generated_at:%Y-%m-%d %H:%M:%S}.")
        self.accept()


class FormulaEditorDialog(QDialog):
    """Create or edit a Formula Lab expression."""

    def __init__(self, parent: Optional[QWidget], ctx: AppContext,
                 formula_id: Optional[int] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.formula_id = formula_id
        self.saved_id: Optional[int] = None
        self.setWindowTitle("Edit formula" if formula_id else "New formula")
        self.setMinimumSize(720, 640)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        form.addRow("Name", self.name_edit)

        self.description_edit = QLineEdit()
        form.addRow("Description", self.description_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(
            ["custom", "price", "volume", "rolling", "classification", "baseline"])
        form.addRow("Category", self.category_combo)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("column name added to result tables")
        form.addRow("Output column", self.output_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(list(VALID_DATA_TYPES))
        form.addRow("Data type", self.type_combo)

        self.null_combo = QComboBox()
        self.null_combo.addItems(list(VALID_NULL_POLICIES))
        self.null_combo.setToolTip(
            "blank = keep missing values blank, zero = treat them as 0, "
            "error = fail the whole column")
        form.addRow("Null policy", self.null_combo)

        self.format_edit = QLineEdit()
        self.format_edit.setPlaceholderText("Python format, e.g. 0.00 or #,##0")
        form.addRow("Display format", self.format_edit)

        layout.addLayout(form)

        layout.addWidget(QLabel("Expression"))
        self.expression_edit = QPlainTextEdit()
        self.expression_edit.setPlaceholderText(
            'e.g. (LAST - PREVCLOSE) / PREVCLOSE * 100  or  AVERAGE(CLOSE, 20)')
        self.expression_edit.setMinimumHeight(90)
        layout.addWidget(self.expression_edit)

        buttons_row = QHBoxLayout()
        self.validate_button = QPushButton("Validate")
        self.test_button = QPushButton("Test on real data")
        self.reference_button = QPushButton("Syntax reference")
        buttons_row.addWidget(self.validate_button)
        buttons_row.addWidget(self.test_button)
        buttons_row.addWidget(self.reference_button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.preview_label = QLabel("Test results appear here.")
        self.preview_label.setObjectName("Muted")
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(60)
        layout.addWidget(self.preview_label)

        footer = QDialogButtonBox()
        self.save_button = footer.addButton("Save", QDialogButtonBox.AcceptRole)
        self.save_button.setObjectName("Primary")
        footer.addButton("Cancel", QDialogButtonBox.RejectRole)
        footer.accepted.connect(self._save)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

        self.validate_button.clicked.connect(self._validate)
        self.test_button.clicked.connect(self._test)
        self.reference_button.clicked.connect(self._show_reference)

        if formula_id is not None:
            self._load(formula_id)

    # ------------------------------------------------------------------

    def _load(self, formula_id: int) -> None:
        formula = self.ctx.formulas.get(formula_id)
        if formula is None:
            return
        self.name_edit.setText(formula.name)
        self.description_edit.setText(formula.description or "")
        self.category_combo.setCurrentText(formula.category or "custom")
        self.output_edit.setText(formula.output_column)
        self.type_combo.setCurrentText(formula.data_type)
        self.null_combo.setCurrentText(formula.null_policy)
        self.format_edit.setText(formula.display_format or "")
        self.expression_edit.setPlainText(formula.expression)

    def _validate(self) -> bool:
        ok, message = self.ctx.formulas.validate(self.expression_edit.toPlainText())
        if ok:
            fields = self.ctx.formulas.required_fields(self.expression_edit.toPlainText())
            self.status_label.setText(
                f"Valid. Reads: {', '.join(fields) if fields else 'no fields'}")
            self.status_label.setStyleSheet("color: #3FB950;")
        else:
            self.status_label.setText(f"Invalid: {message}")
            self.status_label.setStyleSheet("color: #F85149;")
        return ok

    def _test(self) -> None:
        if not self._validate():
            return
        symbols = self.ctx.data.symbols()
        if not symbols:
            self.preview_label.setText("No data has been imported yet, so there is nothing "
                                       "to test against.")
            return
        frame = self.ctx.data.symbol_frame(symbols[0]).head(10)
        preview = self.ctx.formulas.preview(
            self.expression_edit.toPlainText(), frame,
            self.null_combo.currentText())
        if not preview.ok:
            self.preview_label.setText(f"Error: {preview.error}")
            return
        lines = [f"{symbols[0]} — first {len(preview.values)} sessions:"]
        for index, value in enumerate(preview.values[:10]):
            session = frame.iloc[index]["trade_date"]
            lines.append(f"  {session} → {value!r}")
        self.preview_label.setText("\n".join(lines))

    def _show_reference(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Formula syntax reference")
        box.setTextFormat(Qt.PlainText)
        box.setText(SYNTAX_REFERENCE)
        box.exec()

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        expression = self.expression_edit.toPlainText().strip()
        output = self.output_edit.text().strip()
        if not name or not expression or not output:
            QMessageBox.warning(self, "Missing information",
                                "Name, output column and expression are all required.")
            return
        ok, message = self.ctx.formulas.validate(expression)
        if not ok:
            QMessageBox.warning(self, "Invalid expression", message)
            return
        try:
            if self.formula_id is None:
                formula = self.ctx.formulas.create(
                    name=name, expression=expression, output_column=output,
                    description=self.description_edit.text().strip(),
                    category=self.category_combo.currentText().strip() or "custom",
                    data_type=self.type_combo.currentText(),
                    null_policy=self.null_combo.currentText(),
                    display_format=self.format_edit.text().strip() or None,
                )
            else:
                formula = self.ctx.formulas.update(
                    self.formula_id,
                    expression=expression,
                    change_note=f"edited in Formula Lab at {_dt.datetime.now():%Y-%m-%d %H:%M}",
                    name=name,
                    description=self.description_edit.text().strip(),
                    category=self.category_combo.currentText().strip() or "custom",
                    output_column=output,
                    data_type=self.type_combo.currentText(),
                    null_policy=self.null_combo.currentText(),
                    display_format=self.format_edit.text().strip() or None,
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save", f"{type(exc).__name__}: {exc}")
            return
        self.saved_id = formula.id
        self.accept()


class ComponentEditorDialog(QDialog):
    """Add or edit one dashboard component."""

    def __init__(self, parent: Optional[QWidget], ctx: AppContext,
                 component=None):
        super().__init__(parent)
        self.ctx = ctx
        self.component = component
        self.setWindowTitle("Edit component" if component else "Add component")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(list(COMPONENT_KINDS))
        form.addRow("Kind", self.kind_combo)

        self.title_edit = QLineEdit()
        form.addRow("Title", self.title_edit)

        self.source_combo = QComboBox()
        self.source_combo.addItems(list(SOURCES))
        form.addRow("Data source", self.source_combo)

        self.metric_combo = QComboBox()
        self.metric_combo.setEditable(True)
        form.addRow("Metric", self.metric_combo)

        self.dimension_combo = QComboBox()
        self.dimension_combo.setEditable(True)
        self.dimension_combo.addItems(
            ["", "trade_date", "symbol", "d_ind", "dv1", "dv2", "vol_badge"])
        form.addRow("Group by", self.dimension_combo)

        self.aggregation_combo = QComboBox()
        self.aggregation_combo.addItems(list(AGGREGATIONS))
        form.addRow("Aggregation", self.aggregation_combo)

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 500)
        self.limit_spin.setValue(10)
        form.addRow("Row limit (tables/pies)", self.limit_spin)

        self.warning_edit = QLineEdit()
        self.warning_edit.setPlaceholderText("KPI warning threshold (optional)")
        form.addRow("Warning at", self.warning_edit)

        self.danger_edit = QLineEdit()
        self.danger_edit.setPlaceholderText("KPI danger threshold (optional)")
        form.addRow("Danger at", self.danger_edit)

        self.format_edit = QLineEdit()
        self.format_edit.setPlaceholderText("#,##0 / 0.00 / date")
        form.addRow("Value format", self.format_edit)

        self.visible_check = QCheckBox("Visible on the dashboard")
        self.visible_check.setChecked(True)
        form.addRow("", self.visible_check)

        layout.addLayout(form)

        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        footer = QDialogButtonBox()
        footer.addButton("Save", QDialogButtonBox.AcceptRole).setObjectName("Primary")
        footer.addButton("Cancel", QDialogButtonBox.RejectRole)
        footer.accepted.connect(self._save)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

        self.source_combo.currentTextChanged.connect(self._reload_metrics)
        self.kind_combo.currentTextChanged.connect(self._update_hint)

        if component is not None:
            self._load(component)
        else:
            self._reload_metrics("prices")
            self._update_hint("kpi")

    # ------------------------------------------------------------------

    def _reload_metrics(self, source: str) -> None:
        current = self.metric_combo.currentText()
        self.metric_combo.clear()
        self.metric_combo.addItems(metric_choices().get(source, []))
        if current:
            self.metric_combo.setCurrentText(current)

    def _update_hint(self, kind: str) -> None:
        hints = {
            "kpi": "A single aggregated number. 'overview' source supports the headline totals.",
            "line": "Time series: metric is plotted against trade date.",
            "bar": "Time series drawn as bars.",
            "pie": "Category split: 'Group by' defines the slices.",
            "table": "Top rows: use 'Group by' = symbol for per-stock tables, or the "
                     "derived source for top gainers/losers.",
        }
        self.hint.setText(hints.get(kind, ""))

    def _load(self, component) -> None:
        self.kind_combo.setCurrentText(component.kind)
        self.title_edit.setText(component.title)
        self.source_combo.setCurrentText(component.source)
        self._reload_metrics(component.source)
        self.metric_combo.setCurrentText(component.metric)
        self.dimension_combo.setCurrentText(component.dimension or "")
        self.aggregation_combo.setCurrentText(component.aggregation)
        options = component.options or {}
        if options.get("limit"):
            self.limit_spin.setValue(int(options["limit"]))
        thresholds = options.get("thresholds") or {}
        self.warning_edit.setText(str(thresholds.get("warning", "")))
        self.danger_edit.setText(str(thresholds.get("danger", "")))
        self.format_edit.setText(str(options.get("format", "")))
        self.visible_check.setChecked(bool(component.visible))

    def _save(self) -> None:
        metric = self.metric_combo.currentText().strip()
        title = self.title_edit.text().strip() or metric_label(metric)
        options: Dict[str, Any] = {}
        if self.kind_combo.currentText() in ("table", "pie"):
            options["limit"] = self.limit_spin.value()
        thresholds = {}
        if self.warning_edit.text().strip():
            thresholds["warning"] = _float(self.warning_edit.text())
        if self.danger_edit.text().strip():
            thresholds["danger"] = _float(self.danger_edit.text())
        if thresholds:
            options["thresholds"] = thresholds
        if self.format_edit.text().strip():
            options["format"] = self.format_edit.text().strip()

        try:
            if self.component is None:
                self.ctx.dashboard.add_component(
                    kind=self.kind_combo.currentText(),
                    title=title, metric=metric,
                    aggregation=self.aggregation_combo.currentText(),
                    source=self.source_combo.currentText(),
                    dimension=self.dimension_combo.currentText().strip() or None,
                    options=options or None,
                    visible=self.visible_check.isChecked(),
                )
            else:
                self.ctx.dashboard.update_component(
                    self.component.id,
                    kind=self.kind_combo.currentText(), title=title, metric=metric,
                    aggregation=self.aggregation_combo.currentText(),
                    source=self.source_combo.currentText(),
                    dimension=self.dimension_combo.currentText().strip() or None,
                    options=options or None,
                    visible=self.visible_check.isChecked(),
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save", f"{type(exc).__name__}: {exc}")
            return
        self.accept()


def _float(text: str) -> float:
    try:
        return float(str(text).strip())
    except ValueError:
        return 0.0


__all__ = ["ExportDialog", "FormulaEditorDialog", "ComponentEditorDialog"]
