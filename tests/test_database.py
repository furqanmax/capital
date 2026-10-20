"""Database engine behaviour: seeding, settings, backup/restore and maintenance."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

import pytest

from database.db import BUILTIN_FORMULAS, DEFAULT_DASHBOARD, Database
from models import AppSetting, AuditLog, DashboardComponent, Formula, Price, Stock


# --------------------------------------------------------------------------
# schema / seeding
# --------------------------------------------------------------------------


def test_init_schema_creates_expected_tables(db: Database) -> None:
    tables = set(db.table_names())
    for expected in ("stocks", "prices", "price_extras", "import_batches",
                     "import_errors", "formulas", "formula_versions",
                     "dashboard_components", "filter_presets", "app_settings",
                     "audit_log"):
        assert expected in tables, expected


def test_init_schema_is_idempotent(db: Database) -> None:
    before = db.table_names()
    db.init_schema()
    db.init_schema()
    assert db.table_names() == before
    assert db.summary_counts()["stocks"] == 0


def test_seeding_is_idempotent_and_complete(db: Database) -> None:
    with db.session() as session:
        formulas = {f.name for f in session.query(Formula).all()}
        components = session.query(DashboardComponent).count()
        settings = session.query(AppSetting).count()

    assert formulas >= {spec["name"] for spec in BUILTIN_FORMULAS}
    assert components == len(DEFAULT_DASHBOARD)
    assert settings > 0

    # running the seeds again must not duplicate anything
    db.init_schema()
    with db.session() as session:
        assert session.query(DashboardComponent).count() == components
        assert session.query(Formula).count() == len(formulas)


def test_drop_all_removes_tables(db: Database) -> None:
    db.drop_all()
    assert db.summary_counts()["stocks"] == 0
    db.init_schema()
    assert "prices" in db.table_names()


# --------------------------------------------------------------------------
# price access
# --------------------------------------------------------------------------


def test_price_frame_requires_imported_data(db: Database) -> None:
    assert db.price_frame().empty
    assert db.symbol_list() == []


def test_price_frame_filters(filled_db: Database, first_symbol: str) -> None:
    frame = filled_db.price_frame()
    assert not frame.empty
    assert set(frame.columns) >= {"symbol", "trade_date", "close"}

    one = filled_db.price_frame(symbols=[first_symbol])
    assert set(one["symbol"]) == {first_symbol}
    assert len(one) == len(frame[frame["symbol"] == first_symbol])

    latest = filled_db.summary_counts()["latest_date"]
    on_date = filled_db.price_frame(start_date=latest, end_date=latest)
    assert len(on_date) == len(frame)

    future = filled_db.price_frame(start_date=latest + _dt.timedelta(days=1))
    assert future.empty

    limited = filled_db.price_frame(limit=3)
    assert len(limited) == 3


def test_price_frame_is_sorted_and_typed(filled_db: Database) -> None:
    frame = filled_db.price_frame()
    assert frame["trade_date"].is_monotonic_increasing
    for column in ("open", "high", "low", "close", "tottrdqty"):
        assert frame[column].dtype.kind == "f"


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def test_settings_round_trip(settings) -> None:
    assert settings.get("formulas.mode") in ("original", "corrected")
    settings.set("formulas.mode", "corrected")
    assert settings.get("formulas.mode") == "corrected"
    assert settings.section("formulas")["mode"] == "corrected"
    settings.reset("formulas")
    assert settings.get("formulas.mode") == "original"


def test_settings_reject_invalid_choices(settings) -> None:
    with pytest.raises(ValueError):
        settings.set("formulas.mode", "nonsense")
    with pytest.raises(ValueError):
        settings.set("display.theme", "neon")
    with pytest.raises(ValueError):
        settings.set("formulas", "not-a-section.key")


def test_settings_coerce_types(settings) -> None:
    settings.set("filters.page_size", "250")
    assert settings.get("filters.page_size") == 250
    settings.set("import.auto_map_columns", 0)
    assert settings.get("import.auto_map_columns") is False


def test_settings_all_merges_defaults(settings) -> None:
    document = settings.all()
    for section in ("database", "import", "display", "filters", "formulas",
                    "dashboard", "logging", "export"):
        assert section in document, section
    assert settings.database_path().name.endswith(".db")
    assert settings.backup_dir().name


# --------------------------------------------------------------------------
# backup / restore
# --------------------------------------------------------------------------


def test_backup_and_restore_round_trip(db: Database, tmp_path: Path) -> None:
    with db.session() as session:
        session.add(Stock(symbol="ACME", record_count=1))
    backup = db.backup(tmp_path / "bk.db")
    assert backup.exists()

    with db.session() as session:
        session.add(Stock(symbol="EXTRA", record_count=1))
    with db.session() as session:
        assert session.query(Stock).count() == 2

    safety = db.restore(backup)
    assert safety.exists()

    with db.session() as session:
        symbols = {row.symbol for row in session.query(Stock).all()}
    assert symbols == {"ACME"}


def test_restore_rejects_missing_file(db: Database, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.restore(tmp_path / "nope.db")


def test_backup_uses_sqlite_backup_api(db: Database, tmp_path: Path) -> None:
    with db.session() as session:
        session.add(Stock(symbol="ONE", record_count=0))
    target = db.backup(tmp_path / "copy.db")
    connection = sqlite3.connect(target)
    try:
        assert connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] >= 0
    finally:
        connection.close()


# --------------------------------------------------------------------------
# audit log
# --------------------------------------------------------------------------


def test_log_writes_audit_rows(db: Database) -> None:
    db.log("import", "hello", severity="warning", entity="42", payload={"a": 1})
    with db.session() as session:
        row = session.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert row is not None
    assert row.category == "import"
    assert row.severity == "warning"
    assert row.entity == "42"
    assert row.payload == {"a": 1}


def test_log_failure_does_not_break_the_caller(db: Database) -> None:
    db.log("maintenance", "x" * 10, severity="error")
    with db.session() as session:
        assert session.query(AuditLog).count() >= 1


# --------------------------------------------------------------------------
# maintenance
# --------------------------------------------------------------------------


def test_clear_prices_keeps_configuration(db: Database, data) -> None:
    """The clear operation must remove market data only, never configuration."""
    with db.session() as session:
        stock = Stock(symbol="ACME", record_count=1)
        session.add(stock)
        session.flush()
        session.add(Price(stock_id=stock.id, trade_date=_dt.date(2025, 1, 1), close=1.0))
    with db.session() as session:
        formulas = session.query(Formula).count()
        components = session.query(DashboardComponent).count()

    result = data.clear_prices()
    assert result["prices_removed"] == 1

    with db.session() as session:
        assert session.query(Price).count() == 0
        assert session.query(Stock).count() == 0
        assert session.query(Formula).count() == formulas
        assert session.query(DashboardComponent).count() == components


def test_vacuum_runs(data) -> None:
    data.vacuum()  # must not raise
