#!/usr/bin/env python3
"""Capital Analytics — desktop entry point.

    python3 main.py                 # launch the desktop application
    python3 main.py --check         # headless self-test: build the database,
                                    # import the bundled workbook and print a summary
    python3 main.py --db other.db   # use a different database file

Nothing here executes a spreadsheet macro: workbooks are only ever read.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

APP_NAME = "Capital Analytics"
APP_VERSION = "1.0.0"

SAMPLE_WORKBOOK = "Saif-Stock Market Data File-Sample.xlsm"

log = logging.getLogger("capital.main")


def default_workbook() -> Path:
    """The bundled sample workbook, wherever this build keeps it.

    A source checkout keeps it in ``data/``; a PyInstaller build may keep it in
    the bundle (``_MEIPASS``) or beside the executable.  The first one that
    exists wins, so ``--check`` works the same either way.
    """
    roots = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    roots.append(PROJECT_ROOT)
    if getattr(sys, "frozen", False):                       # pragma: no cover - packaging
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        candidate = root / "data" / SAMPLE_WORKBOOK
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / "data" / SAMPLE_WORKBOOK


def build_context(db_path: str | None, theme: str | None, verbose: bool = False):
    """Create the shared application context (database + services)."""
    from services.logging_setup import configure_logging
    from ui.context import AppContext

    ctx = AppContext.create(db_path=db_path, theme=theme)
    configure_logging(ctx.settings, force=True, to_stderr=verbose)
    log.info("Context ready (database=%s, theme=%s)", ctx.db.path, ctx.theme)
    return ctx


def run_gui(ctx) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.main_window import APP_NAME as NAME, APP_VERSION as VERSION, MainWindow, teardown

    # QtWebEngine requires shared OpenGL contexts; set it before the app exists.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Capital")

    window = MainWindow(ctx)
    window.show()
    log.info("Main window shown")
    try:
        return app.exec()
    finally:
        # never leave a QWebEnginePage alive while Qt tears down the profile
        teardown(window, app)


def run_check(db_path: str | None, workbook: str | None) -> int:
    """Headless self-test used by the test-suite and by packagers."""
    from services.data_service import DataFilters
    from services.excel_import import ImportService, auto_map_columns, preview_sheet
    from services.settings_service import SettingsService
    from ui.context import AppContext

    ctx = AppContext.create(db_path=db_path)
    SettingsService(ctx.db).set("logging.level", "INFO")

    target = Path(workbook) if workbook else default_workbook()

    print(f"{APP_NAME} {APP_VERSION} — self-test")
    print(f"  database : {ctx.db.path}")
    print(f"  workbook : {target}")

    if target.exists():
        payload = preview_sheet(target, "Data", rows=2)
        mapping = auto_map_columns(payload["headers"])
        report = ImportService(ctx.db).run(target, "Data", mapping,
                                           duplicate_policy="skip",
                                           header_row=payload["header_row"])
        print(f"  import   : {report.rows_read} read, {report.rows_inserted} new, "
              f"{report.rows_updated} updated, {report.rows_skipped} skipped, "
              f"{report.rows_rejected} rejected")
        ctx.invalidate("self-test import")
    else:
        print("  import   : workbook not found — skipped")

    summary = ctx.data.summary()
    print(f"  dataset  : {summary['stocks']:,} symbols · {summary['records']:,} rows · "
          f"{summary['earliest_date']} → {summary['latest_date']}")

    screener = ctx.data.screener()
    print(f"  screener : {len(screener):,} rows")
    lookback = ctx.data.lookback(ctx.data.symbols()[0]) if ctx.data.symbols() else []
    print(f"  lookback : {len(lookback)} windows for the first symbol")

    reports = ctx.reports.build("dashboard", DataFilters())
    print(f"  reports  : {reports.title} ({reports.rows} rows)")
    print("  result   : OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capital", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="path to the SQLite database file")
    parser.add_argument("--theme", choices=("dark", "light"), help="override the theme")
    parser.add_argument("--check", action="store_true",
                       help="run the headless self-test instead of the GUI")
    parser.add_argument("--workbook", help="workbook used by --check")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="also log to stderr")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args(argv)

    try:
        if args.check:
            return run_check(args.db, args.workbook)
        ctx = build_context(args.db, args.theme, verbose=args.verbose)
        return run_gui(ctx)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # noqa: BLE001 - a startup failure must be legible
        logging.basicConfig(level=logging.DEBUG)
        log.exception("Fatal error during start-up")
        print(f"{APP_NAME} could not start: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Run with -v for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
