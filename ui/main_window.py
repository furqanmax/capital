"""Main window: navigation, menus, status bar and theme handling.

The window owns the pages, applies the theme, and is the single place that reacts
to a change that other pages need to know about (an import, a settings change, a
theme switch).  Pages never talk to each other directly.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QStackedWidget, QStatusBar, QToolBar, QVBoxLayout,
    QWidget,
)

from ui.context import AppContext
from ui.pages import PAGE_CLASSES, SettingsPage
from ui.theme import stylesheet
from ui.widgets import Badge

from .theme import palette

APP_NAME = "Capital Analytics"
APP_VERSION = "1.0.0"


def teardown(window: "MainWindow", application=None) -> None:
    """Destroy the window — and its web views — before Qt releases its profile.

    QtWebEngine aborts the whole process ("Release of profile requested but
    WebEnginePage still not deleted") if a ``QWebEnginePage`` outlives its
    profile, which is exactly what happens when the main window is only
    garbage-collected at interpreter shutdown.  Closing the window and then
    pumping the deferred-delete events guarantees every child view is really
    gone first.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = application or QApplication.instance()
    window.close()
    window.deleteLater()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        if app is not None:
            app.processEvents()


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)

        # ---- navigation + page stack --------------------------------------
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(196)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        brand = QLabel("  CAPITAL\n  analytics")
        brand.setObjectName("SectionTitle")
        brand.setFixedHeight(54)
        brand.setStyleSheet(
            f"padding-left: 10px; color: {palette(ctx.theme)['accent']};")
        sidebar_layout.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFrameShape(QListWidget.NoFrame)
        sidebar_layout.addWidget(self.nav, 1)

        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self.toggle_theme)
        sidebar_layout.addWidget(self.theme_button)
        layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # ---- pages ---------------------------------------------------------
        self.pages: List[QWidget] = []
        self.page_index: Dict[str, int] = {}
        self.settings_page: Optional[SettingsPage] = None

        for key, page_class in PAGE_CLASSES:
            page = page_class(ctx, self)
            page.status.connect(self._on_status)
            if hasattr(page, "themeChanged"):
                page.themeChanged.connect(self.apply_theme)
            if hasattr(page, "coloursChanged"):
                page.coloursChanged.connect(self.apply_colours)
            if hasattr(page, "dataChanged"):
                page.dataChanged.connect(self._on_data_changed)
            index = self.stack.addWidget(page)
            self.page_index[key] = index
            self.pages.append(page)
            item = QListWidgetItem(f"  {page_class.nav_icon}   {page_class.nav_title}")
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(0, 34))
            self.nav.addItem(item)
            if isinstance(page, SettingsPage):
                self.settings_page = page

        self.nav.currentRowChanged.connect(self._on_nav_changed)

        # ---- dashboard "manage layout" wiring -----------------------------
        dashboard = self.pages[0]
        if hasattr(dashboard, "set_manager_callback"):
            dashboard.set_manager_callback(self.show_dashboard_builder)

        # ---- menu / toolbar ------------------------------------------------
        self._build_menus()
        self._build_status_bar()

        self.apply_theme(ctx.theme)
        self.go_to(ctx.settings.get("dashboard.default_landing_page", "dashboard"))
        self._update_brand()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------

    def go_to(self, key: str) -> None:
        index = self.page_index.get(key)
        if index is None:
            index = 0
        self.nav.setCurrentRow(index)

    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.nav.item(row)
        if item is None:
            return
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        try:
            page.activate()
        except Exception as exc:  # noqa: BLE001 - a page must never kill the window
            QMessageBox.critical(self, "Page error", f"{type(exc).__name__}: {exc}")
        self._update_brand()

    def show_dashboard_builder(self) -> None:
        self.go_to("settings")
        if self.settings_page is not None:
            self.settings_page.show_dashboard_builder()

    def _on_status(self, message: str) -> None:
        self.status_message.setText(message)

    def _on_data_changed(self) -> None:
        """An import or a settings change: refresh every loaded page."""
        for page in self.pages:
            if getattr(page, "_loaded", False) and page is self.stack.currentWidget():
                try:
                    page.refresh()
                except Exception:  # noqa: BLE001 - never block a refresh
                    pass
        self._update_brand()

    def current_page_key(self) -> str:
        item = self.nav.currentItem()
        return "" if item is None else str(item.data(Qt.UserRole))

    # ------------------------------------------------------------------
    # theme
    # ------------------------------------------------------------------

    def toggle_theme(self) -> None:
        new_theme = "light" if self.ctx.theme == "dark" else "dark"
        self.ctx.settings.set("display.theme", new_theme)
        self.apply_theme(new_theme)
        if self.settings_page is not None:
            self.settings_page.theme_selector_sync(new_theme)

    def apply_theme(self, theme: str) -> None:
        self.ctx.theme = theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(theme))
        self.theme_button.setText(
            "  ☀  Light mode" if theme == "dark" else "  ☾  Dark mode")
        self._update_brand()
        for page in self.pages:
            handler = getattr(page, "on_theme_changed", None)
            if callable(handler):
                try:
                    handler(theme)
                except Exception:  # noqa: BLE001
                    pass
        self._update_brand()

    def apply_colours(self) -> None:
        """Re-apply the workbook colour scheme after Settings ▸ Colours saves."""
        self.ctx.palette.apply()
        self.apply_theme(self.ctx.theme)

    def _update_brand(self) -> None:
        c = palette(self.ctx.theme)
        for widget in self.findChildren(QLabel):
            if widget.objectName() == "SectionTitle" and widget.text().startswith("  CAPITAL"):
                widget.setStyleSheet(f"padding-left: 10px; color: {c['accent']};")
        self._refresh_status_bar()

    # ------------------------------------------------------------------
    # menus / status bar
    # ------------------------------------------------------------------

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        import_action = QAction("Import workbook…", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(lambda: self.go_to("import"))
        file_menu.addAction(import_action)

        export_action = QAction("Reports and exports…", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(lambda: self.go_to("reports"))
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        backup_action = QAction("Back up database…", self)
        backup_action.triggered.connect(self._quick_backup)
        file_menu.addAction(backup_action)

        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("&View")
        refresh_action = QAction("Refresh page", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self._refresh_current)
        view_menu.addAction(refresh_action)
        theme_action = QAction("Toggle light / dark theme", self)
        theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(theme_action)

        help_menu = menubar.addMenu("&Help")
        docs_action = QAction("About", self)
        docs_action.triggered.connect(self._about)
        help_menu.addAction(docs_action)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(import_action)
        toolbar.addAction(export_action)
        toolbar.addAction(refresh_action)
        toolbar.addSeparator()
        toolbar.addAction(theme_action)
        self.addToolBar(toolbar)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.status_message = QLabel("Ready")
        bar.addWidget(self.status_message, 1)
        self.summary_label = QLabel("")
        bar.addPermanentWidget(self.summary_label)
        self.mode_badge = Badge("", self.ctx.theme)
        bar.addPermanentWidget(self.mode_badge)

    def _refresh_status_bar(self) -> None:
        try:
            summary = self.ctx.data.summary()
        except Exception:  # noqa: BLE001
            summary = {"stocks": 0, "records": 0, "latest_date": None}
        page = self.stack.currentWidget()
        title = getattr(page, "nav_title", "")
        self.summary_label.setText(
            f"{summary['stocks']:,} symbols · {summary['records']:,} rows · "
            f"latest {summary['latest_date'] or '—'}   |   {title}")
        mode = self.ctx.settings.formula_mode()
        self.mode_badge.setText(mode.upper())
        c = palette(self.ctx.theme)
        colour = c["warning"] if mode == "original" else c["good"]
        self.mode_badge.setStyleSheet(
            f"color: {colour}; border: 1px solid {colour}; border-radius: 7px;"
            f"padding: 1px 7px; font-weight: 600; font-size: 10px;")

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def _refresh_current(self) -> None:
        page = self.stack.currentWidget()
        handler = getattr(page, "refresh", None)
        if callable(handler):
            handler()
        self._refresh_status_bar()
        self.status_message.setText(f"{getattr(page, 'nav_title', 'Page')} refreshed")

    def _quick_backup(self) -> None:
        try:
            path = self.ctx.db.backup()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Backup failed", f"{type(exc).__name__}: {exc}")
            return
        self.ctx.db.log("maintenance", f"Backup written to {path.name}")
        QMessageBox.information(self, "Backup complete", f"Saved to:\n{path}")

    def _about(self) -> None:
        summary = self.ctx.data.summary()
        QMessageBox.information(
            self, f"About {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            f"Local, offline analytics for imported stock-market sessions.\n"
            f"Database: {self.ctx.db.path}\n"
            f"Records: {summary['records']:,} across {summary['stocks']:,} symbols\n"
            f"Formula mode: {self.ctx.settings.formula_mode()}\n\n"
            f"Excel macros are never executed. Calculated metrics are always labelled "
            f"in exports, and the workbook's defects are reproduced only in “original” "
            f"mode — never silently.")

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.settings_page is not None:
            # nothing to flush, but make the shutdown explicit in the log
            try:
                self.ctx.db.log("app", "Application closed",
                                payload={"page": self.current_page_key()})
            except Exception:  # noqa: BLE001
                pass
        event.accept()
