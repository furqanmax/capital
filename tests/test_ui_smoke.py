"""Headless GUI smoke test.

Constructs the real main window against the imported sample data with Qt's
``offscreen`` platform plugin (set in ``conftest``), then walks the navigation.
A page that cannot be built or refreshed fails here rather than in front of a
user.

Marked ``gui``:  pytest -m gui
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def window(qapp, filled_db):
    from ui.context import AppContext
    from ui.main_window import MainWindow, teardown

    context = AppContext.create(db_path=filled_db.path)
    main = MainWindow(context)
    yield main
    # QtWebEngine aborts the process if a page outlives its profile
    teardown(main, qapp)


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------


def test_window_registers_every_page(window) -> None:
    from ui.pages import PAGE_CLASSES

    assert window.nav.count() == len(PAGE_CLASSES)
    assert set(window.page_index) == {key for key, _ in PAGE_CLASSES}
    assert window.stack.count() == len(PAGE_CLASSES)
    for _, page_class in PAGE_CLASSES:
        assert page_class.nav_title
        assert page_class.nav_icon


def test_pages_are_wired_to_the_window(window) -> None:
    for page in window.pages:
        assert page.ctx is window.ctx
        assert hasattr(page, "refresh")
        assert hasattr(page, "activate")


def test_dashboard_is_the_default_page(window) -> None:
    assert window.page_index["dashboard"] == 0
    assert window.current_page_key() == "dashboard"


# --------------------------------------------------------------------------
# navigation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["dashboard", "explorer", "history", "formula_lab",
                                 "reports", "import", "settings", "audit"])
def test_navigating_to_each_page(window, key: str) -> None:
    window.go_to(key)
    assert window.stack.currentIndex() == window.page_index[key]
    assert window.current_page_key() == key
    assert window.stack.currentWidget() is window.pages[window.page_index[key]]


def test_unknown_page_falls_back_to_the_dashboard(window) -> None:
    window.go_to("does-not-exist")
    assert window.current_page_key() == "dashboard"


# --------------------------------------------------------------------------
# refreshing
# --------------------------------------------------------------------------


def test_every_page_refreshes_without_raising(window) -> None:
    for page in window.pages:
        page.refresh()          # must never raise: a page cannot kill the window


def test_activate_loads_every_page_once(window) -> None:
    for page in window.pages:
        page.activate()         # first call -> load(); later calls -> refresh()


# --------------------------------------------------------------------------
# theme
# --------------------------------------------------------------------------


def test_theme_toggle_round_trips(window) -> None:
    original = window.ctx.theme
    try:
        window.toggle_theme()
        assert window.ctx.theme != original
        assert window.ctx.settings.get("display.theme") == window.ctx.theme
    finally:
        window.toggle_theme()
    assert window.ctx.theme == original


def test_status_bar_reports_page_messages(window) -> None:
    window._on_status("hello from a page")
    assert "hello" in window.status_message.text()


def test_tables_format_numbers_like_excel(qapp) -> None:
    import pandas as pd

    from ui.widgets import DataFrameModel

    model = DataFrameModel(pd.DataFrame({"x": [0.125]}))
    assert model.format(0.125) == "0.13"          # not Python's "0.12"
    assert model.format(float("nan")) == ""
    assert model.format(None) == ""
    assert model.format("ACME") == "ACME"
