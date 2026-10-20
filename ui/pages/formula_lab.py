"""Formula Lab — create, version and test custom calculated metrics."""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from ui.dialogs import FormulaEditorDialog
from ui.widgets import Card, DataTable

from .base import BasePage


class FormulaLabPage(BasePage):
    nav_title = "Formula Lab"
    nav_icon = "ƒ"

    def page_subtitle(self) -> str:
        return ("Expressions are evaluated by a sandboxed parser — no Python is executed. "
                "Every saved change creates a new version you can restore.")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)

        self.new_button = QPushButton("New formula")
        self.new_button.setObjectName("Primary")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("Danger")
        self.toggle_button = QPushButton("Disable")
        for button in (self.new_button, self.edit_button, self.delete_button,
                       self.toggle_button):
            self.header.add_action(button)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentItemChanged.connect(self._on_select)
        left_layout.addWidget(self.list, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        left_layout.addWidget(self.status_label)
        splitter.addWidget(left)

        right = Card()
        self.headline = QLabel("Select a formula")
        self.headline.setObjectName("SectionTitle")
        right.add(self.headline)
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        self.meta.setWordWrap(True)
        right.add(self.meta)

        right.add(QLabel("Expression"))
        self.expression = QPlainTextEdit()
        self.expression.setReadOnly(True)
        self.expression.setMaximumHeight(90)
        right.add(self.expression)

        self.tabs = QTabWidget()
        self.versions = DataTable(theme=ctx.theme, page_size=10)
        self.versions_table = self.versions
        self.preview = DataTable(theme=ctx.theme, page_size=25)
        self.tabs.addTab(self.versions, "Version history")
        self.tabs.addTab(self.preview, "Test output")
        right.add(self.tabs)

        self.restore_button = QPushButton("Restore selected version")
        self.restore_button.clicked.connect(self._restore)
        right.add(self.restore_button)

        splitter.addWidget(right)
        splitter.setSizes([260, 880])
        self.body.addWidget(splitter, 1)

        self.new_button.clicked.connect(self._new)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.toggle_button.clicked.connect(self._toggle)
        self._current_id: Optional[int] = None

    # ------------------------------------------------------------------

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        formulas = self.ctx.formulas.list_formulas()
        self.list.blockSignals(True)
        self.list.clear()
        for formula in formulas:
            marker = "●" if formula.enabled else "○"
            item = QListWidgetItem(f"{marker} {formula.name}")
            item.setData(Qt.UserRole, formula.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.status_label.setText(f"{len(formulas)} formulas "
                                  f"({sum(1 for f in formulas if f.enabled)} enabled)")

        if formulas:
            target = self._current_id if self._current_id else formulas[0].id
            for row in range(self.list.count()):
                if self.list.item(row).data(Qt.UserRole) == target:
                    self.list.setCurrentRow(row)
                    break
        else:
            self._current_id = None
            self.headline.setText("No formulas yet")
            self.meta.setText("Use “New formula” to add one.")
            self.expression.setPlainText("")
            self.versions.set_frame(pd.DataFrame())
            self.preview.set_frame(pd.DataFrame())

    # ------------------------------------------------------------------

    def _selected_id(self) -> Optional[int]:
        item = self.list.currentItem()
        return None if item is None else int(item.data(Qt.UserRole))

    def _on_select(self, current: Optional[QListWidgetItem], _previous=None) -> None:
        if current is None:
            return
        self._current_id = int(current.data(Qt.UserRole))
        self._show(self._current_id)

    def _show(self, formula_id: int) -> None:
        formula = self.ctx.formulas.get(formula_id)
        if formula is None:
            return
        self.headline.setText(f"{formula.name} — {formula.category}")
        reason = "enabled" if formula.enabled else "disabled"
        self.meta.setText(
            f"{formula.description or 'No description'} · output column "
            f"“{formula.output_column}” · {formula.data_type} · null policy "
            f"{formula.null_policy} · version {formula.current_version} · {reason}"
            + (" · built-in" if formula.builtin else "")
        )
        self.expression.setPlainText(formula.expression)
        self.toggle_button.setText("Disable" if formula.enabled else "Enable")

        versions = self.ctx.formulas.versions(formula_id)
        self.versions.set_frame(pd.DataFrame([{
            "version": v.version, "changed": v.created_at, "note": v.change_note,
            "expression": v.expression,
        } for v in versions]))
        self._update_preview(formula)

    def _update_preview(self, formula) -> None:
        symbols = self.ctx.data.symbols()
        if not symbols:
            self.preview.set_frame(pd.DataFrame(
                {"note": ["Import data to test formulas against real rows."]}))
            return
        frame = self.ctx.data.symbol_frame(symbols[0]).head(50)
        result = self.ctx.formulas.preview(formula.expression, frame, formula.null_policy)
        if not result.ok:
            self.preview.set_frame(pd.DataFrame({"error": [result.error]}))
            return
        display = frame[["trade_date", "symbol"]].copy()
        display[formula.output_column] = result.values
        self.preview.set_frame(display)
        self.notify(f"Tested {formula.name} on {symbols[0]}")

    # ------------------------------------------------------------------

    def _new(self) -> None:
        dialog = FormulaEditorDialog(self, self.ctx)
        if dialog.exec() and dialog.saved_id:
            self._current_id = dialog.saved_id
            self.refresh()
            self.notify("Formula created")

    def _edit(self) -> None:
        formula_id = self._selected_id()
        if formula_id is None:
            self.warn("No formula selected", "Choose a formula from the list first.")
            return
        dialog = FormulaEditorDialog(self, self.ctx, formula_id=formula_id)
        if dialog.exec():
            self.refresh()
            self.notify("Formula updated — a new version was recorded")

    def _delete(self) -> None:
        formula_id = self._selected_id()
        if formula_id is None:
            return
        formula = self.ctx.formulas.get(formula_id)
        if formula is None:
            return
        if formula.builtin:
            if not self.confirm("Delete built-in formula",
                                f"“{formula.name}” ships with the application. Delete it anyway?",
                                danger=True):
                return
        elif not self.confirm("Delete formula",
                              f"Delete “{formula.name}” and all of its versions?\n"
                              "This cannot be undone.", danger=True):
            return
        self.ctx.formulas.delete(formula_id)
        self._current_id = None
        self.refresh()
        self.notify("Formula deleted")

    def _toggle(self) -> None:
        formula_id = self._selected_id()
        if formula_id is None:
            return
        formula = self.ctx.formulas.get(formula_id)
        if formula is None:
            return
        self.ctx.formulas.set_enabled(formula_id, not formula.enabled)
        self.refresh()

    def _restore(self) -> None:
        formula_id = self._selected_id()
        if formula_id is None:
            return
        selected = self.versions.selected_frame()
        if selected.empty:
            self.warn("No version selected", "Pick a row in the version history first.")
            return
        version = int(selected.iloc[0]["version"])
        if not self.confirm("Restore version",
                            f"Restore version {version}? The current expression becomes "
                            "a new version, so nothing is lost."):
            return
        self.ctx.formulas.restore_version(formula_id, version)
        self.refresh()
        self.notify(f"Restored version {version}")

    def on_theme_changed(self, theme: str) -> None:
        self.versions.set_theme(theme)
        self.preview.set_theme(theme)
