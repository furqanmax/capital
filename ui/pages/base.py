"""Base class for the application's pages."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from ui.context import AppContext
from ui.widgets import PageHeader


class BasePage(QWidget):
    """Common shell: header, body layout, activation hook and error reporting."""

    status = Signal(str)

    #: shown in the navigation list
    nav_title = "Page"
    nav_icon = "•"

    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self._loaded = False

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(16, 14, 16, 12)
        self._root.setSpacing(12)

        self.header = PageHeader(self.page_title(), self.page_subtitle())
        self._root.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self._root.addLayout(self.body, 1)

    # -- overridable ------------------------------------------------------

    def page_title(self) -> str:
        return self.nav_title

    def page_subtitle(self) -> str:
        return ""

    def load(self) -> None:
        """Populate the page for the first time."""

    def refresh(self) -> None:
        """Reload data (called on activate and after a data change)."""

    def on_theme_changed(self, theme: str) -> None:
        """Update any custom-painted children."""

    # -- helpers ----------------------------------------------------------

    def activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.load()
        else:
            self.refresh()

    def notify(self, message: str) -> None:
        self.status.emit(message)

    def warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def confirm(self, title: str, message: str, *, danger: bool = False) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
        yes = box.addButton("Continue", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        return box.clickedButton() is yes


__all__ = ["BasePage"]
