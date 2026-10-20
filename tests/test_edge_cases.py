"""End-to-end edge cases.

Test-analytics.py covers the engine's arithmetic in isolation; this module walks
the same hazards through the *services* the pages actually call: zero
denominators, missing values, duplicate dates, too little history, empty data,
a database without a schema, and settings that do not exist.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import Database
from models import Price, Stock
from services.data_service import DataFilters, DataService
from services.dashboard_service import DashboardService, format_value
from services.export_service import ExportRequest, export
from services.formula_service import FormulaService
from services.settings_service import SettingsService

DATE = _dt.date(2025, 1, 1)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def seed_prices(db: Database, records) -> None:
    """Insert ``Price`` rows directly, creating their ``Stock`` as needed."""
    with db.session() as session:
        stocks: dict = {}
        for record in records:
            symbol = record["symbol"]
            stock = stocks.get(symbol)
            if stock is None:
                stock = session.scalar(select(Stock).where(Stock.symbol == symbol))
                if stock is None:
                    stock = Stock(symbol=symbol, record_count=0)
                    session.add(stock)
                    session.flush()
                stocks[symbol] = stock
            session.add(Price(
                stock_id=stock.id, trade_date=record["trade_date"],
                open=record.get("open"), high=record.get("high"), low=record.get("low"),
                close=record.get("close"), last=record.get("last"),
                prevclose=record.get("prevclose"), tottrdqty=record.get("tottrdqty"),
                tottrdval=record.get("tottrdval"), totaltrades=record.get("totaltrades"),
                delivery_pct=record.get("delivery_pct"),
            ))
            stock.record_count += 1
            stock.first_date = stock.first_date or record["trade_date"]
            stock.last_date = record["trade_date"]


def make_db(tmp_path: Path, records) -> Database:
    database = Database(tmp_path / "edge.db")
    database.init_schema()
    seed_prices(database, records)
    return database


# --------------------------------------------------------------------------
# zero denominators
# --------------------------------------------------------------------------


def test_pct_change_is_blank_when_the_previous_close_is_zero(tmp_path: Path) -> None:
    db = make_db(tmp_path, [{"symbol": "ZERO", "trade_date": DATE, "close": 10.0,
                             "last": 11.0, "prevclose": 0.0}])
    row = DataService(db).metrics("ZERO").iloc[0]
    assert row["pct_change"] is None                # never a divide-by-zero error


def test_pct_change_is_blank_when_the_previous_close_is_missing(tmp_path: Path) -> None:
    db = make_db(tmp_path, [{"symbol": "GAP", "trade_date": DATE, "close": 10.0,
                             "last": 11.0, "prevclose": None}])
    assert DataService(db).metrics("GAP").iloc[0]["pct_change"] is None


def test_action_is_blank_when_there_were_no_trades(tmp_path: Path) -> None:
    db = make_db(tmp_path, [{"symbol": "QUIET", "trade_date": DATE, "close": 10.0,
                             "tottrdqty": 100.0, "totaltrades": 0.0}])
    assert DataService(db).metrics("QUIET").iloc[0]["action"] is None


def test_a_formula_divides_by_zero_safely(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    formulas = FormulaService(db)
    plain = formulas.create(name="raw_div", expression="CLOSE / 0", output_column="raw_div")
    guarded = formulas.create(name="safe_div", expression="SAFE_DIV(CLOSE, 0)",
                              output_column="safe_div")
    frame = pd.DataFrame([{"close": 10.0}])
    out, results = formulas.apply(frame, formulas=[plain, guarded])
    assert out["raw_div"].iloc[0] is None or pd.isna(out["raw_div"].iloc[0])
    assert out["safe_div"].iloc[0] is None or pd.isna(out["safe_div"].iloc[0])
    assert all(result["ok"] for result in results)


# --------------------------------------------------------------------------
# missing values
# --------------------------------------------------------------------------


def test_avg_price_needs_every_component(tmp_path: Path) -> None:
    db = make_db(tmp_path, [{"symbol": "HALF", "trade_date": DATE, "open": 10.0,
                             "high": 12.0, "low": 9.0, "close": None}])
    assert DataService(db).metrics("HALF").iloc[0]["avg_price"] is None


def test_deliverable_quantity_needs_both_inputs(tmp_path: Path) -> None:
    db = make_db(tmp_path, [
        {"symbol": "NOQTY", "trade_date": DATE, "close": 10.0, "tottrdqty": None,
         "delivery_pct": 50.0},
        {"symbol": "NODELIV", "trade_date": DATE, "close": 10.0, "tottrdqty": 100.0,
         "delivery_pct": None},
    ])
    data = DataService(db)
    assert data.metrics("NOQTY").iloc[0]["deliverable_qty"] is None
    assert data.metrics("NODELIV").iloc[0]["deliverable_qty"] is None


def test_screener_averages_are_blank_when_nothing_matches(tmp_path: Path) -> None:
    # a single session with no quantity: AVERAGEIF finds no numeric cell
    db = make_db(tmp_path, [{"symbol": "EMPTYQ", "trade_date": DATE, "close": 10.0,
                             "tottrdqty": None, "delivery_pct": None}])
    row = DataService(db).screener().iloc[0]
    assert row["avg_volume"] is None or pd.isna(row["avg_volume"])
    assert row["today_volume"] == 0.0                # SUMIFS of nothing is 0, like Excel


def test_missing_values_export_as_blanks(tmp_path: Path) -> None:
    frame = pd.DataFrame([{"symbol": "GAP", "close": None, "avg_price": None}])
    result = export(ExportRequest(title="Gaps", frame=frame,
                                  path=tmp_path / "gaps.csv", format="csv"))
    data_row = next(line for line in result.path.read_text(encoding="utf-8-sig").splitlines()
                    if line.startswith("GAP"))
    assert data_row == "GAP,,"


def test_missing_kpi_renders_as_not_available(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    dashboard = DashboardService(db, DataService(db))
    component = dashboard.add_component("kpi", "Nothing", "close", aggregation="avg",
                                        source="prices")
    payload = dashboard.render(component)
    assert payload.value is None
    assert payload.text == "n/a"
    assert payload.status in ("neutral", "", None)


# --------------------------------------------------------------------------
# duplicate dates
# --------------------------------------------------------------------------


def test_a_duplicate_date_cannot_be_inserted_directly(tmp_path: Path) -> None:
    db = Database(tmp_path / "dup.db")
    db.init_schema()
    with db.session() as session:
        stock = Stock(symbol="X", record_count=1)
        session.add(stock)
        session.flush()
        session.add(Price(stock_id=stock.id, trade_date=DATE, close=1.0))
        session.flush()
        session.add(Price(stock_id=stock.id, trade_date=DATE, close=2.0))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_duplicate_policies_resolve_a_repeated_date(tmp_path: Path) -> None:
    import openpyxl

    from services.excel_import import ImportService, auto_map_columns

    headers = ["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE",
               "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %"]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(headers)
    sheet.append(["ACME", DATE, 100, 110, 90, 105, 106, 100, 1000, 105000, 50, 40])
    sheet.append(["ACME", DATE, 100, 110, 90, 999, 999, 100, 1000, 105000, 50, 40])
    path = tmp_path / "dup.xlsx"
    workbook.save(path)

    skipped_db = Database(tmp_path / "skip.db")
    skipped_db.init_schema()
    report = ImportService(skipped_db).run(path, "Data", auto_map_columns(headers),
                                           duplicate_policy="skip")
    assert (report.rows_inserted, report.rows_skipped) == (1, 1)
    with skipped_db.session() as session:
        assert session.scalar(select(Price)).close == 105.0        # the first wins

    updated_db = Database(tmp_path / "update.db")
    updated_db.init_schema()
    report = ImportService(updated_db).run(path, "Data", auto_map_columns(headers),
                                           duplicate_policy="update")
    assert (report.rows_inserted, report.rows_updated) == (1, 1)
    with updated_db.session() as session:
        assert session.scalar(select(Price)).close == 999.0        # the last wins


# --------------------------------------------------------------------------
# insufficient history
# --------------------------------------------------------------------------


def test_a_short_history_still_produces_every_window(tmp_path: Path) -> None:
    db = make_db(tmp_path, [
        {"symbol": "NEW", "trade_date": _dt.date(2025, 1, day), "close": 10.0 + day,
         "last": 10.0 + day, "tottrdqty": 100.0 * day}
        for day in (1, 2)
    ])
    data = DataService(db)
    rows = data.lookback("NEW")
    assert [row["window"] for row in rows] == ["1D", "3D", "5D", "7D", "10D"]
    # only two sessions exist, so the windows report what they actually used
    assert rows[0]["window_used"] == 1
    assert rows[-1]["window_used"] == 2


def test_the_original_volume_window_reports_its_error(tmp_path: Path) -> None:
    db = make_db(tmp_path, [
        {"symbol": "NEW", "trade_date": _dt.date(2025, 1, day), "close": 10.0,
         "tottrdqty": 100.0}
        for day in range(1, 7)
    ])
    metrics = DataService(db).metrics("NEW")
    # in original mode the offset height goes negative from the fifth row on,
    # which Excel reports as #VALUE!; we surface that as vol_ma_error
    assert bool(metrics["vol_ma_error"].iloc[4:].all())
    assert not metrics["vol_ma_error"].iloc[:4].any()


def test_metrics_on_a_symbol_with_no_rows_is_empty(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    assert DataService(db).metrics("GHOST").empty


# --------------------------------------------------------------------------
# empty data
# --------------------------------------------------------------------------


def test_every_service_survives_an_empty_database(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    data = DataService(db)

    assert data.screener().empty
    assert data.metrics("NONE").empty
    assert data.lookback("NONE") == []
    assert data.classification() == {"D. Ind": {}, "DV1": {}, "DV2": {}}
    assert data.trend("close").empty
    assert data.group_by("close", "symbol").empty
    assert data.movers()["gainers"].empty
    assert data.overview()["total_records"] == 0


def test_reports_and_exports_survive_an_empty_database(tmp_path: Path) -> None:
    from services.report_service import ReportService

    db = make_db(tmp_path, [])
    reports = ReportService(db, DataService(db), FormulaService(db))
    report = reports.build("screener")
    assert report.frame.empty
    result = reports.export(report, tmp_path / "empty.xlsx", fmt="xlsx")
    assert result.rows == 0
    assert result.path.exists()


def test_formulas_on_an_empty_frame_are_a_no_op(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    out, results = FormulaService(db).apply(pd.DataFrame())
    assert out.empty
    assert results == []


def test_dashboard_over_an_empty_database_renders_without_error(tmp_path: Path) -> None:
    db = make_db(tmp_path, [])
    payloads = DashboardService(db, DataService(db)).render_all()
    assert payloads
    assert all(payload.error is None for payload in payloads)


# --------------------------------------------------------------------------
# a database without a schema
# --------------------------------------------------------------------------


def test_schema_less_database_is_tolerated(tmp_path: Path) -> None:
    database = Database(tmp_path / "bare.db")          # created, never initialised

    assert database.symbol_list() == []
    frame = database.price_frame()
    assert frame.empty
    assert "symbol" in frame.columns
    counts = database.summary_counts()
    assert counts["records"] == 0 and counts["stocks"] == 0


def test_data_service_over_a_schema_less_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "bare2.db")
    data = DataService(database)
    assert data.symbols() == []
    assert data.latest_date() is None
    assert data.frame().empty
    assert data.summary()["records"] == 0


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def test_unknown_settings_fall_back_to_the_default(settings: SettingsService) -> None:
    assert settings.get("nothing.here", "fallback") == "fallback"
    assert settings.get("display.theme") in ("dark", "light")


def test_settings_reject_an_invalid_choice(settings: SettingsService) -> None:
    with pytest.raises(ValueError):
        settings.set("display.theme", "neon")
    with pytest.raises(ValueError):
        settings.set("formulas.mode", "sideways")


def test_settings_require_a_section_and_a_key(settings: SettingsService) -> None:
    with pytest.raises(ValueError):
        settings.set("theme", "dark")


def test_settings_round_trip_and_persist(db: Database) -> None:
    SettingsService(db).set("display.theme", "light")
    assert SettingsService(db).get("display.theme") == "light"


def test_path_settings_resolve(tmp_path: Path, settings: SettingsService) -> None:
    settings.set("export.directory", str(tmp_path / "out"))
    assert settings.export_dir() == tmp_path / "out"
    assert settings.database_path().name.endswith(".db")


def test_mode_setting_switches_the_engine(settings: SettingsService,
                                          tiny_frame: pd.DataFrame) -> None:
    from services.analytics import AnalyticsParams, compute_symbol_metrics

    settings.set("formulas.mode", "corrected")
    assert settings.formula_mode() == "corrected"

    original = compute_symbol_metrics(tiny_frame, AnalyticsParams(mode="original"))
    corrected = compute_symbol_metrics(tiny_frame, AnalyticsParams(mode="corrected"))
    # the original mode's forward-looking window has a different width
    assert original["vol_ma_window"].tolist() != corrected["vol_ma_window"].tolist()


# --------------------------------------------------------------------------
# caching after a change
# --------------------------------------------------------------------------


def test_import_then_invalidate_refreshes_every_view(tmp_path: Path) -> None:
    import openpyxl

    from services.excel_import import ImportService, auto_map_columns

    db = Database(tmp_path / "refresh.db")
    db.init_schema()
    data = DataService(db)
    assert data.summary()["records"] == 0

    headers = ["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE",
               "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %"]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(headers)
    sheet.append(["ACME", DATE, 100, 110, 90, 105, 106, 100, 1000, 105000, 50, 40])
    path = tmp_path / "one.xlsx"
    workbook.save(path)

    ImportService(db).run(path, "Data", auto_map_columns(headers), duplicate_policy="skip")
    data.invalidate("edge test import")
    assert data.summary()["records"] == 1
    assert data.symbols() == ["ACME"]


# --------------------------------------------------------------------------
# display formatting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (None, "n/a"),
    (0, "0.00"),
    (1234.5, "1,234.50"),
    (True, "Yes"),
    (False, "No"),
])
def test_format_value_edge_cases(value, expected) -> None:
    assert format_value(value, None) == expected


def test_format_value_rounds_half_away_from_zero() -> None:
    assert format_value(1234.5, "#,##0") == "1,235"     # not Python's "1,234"
    assert format_value(0.125, "0.00") == "0.13"


def test_filters_describe_handles_no_filters() -> None:
    described = DataFilters().describe()
    assert described["symbols"] == "all symbols"
