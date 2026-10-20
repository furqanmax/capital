"""Import Data page — pick a workbook, map columns, validate and import."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QTabWidget, QWidget

from services.analytics import COLUMN_LABELS
from services.excel_import import (
    CANONICAL_TO_DB,
    DATA_COLUMNS,
    ImportReport,
    ImportService,
    auto_map_columns,
    list_sheets,
    preview_sheet,
    read_records,
)
from services.validation import validate_records
from services.workbook_reader import DATE_COLUMN, SYMBOL_COLUMN

from ui.widgets import Card, DataTable

from .base import BasePage

FILE_FILTER = ("Workbooks (*.xlsm *.xlsx *.xlsm);;Excel files (*.xlsx *.xlsm *.xls);;"
               "All files (*)")
NOT_MAPPED = "— not imported —"


def _column_label(canonical: str) -> str:
    """Human label for an import mapping row (workbook name -> column label)."""
    db_column = CANONICAL_TO_DB.get(canonical)
    if db_column:
        return f"{COLUMN_LABELS.get(db_column, canonical)}  ({db_column})"
    return canonical


class ImportDataPage(BasePage):
    nav_title = "Import Data"
    nav_icon = "↧"

    #: emitted after a successful import so other pages can refresh
    dataChanged = Signal()

    def page_subtitle(self) -> str:
        return ("Macros are never executed: only cell values are read. The import runs in "
                "one transaction, so a failure leaves the database untouched.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.choose_button = QPushButton("Choose workbook…")
        self.choose_button.clicked.connect(self._choose_file)
        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._import)
        self.header.add_action(self.choose_button)
        self.header.add_action(self.import_button)

        # ---- file + options card ----------------------------------------
        setup = Card(padding=12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Workbook"), 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No file selected")
        self.path_edit.setReadOnly(True)
        grid.addWidget(self.path_edit, 0, 1, 1, 3)

        grid.addWidget(QLabel("Sheet"), 1, 0)
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(180)
        self.sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
        grid.addWidget(self.sheet_combo, 1, 1)

        grid.addWidget(QLabel("Header row"), 1, 2)
        self.header_spin = QSpinBox()
        self.header_spin.setRange(1, 200)
        self.header_spin.setValue(1)
        grid.addWidget(self.header_spin, 1, 3)

        grid.addWidget(QLabel("Duplicates"), 2, 0)
        self.policy_combo = QComboBox()
        self.policy_combo.addItem("Skip existing rows", "skip")
        self.policy_combo.addItem("Update existing rows in place", "update")
        self.policy_combo.addItem("Update and archive previous values", "new_batch")
        grid.addWidget(self.policy_combo, 2, 1)
        self.policy_hint = QLabel("")
        self.policy_hint.setObjectName("Muted")
        self.policy_hint.setWordWrap(True)
        grid.addWidget(self.policy_hint, 4, 1, 1, 3)
        self.policy_combo.currentIndexChanged.connect(self._update_policy_hint)

        grid.addWidget(QLabel("Write mode"), 3, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Strict — all or nothing", "strict")
        self.mode_combo.addItem("Bulk — commit in chunks", "bulk")
        self.mode_combo.setToolTip(
            "Strict writes every row in one transaction: if anything fails the "
            "database is left exactly as it was.\n\n"
            "Bulk commits every 100,000 rows and remembers where it stopped, so a "
            "multi-million-row import that is interrupted can resume instead of "
            "starting over.")
        grid.addWidget(self.mode_combo, 3, 1)

        grid.addWidget(QLabel("Columns"), 2, 2)
        auto_button = QPushButton("Auto-map")
        auto_button.clicked.connect(self._auto_map)
        validate_button = QPushButton("Validate only")
        validate_button.setToolTip("Parse and validate without writing to the database")
        validate_button.clicked.connect(self._validate_only)
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(auto_button)
        button_layout.addWidget(validate_button)
        grid.addWidget(button_row, 2, 3)

        setup.add_layout(grid)
        self.body.addWidget(setup)

        # ---- progress ----------------------------------------------------
        progress_row = QWidget()
        progress_layout = QHBoxLayout(progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        progress_layout.addWidget(self.progress, 1)
        progress_layout.addWidget(self.status_label)
        self.body.addWidget(progress_row)

        # ---- tabs --------------------------------------------------------
        self.tabs = QTabWidget()

        self.preview_table = DataTable(theme=ctx.theme, page_size=50)
        self.tabs.addTab(self.preview_table, "Preview")

        self.mapping_table = DataTable(theme=ctx.theme, page_size=50)
        self.mapping_combo_holder = QWidget()
        self.mapping_layout = QGridLayout(self.mapping_combo_holder)
        self.mapping_layout.setContentsMargins(8, 8, 8, 8)
        mapping_scroll = QScrollArea()
        mapping_scroll.setWidgetResizable(True)
        mapping_scroll.setFrameShape(QScrollArea.NoFrame)
        mapping_scroll.setWidget(self.mapping_combo_holder)
        self.tabs.addTab(mapping_scroll, "Column mapping")

        self.issues_table = DataTable(theme=ctx.theme, page_size=100)
        self.tabs.addTab(self.issues_table, "Validation issues")

        self.history_table = DataTable(theme=ctx.theme, page_size=50)
        self.tabs.addTab(self.history_table, "Import history")

        self.body.addWidget(self.tabs, 1)

        self._path: Optional[Path] = None
        self._headers: List[str] = []
        self._mapping: Dict[str, str] = {}
        self._combos: Dict[str, QComboBox] = {}
        self._report: Optional[ImportReport] = None
        self._worker = None

        self._update_policy_hint()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._reload_history()
        if self._path is not None:
            self._preview()

    # ------------------------------------------------------------------
    # file / sheet selection
    # ------------------------------------------------------------------

    def _choose_file(self) -> None:
        start = str(self.ctx.settings.export_dir().parent)
        selected, _ = QFileDialog.getOpenFileName(self, "Choose a workbook", start, FILE_FILTER)
        if not selected:
            return
        self.load_file(Path(selected))

    def load_file(self, path: Path) -> None:
        self._path = path
        self.path_edit.setText(str(path))
        try:
            sheets = list_sheets(path)
        except Exception as exc:  # noqa: BLE001
            self.warn("Could not open the workbook", f"{type(exc).__name__}: {exc}")
            return
        if not sheets:
            self.warn("Empty workbook", "This file has no worksheets.")
            return

        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.addItems(sheets)
        preferred = self.ctx.settings.preferred_sheets()
        chosen = next((s for s in preferred if s in sheets), sheets[0])
        self.sheet_combo.setCurrentText(chosen)
        self.sheet_combo.blockSignals(False)

        self.import_button.setEnabled(True)
        self._on_sheet_changed(chosen)
        self.notify(f"Opened {path.name} ({len(sheets)} sheets)")

    def _on_sheet_changed(self, sheet: str) -> None:
        if not sheet or self._path is None:
            return
        self._preview()

    # ------------------------------------------------------------------
    # preview + mapping
    # ------------------------------------------------------------------

    def _preview(self) -> None:
        if self._path is None:
            return
        sheet = self.sheet_combo.currentText()
        if not sheet:
            return
        try:
            payload = preview_sheet(self._path, sheet, rows=200)
        except Exception as exc:  # noqa: BLE001
            self.warn("Could not read the sheet", f"{type(exc).__name__}: {exc}")
            return

        self._headers = list(payload["headers"])
        self.header_spin.blockSignals(True)
        self.header_spin.setValue(int(payload["header_row"]))
        self.header_spin.blockSignals(False)

        frame = pd.DataFrame(payload["preview"], columns=self._headers)
        self.preview_table.set_frame(frame)

        self._mapping = auto_map_columns(self._headers)
        self._build_mapping_combos()
        # A workbook saved by a writer that omits the sheet dimension reports no
        # row/column count; say so instead of printing "None".
        if payload.get("max_row") and payload.get("max_column"):
            size = f"{payload['max_row']:,} rows × {payload['max_column']} columns"
        else:
            size = f"{len(self._headers)} mapped columns (sheet size not recorded)"
        self.status_label.setText(
            f"{size}; header detected on row {payload['header_row']}")

    def _build_mapping_combos(self) -> None:
        while self.mapping_layout.count():
            item = self.mapping_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._combos = {}

        self.mapping_layout.addWidget(_muted("Application column"), 0, 0)
        self.mapping_layout.addWidget(_muted("Source column in the sheet"), 0, 1)
        for row, canonical in enumerate(DATA_COLUMNS, start=1):
            self.mapping_layout.addWidget(QLabel(_column_label(canonical)), row, 0)
            combo = QComboBox()
            combo.addItem(NOT_MAPPED, "")
            for header in self._headers:
                combo.addItem(str(header), str(header))
            mapped = self._mapping.get(canonical)
            if mapped:
                index = combo.findData(mapped)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._capture_mapping)
            self.mapping_layout.addWidget(combo, row, 1)
            self._combos[canonical] = combo
        self.mapping_layout.setColumnStretch(1, 1)

    def _capture_mapping(self, *_args) -> None:
        self._mapping = {}
        for canonical, combo in self._combos.items():
            value = combo.currentData()
            if value:
                self._mapping[canonical] = value

    def _auto_map(self) -> None:
        if not self._headers:
            return
        self._preview()
        self.notify("Columns auto-mapped — review the mapping tab")

    def _update_policy_hint(self) -> None:
        hints = {
            "skip": "Existing (symbol, date) rows are left exactly as they are.",
            "update": "Existing (symbol, date) rows are overwritten with the incoming values.",
            "new_batch": "Like update, but the superseded values are archived in the "
                         "price_extras table so the previous snapshot is preserved.",
        }
        self.policy_hint.setText(hints.get(self.policy_combo.currentData(), ""))

    # ------------------------------------------------------------------
    # validation / import
    # ------------------------------------------------------------------

    def _read_and_validate(self):
        assert self._path is not None
        sheet = self.sheet_combo.currentText()
        header_row = int(self.header_spin.value())
        payload = read_records(self._path, sheet, header_row)
        result = validate_records(payload["headers"], payload["records"], self._mapping)
        return payload, result

    def _validate_only(self) -> None:
        if self._path is None:
            self.warn("No workbook", "Choose a workbook first.")
            return
        self._capture_mapping()
        missing = [c for c in (SYMBOL_COLUMN, DATE_COLUMN) if c not in self._mapping]
        if missing:
            self.warn("Mapping incomplete",
                      "The Symbol and Date columns must be mapped before validating.")
            return
        try:
            payload, result = self._read_and_validate()
        except Exception as exc:  # noqa: BLE001
            self.warn("Validation failed", f"{type(exc).__name__}: {exc}")
            return

        rows = [{"row": i.row_number, "column": i.column, "severity": i.severity,
                 "message": i.message, "value": i.raw_value} for i in result.issues]
        self.issues_table.set_frame(pd.DataFrame(rows) if rows
                                    else pd.DataFrame(columns=["row", "column", "severity",
                                                               "message", "value"]))
        self.tabs.setCurrentWidget(self.issues_table)
        summary = result.summary()
        self.status_label.setText(
            f"Validated {summary['rows_parsed']:,} rows · {summary['errors']} errors · "
            f"{summary['warnings']} warnings · {summary['extra_columns']} unmapped columns")
        self.notify("Validation complete — nothing was written")

    def _import(self) -> None:
        if self._path is None:
            self.warn("No workbook", "Choose a workbook first.")
            return
        self._capture_mapping()
        missing = [c for c in (SYMBOL_COLUMN, DATE_COLUMN) if c not in self._mapping]
        if missing:
            self.warn("Mapping incomplete",
                      "Symbol and Date must be mapped before importing.")
            return

        sheet = self.sheet_combo.currentText()
        policy = self.policy_combo.currentData()
        header_row = int(self.header_spin.value())
        if policy != "skip" and not self.confirm(
            "Confirm duplicate handling",
            f"Policy “{policy}” will overwrite existing rows for the same symbol and "
            f"date.\n\nContinue importing {self._path.name}[{sheet}]?",
            danger=(policy == "update"),
        ):
            return

        self._set_busy(True)
        path = self._path
        db = self.ctx.db
        data = self.ctx.data
        mapping = dict(self._mapping)
        mode = self.mode_combo.currentData()

        def job(progress, is_cancelled):
            def relay(current: int, total: int, message: str = "") -> None:
                # ``total == 0`` means the workbook does not record its row count.
                if total:
                    percent = int(current * 100 / max(1, total))
                    detail = message or f"{current:,}/{total:,} rows"
                else:
                    percent = -1
                    detail = message or f"{current:,} rows"
                progress(percent, detail)

            report = ImportService(db).run(
                path, sheet, mapping,
                duplicate_policy=policy,
                header_row=header_row,
                mode=mode,
                resume=(mode == "bulk"),
                progress=relay,
            )
            # Keep the optional DuckDB mirror in step while we are still on a
            # worker thread: on a fresh database the first copy of a large table
            # can take a while, and doing it here keeps the pages instant.
            if getattr(data.backend, "name", "") == "duckdb" and not is_cancelled():
                try:
                    progress(-1, "Updating the analytics index…")
                    data.backend.invalidate()
                    data.backend.sync()
                except Exception as exc:  # noqa: BLE001 - analytics is optional
                    db.log("analytics",
                           f"Analytics index not updated ({exc}); it will rebuild "
                           "on the next dashboard view", severity="warning")
            return report

        from ui.workers import run_async

        self._worker = run_async(
            self, job, self._import_done, self._import_failed,
            self._import_progress)

    def _import_progress(self, percent: int, message: str) -> None:
        if percent is None or int(percent) < 0:
            if self.progress.maximum() != 0:
                self.progress.setRange(0, 0)      # indeterminate: size unknown
        else:
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(int(percent))
        self.status_label.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.import_button.setEnabled(not busy and self._path is not None)
        self.choose_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status_label.setText("Importing…")
        else:
            self.progress.setRange(0, 100)

    def _import_done(self, report: ImportReport) -> None:
        self._set_busy(False)
        self._report = report
        rows = [{"row": i.get("row_number"), "column": i.get("column_name"),
                 "severity": i.get("severity"), "message": i.get("message"),
                 "value": i.get("raw_value")} for i in report.issues]
        self.issues_table.set_frame(pd.DataFrame(rows) if rows
                                    else pd.DataFrame(columns=["row", "column", "severity",
                                                               "message", "value"]))
        self.status_label.setText(
            f"Imported {report.rows_inserted:,} new, {report.rows_updated:,} updated, "
            f"{report.rows_skipped:,} skipped, {report.rows_rejected:,} rejected "
            f"in {report.duration_seconds:.1f}s")

        self.ctx.invalidate("workbook import")
        self._reload_history()
        self.dataChanged.emit()

        extra = ("\nUnmapped columns preserved: " + ", ".join(report.extra_columns)
                 if report.extra_columns else "")
        self.info(
            "Import complete",
            f"Batch #{report.batch_id}\n"
            f"Rows read: {report.rows_read:,}\n"
            f"Inserted: {report.rows_inserted:,}\n"
            f"Updated: {report.rows_updated:,}\n"
            f"Skipped: {report.rows_skipped:,}\n"
            f"Rejected: {report.rows_rejected:,}\n"
            f"Errors: {report.error_count} · Warnings: {report.warning_count}"
            f"{extra}")
        self.notify("Import finished")

    def _import_failed(self, message: str, detail: str) -> None:
        self._set_busy(False)
        self.status_label.setText("Import failed — no rows were written")
        self.warn("Import failed", f"{message}\n\nThe database was not changed.")
        self.ctx.db.log("import", f"Import failed: {message}", severity="error")

    def _reload_history(self) -> None:
        batches = self.ctx.data.batches(limit=100)
        rows = [{
            "batch": b.id,
            "file": Path(b.source_file).name,
            "sheet": b.sheet_name,
            "policy": b.duplicate_policy,
            "status": b.status,
            "started": b.started_at,
            "finished": b.finished_at,
            "read": b.rows_read,
            "new": b.rows_inserted,
            "updated": b.rows_updated,
            "skipped": b.rows_skipped,
            "rejected": b.rows_rejected,
        } for b in batches]
        self.history_table.set_frame(pd.DataFrame(rows) if rows
                                     else pd.DataFrame(columns=["batch", "file", "sheet",
                                                                "policy", "status", "started"]))

    def on_theme_changed(self, theme: str) -> None:
        for table in (self.preview_table, self.issues_table, self.history_table,
                      self.mapping_table):
            table.set_theme(theme)


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    return label
