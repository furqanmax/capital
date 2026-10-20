"""Shared pytest fixtures.

The suite is designed to run without the GUI: it exercises the database, the
importer, the analytics engine, the formula sandbox, the dashboard service and
the exporters directly, plus a headless construction of the main window.

The bundled workbook is used as a fixture when it is present; tests that need it
are skipped (not failed) when it is missing, so the suite stays useful in a fresh
checkout.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WORKBOOK = PROJECT_ROOT / "data" / "Saif-Stock Market Data File-Sample.xlsm"

#: Qt pages are exercised headlessly; a real display is never required.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl.*")

from database.db import Database  # noqa: E402
from services.data_service import DataFilters, DataService  # noqa: E402
from services.excel_import import (  # noqa: E402
    ImportService,
    auto_map_columns,
    preview_sheet,
)
from services.settings_service import SettingsService  # noqa: E402


@pytest.fixture(scope="session")
def workbook_path() -> Path:
    if not WORKBOOK.exists():
        pytest.skip(f"sample workbook not available: {WORKBOOK}")
    return WORKBOOK


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """An empty database with the schema and seeds in place."""
    database = Database(tmp_path / "capital-test.db")
    database.init_schema()
    return database


@pytest.fixture(scope="session")
def filled_db(tmp_path_factory: pytest.TempPathFactory, workbook_path: Path) -> Database:
    """A database containing the sample workbook's ``Data`` sheet."""
    path = tmp_path_factory.mktemp("data") / "capital-filled.db"
    database = Database(path)
    database.init_schema()
    payload = preview_sheet(workbook_path, "Data", rows=1)
    mapping = auto_map_columns(payload["headers"])
    report = ImportService(database).run(
        workbook_path, "Data", mapping, duplicate_policy="skip",
        header_row=payload["header_row"])
    assert report.rows_inserted > 0, report.summary()
    return database


@pytest.fixture()
def settings(db: Database) -> SettingsService:
    return SettingsService(db)


@pytest.fixture()
def data(db: Database) -> DataService:
    return DataService(db, SettingsService(db))


@pytest.fixture(scope="session")
def filled_settings(filled_db: Database) -> SettingsService:
    return SettingsService(filled_db)


@pytest.fixture(scope="session")
def filled_data(filled_db: Database) -> DataService:
    return DataService(filled_db, SettingsService(filled_db))


@pytest.fixture()
def ctx(filled_db: Database):
    """A full :class:`AppContext` over the imported sample data."""
    from ui.context import AppContext

    return AppContext.create(db_path=filled_db.path)


@pytest.fixture()
def filters() -> DataFilters:
    return DataFilters()


@pytest.fixture()
def first_symbol(filled_data: DataService) -> str:
    symbols = filled_data.symbols()
    if not symbols:
        pytest.skip("no symbols in the sample data")
    return symbols[0]


@pytest.fixture()
def tiny_frame():
    """A two-session frame used by the engine tests."""
    import pandas as pd

    return pd.DataFrame([
        {"symbol": "TEST", "trade_date": pd.Timestamp("2025-01-01").date(),
         "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "last": 106.0,
         "prevclose": 100.0, "tottrdqty": 1000.0, "tottrdval": 105000.0,
         "totaltrades": 50.0, "delivery_pct": 40.0},
        {"symbol": "TEST", "trade_date": pd.Timestamp("2025-01-02").date(),
         "open": 106.0, "high": 112.0, "low": 104.0, "close": 110.0, "last": 111.0,
         "prevclose": 106.0, "tottrdqty": 1200.0, "tottrdval": 132000.0,
         "totaltrades": 60.0, "delivery_pct": 55.0},
    ])
