"""The SQL and columnar aggregate paths must return what the pandas path does.

Every dashboard number has two implementations:

* the original pandas one, which is the reference (and stays in charge for
  small databases), and
* the SQL one used once a table is too big to materialise.

These tests force each path and compare the results, so an optimisation can
never silently change a number.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import pytest
from sqlalchemy import select

from database.db import Database
from models import Price, Stock
from services import query_engine
from services.data_service import DataFilters, DataService, _FrameCache
from services.query_engine import Scope
from services.settings_service import SettingsService

METRICS = ("close", "tottrdqty", "delivery_pct")
AGGREGATES = ("sum", "avg", "min", "max", "count")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def pandas_service(database: Database) -> DataService:
    """A service that always materialises a frame (the reference path)."""
    service = DataService(database, SettingsService(database), prefer_duckdb=False)
    service.large_dataset_rows = 10 ** 12
    return service


def sql_service(database: Database) -> DataService:
    """A service that answers every aggregate with SQL."""
    service = DataService(database, SettingsService(database), prefer_duckdb=False)
    service.large_dataset_rows = -1
    return service


def assertion(dimension: str):
    """Sort a group-by result deterministically (ties are arbitrary in pandas)."""
    return lambda frame: frame.sort_values(
        ["value", dimension], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)


def seed(database: Database, rows) -> None:
    with database.session() as session:
        stocks: dict[str, Stock] = {}
        for row in rows:
            stock = stocks.get(row["symbol"])
            if stock is None:
                stock = session.scalar(select(Stock).where(Stock.symbol == row["symbol"]))
                if stock is None:
                    stock = Stock(symbol=row["symbol"], record_count=0)
                    session.add(stock)
                    session.flush()
                stocks[row["symbol"]] = stock
            session.add(Price(
                stock_id=stock.id,
                trade_date=row["trade_date"],
                open=row.get("open"), high=row.get("high"), low=row.get("low"),
                close=row.get("close"), last=row.get("last"),
                prevclose=row.get("prevclose"), tottrdqty=row.get("tottrdqty"),
                tottrdval=row.get("tottrdval"), totaltrades=row.get("totaltrades"),
                delivery_pct=row.get("delivery_pct"),
            ))
            stock.record_count += 1
            stock.first_date = stock.first_date or row["trade_date"]
            stock.last_date = row["trade_date"]
        session.commit()


# ---------------------------------------------------------------------------
# SQLite vs pandas
# ---------------------------------------------------------------------------


def test_sql_overview_matches_pandas(filled_db: Database) -> None:
    expected = pandas_service(filled_db).overview()
    actual = sql_service(filled_db).overview()

    assert actual["total_records"] == expected["total_records"]
    assert actual["unique_stocks"] == expected["unique_stocks"]
    assert actual["earliest_date"] == expected["earliest_date"]
    assert actual["latest_date"] == expected["latest_date"]
    assert actual["symbols"] == expected["symbols"]
    assert actual["total_volume"] == pytest.approx(expected["total_volume"], rel=1e-9)
    assert actual["total_value"] == pytest.approx(expected["total_value"], rel=1e-9)
    assert actual["avg_delivery"] == pytest.approx(expected["avg_delivery"], rel=1e-9)


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("aggregation", AGGREGATES)
def test_sql_trend_matches_pandas(filled_db: Database, metric: str,
                                  aggregation: str) -> None:
    expected = pandas_service(filled_db).trend(metric, aggregation=aggregation)
    actual = sql_service(filled_db).trend(metric, aggregation=aggregation)

    assert list(actual.columns) == ["trade_date", "value"]
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-9)


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("dimension", ("symbol", "trade_date"))
@pytest.mark.parametrize("aggregation", ("sum", "avg", "count", "min", "max"))
def test_sql_group_by_matches_pandas(filled_db: Database, metric: str,
                                     dimension: str, aggregation: str) -> None:
    expected = pandas_service(filled_db).group_by(
        metric, dimension, aggregation=aggregation)
    actual = sql_service(filled_db).group_by(metric, dimension, aggregation=aggregation)

    assert list(actual.columns) == [dimension, "value"]
    order = assertion(dimension)
    pd.testing.assert_frame_equal(
        order(actual), order(expected), check_exact=False, rtol=1e-9)


def test_sql_group_by_honours_limit(filled_db: Database) -> None:
    actual = sql_service(filled_db).group_by("close", "symbol", aggregation="count", limit=3)
    assert len(actual) == 3
    assert list(actual["value"]) == sorted(actual["value"], reverse=True)


def test_sql_movers_matches_pandas(filled_db: Database) -> None:
    expected = pandas_service(filled_db).movers(limit=5)
    actual = sql_service(filled_db).movers(limit=5)
    for side in ("gainers", "losers"):
        pd.testing.assert_frame_equal(
            actual[side], expected[side], check_exact=False, rtol=1e-9)


def test_sql_screener_matches_pandas(filled_db: Database) -> None:
    expected = pandas_service(filled_db).screener()
    actual = sql_service(filled_db).screener()
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-9)


def test_sql_screener_matches_pandas_with_universe(filled_db: Database) -> None:
    universe = ["ACME", "ZZZZ", "RANEENGINE"]
    expected = pandas_service(filled_db).screener(universe=universe)
    actual = sql_service(filled_db).screener(universe=universe)
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-9)


def test_screener_cache_is_keyed_by_the_date_range(db: Database) -> None:
    """A one-day screener must not poison a later full-history request."""
    first, second = _dt.date(2024, 1, 2), _dt.date(2024, 1, 3)
    seed(db, [
        {"symbol": "ACME", "trade_date": first, "close": 100,
         "tottrdqty": 100, "tottrdval": 10_000, "totaltrades": 10,
         "delivery_pct": 40},
        {"symbol": "ACME", "trade_date": second, "close": 110,
         "tottrdqty": 300, "tottrdval": 33_000, "totaltrades": 30,
         "delivery_pct": 60},
    ])
    service = pandas_service(db)
    wide = service.screener(DataFilters()).set_index("symbol")
    narrow = service.screener(
        DataFilters(start_date=second, end_date=second)).set_index("symbol")
    assert wide.at["ACME", "avg_volume"] == pytest.approx(200.0)
    assert narrow.at["ACME", "avg_volume"] == pytest.approx(300.0)


def test_sql_path_respects_filters(filled_db: Database) -> None:
    frame = pandas_service(filled_db).frame()
    start = sorted(frame["trade_date"].unique())[0]
    symbols = sorted(frame["symbol"].unique())[:3]
    filters = DataFilters(symbols=symbols, start_date=start, end_date=start)

    expected_trend = pandas_service(filled_db).trend("close", filters, "sum")
    actual_trend = sql_service(filled_db).trend("close", filters, "sum")
    pd.testing.assert_frame_equal(actual_trend, expected_trend, check_exact=False, rtol=1e-9)

    expected_group = pandas_service(filled_db).group_by(
        "close", "symbol", filters, aggregation="sum")
    actual_group = sql_service(filled_db).group_by(
        "close", "symbol", filters, aggregation="sum")
    order = assertion("symbol")
    pd.testing.assert_frame_equal(
        order(actual_group), order(expected_group), check_exact=False, rtol=1e-9)


def test_unknown_columns_do_not_raise(filled_db: Database) -> None:
    service = sql_service(filled_db)
    assert service.trend("not_a_column").empty
    assert service.group_by("close", "not_a_column").empty


def test_empty_database_aggregates(db: Database) -> None:
    service = sql_service(db)

    overview = service.overview()
    assert overview["total_records"] == 0
    assert overview["symbols"] == []
    assert overview["latest_date"] is None
    assert overview["total_volume"] == 0.0

    assert service.trend("close").empty
    assert service.group_by("close", "symbol").empty
    assert service.movers()["gainers"].empty
    assert service.screener().empty


# ---------------------------------------------------------------------------
# cache bounds
# ---------------------------------------------------------------------------


def test_frame_cache_evicts_least_recently_used() -> None:
    cache = _FrameCache(max_entries=2, max_rows=10_000)
    cache.put("a", pd.DataFrame({"x": range(3)}))
    cache.put("b", pd.DataFrame({"x": range(3)}))
    cache.get("a")                      # 'a' is now the most recently used
    cache.put("c", pd.DataFrame({"x": range(3)}))

    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_frame_cache_respects_the_row_budget() -> None:
    cache = _FrameCache(max_entries=10, max_rows=100)
    cache.put("small", pd.DataFrame({"x": range(10)}))
    cache.put("big", pd.DataFrame({"x": range(95)}))

    # The 10-row frame is evicted to stay inside the 100-row budget.
    assert cache.get("small") is None
    assert cache.get("big") is not None
    assert cache.rows == 95


def test_frame_limit_is_separate_from_the_full_frame(filled_db: Database) -> None:
    service = pandas_service(filled_db)
    limited = service.frame(limit=5)
    assert len(limited) == 5
    assert len(service.frame()) > 5


# ---------------------------------------------------------------------------
# DuckDB mirror (optional dependency)
# ---------------------------------------------------------------------------


requires_duckdb = pytest.mark.skipif(
    not query_engine.duckdb_available(),
    reason="the optional duckdb package is not installed")


@pytest.fixture()
def duck_backend(db: Database, tmp_path):
    seed(db, [
        {"symbol": "AAA", "trade_date": _dt.date(2025, 1, 1), "close": 10.0,
         "prevclose": 9.0, "last": 9.5, "tottrdqty": 100.0, "tottrdval": 1000.0,
         "totaltrades": 10.0, "delivery_pct": 40.0},
        {"symbol": "AAA", "trade_date": _dt.date(2025, 1, 2), "close": 12.0,
         "prevclose": 10.0, "last": 12.5, "tottrdqty": 200.0, "tottrdval": 2400.0,
         "totaltrades": 20.0, "delivery_pct": 50.0},
        {"symbol": "BBB", "trade_date": _dt.date(2025, 1, 2), "close": 5.0,
         "prevclose": 6.0, "last": 5.5, "tottrdqty": 300.0, "tottrdval": 1650.0,
         "totaltrades": 30.0, "delivery_pct": 60.0},
    ])
    backend = query_engine.DuckDbBackend(db, tmp_path / "mirror.duckdb")
    yield backend
    backend.close()


@requires_duckdb
def test_duckdb_matches_sqlite(duck_backend, db: Database) -> None:
    sqlite_backend = query_engine.SqliteBackend(db)

    assert duck_backend.overview() == sqlite_backend.overview()
    pd.testing.assert_frame_equal(
        duck_backend.trend("close"), sqlite_backend.trend("close"),
        check_exact=False, rtol=1e-9)
    pd.testing.assert_frame_equal(
        duck_backend.group_by("close", "symbol", aggregation="sum"),
        sqlite_backend.group_by("close", "symbol", aggregation="sum"),
        check_exact=False, rtol=1e-9)
    for side in ("gainers", "losers"):
        pd.testing.assert_frame_equal(
            duck_backend.movers(_dt.date(2025, 1, 2))[side],
            sqlite_backend.movers(_dt.date(2025, 1, 2))[side],
            check_exact=False, rtol=1e-9)


@requires_duckdb
def test_duckdb_syncs_new_rows(duck_backend, db: Database) -> None:
    assert duck_backend.overview()["total_records"] == 3

    seed(db, [
        {"symbol": "CCC", "trade_date": _dt.date(2025, 1, 3), "close": 7.0,
         "prevclose": 5.0, "last": 7.0, "tottrdqty": 400.0, "tottrdval": 2800.0,
         "totaltrades": 40.0, "delivery_pct": 70.0},
    ])
    duck_backend.invalidate()

    overview = duck_backend.overview()
    assert overview["total_records"] == 4
    assert "CCC" in overview["symbols"]


@requires_duckdb
def test_duckdb_rebuilds_when_rows_disappear(duck_backend, db: Database) -> None:
    assert duck_backend.overview()["total_records"] == 3

    with db.session() as session:
        session.execute(Price.__table__.delete())
        session.commit()
    duck_backend.invalidate()

    assert duck_backend.overview()["total_records"] == 0


@requires_duckdb
def test_duckdb_forces_a_rebuild_on_restore(duck_backend, db: Database) -> None:
    assert duck_backend.overview()["total_records"] == 3

    duck_backend.invalidate(force=True)
    # The mirror was emptied and rebuilt from the same SQLite rows.
    assert duck_backend.overview()["total_records"] == 3


def test_create_backend_falls_back_to_sqlite(db: Database, tmp_path) -> None:
    backend = query_engine.create_backend(db, prefer_duckdb=False)
    assert backend.name == "sqlite"
    assert isinstance(backend, query_engine.SqliteBackend)


@requires_duckdb
def test_scope_filter_sql_is_shared_by_both_backends(duck_backend, db: Database) -> None:
    scope = Scope(symbols=("AAA",))
    assert duck_backend.overview(scope) == query_engine.SqliteBackend(db).overview(scope)
    assert duck_backend.overview(scope)["unique_stocks"] == 1


# ---------------------------------------------------------------------------
# settings wiring
# ---------------------------------------------------------------------------


def test_large_dataset_threshold_comes_from_the_settings(db: Database) -> None:
    settings = SettingsService(db)
    assert settings.large_dataset_rows() == query_engine.LARGE_DATASET_ROWS
    settings.set("analytics.large_dataset_rows", 5)

    service = DataService(db, settings)
    assert service.large_dataset_rows == 5
    assert service.dataset_is_large() is False

    seed(db, [
        {"symbol": f"S{index}", "trade_date": _dt.date(2025, 1, 1), "close": 1.0}
        for index in range(3)
    ])
    service.invalidate()
    assert service.dataset_is_large() is False

    seed(db, [
        {"symbol": f"T{index}", "trade_date": _dt.date(2025, 1, 2), "close": 1.0}
        for index in range(3)
    ])
    service.invalidate()
    assert service.dataset_is_large() is True


def test_settings_can_disable_columnar_analytics(db: Database) -> None:
    settings = SettingsService(db)
    settings.set("analytics.duckdb", False)

    service = DataService(db, settings)
    assert service.backend.name == "sqlite"


@requires_duckdb
def test_settings_choose_the_mirror_location(db: Database, tmp_path) -> None:
    settings = SettingsService(db)
    settings.set("analytics.duckdb", True)
    target = tmp_path / "index" / "mirror.duckdb"
    settings.set("analytics.duckdb_path", str(target))

    service = DataService(db, settings)
    assert service.backend.name == "duckdb"
    assert service.backend.path == target
    service.backend.close()


def test_missing_settings_table_falls_back_to_pandas_defaults(tmp_path) -> None:
    """A database that was never initialised must still be serviceable."""
    bare = Database(tmp_path / "bare.db")
    service = DataService(bare)
    assert service.large_dataset_rows == query_engine.LARGE_DATASET_ROWS
    assert service.frame().empty
