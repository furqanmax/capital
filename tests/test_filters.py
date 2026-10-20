"""``DataService``: the cached query layer every page shares.

The bundled workbook gives 191 symbols on a single session (2025-01-01), which
makes the aggregates easy to reason about; the empty-database cases live here
too because a fresh install must not crash.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import pytest

from database.db import Database
from services.analytics import COLUMN_LABELS
from services.data_service import DataFilters, DataService
from services.excel_import import ImportService, auto_map_columns, preview_sheet
from services.settings_service import SettingsService

SESSION = _dt.date(2025, 1, 1)


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------


def test_filters_normalise_symbols() -> None:
    filters = DataFilters(symbols=["b", "a", "  ", "b"]).normalised()
    assert filters.symbols == ["A", "B"]


def test_filters_describe_is_human_readable() -> None:
    described = DataFilters(symbols=["A"], start_date=SESSION).describe()
    assert described["symbols"] == "A"
    assert described["start_date"] == "2025-01-01"
    assert described["start_date"] == SESSION.isoformat()
    assert DataFilters().describe()["symbols"] == "all symbols"


# --------------------------------------------------------------------------
# the empty database
# --------------------------------------------------------------------------


def test_empty_database_reports_nothing(data: DataService) -> None:
    assert data.symbols() == []
    assert data.dates() == []
    assert data.latest_date() is None
    assert data.summary()["records"] == 0


def test_empty_database_returns_empty_frames(data: DataService) -> None:
    assert data.frame().empty
    frame = data.frame(DataFilters(symbols=["ACME"]))
    assert frame.empty
    assert list(frame.columns)

    assert data.metrics("ACME").empty
    assert data.lookback("ACME") == []
    assert data.screener().empty
    assert data.trend("close").empty
    assert data.group_by("close", "symbol").empty
    assert data.movers()["gainers"].empty


def test_empty_database_overview_is_zeroed(data: DataService) -> None:
    overview = data.overview()
    assert overview["unique_stocks"] == 0
    assert overview["total_records"] == 0
    assert overview["latest_date"] is None


# --------------------------------------------------------------------------
# the sample data
# --------------------------------------------------------------------------


def test_symbols_and_dates_are_loaded(filled_data: DataService) -> None:
    symbols = filled_data.symbols()
    assert len(symbols) == 191
    assert symbols == sorted(symbols)
    assert filled_data.dates() == [SESSION]
    assert filled_data.latest_date() == SESSION


def test_summary_matches_the_import(filled_data: DataService) -> None:
    summary = filled_data.summary()
    assert summary["stocks"] == 191
    assert summary["records"] == 191
    assert summary["earliest_date"] == summary["latest_date"] == SESSION
    assert summary["batches"] == 1


def test_frame_has_the_canonical_columns(filled_data: DataService) -> None:
    frame = filled_data.frame()
    assert len(frame) == 191
    for column in ("symbol", "trade_date", "open", "high", "low", "close", "last",
                   "prevclose", "tottrdqty", "tottrdval", "totaltrades", "delivery_pct"):
        assert column in frame.columns, column
    assert set(frame["symbol"]) == set(filled_data.symbols())


def test_frame_can_be_filtered_by_symbol_and_date(filled_data: DataService,
                                                  first_symbol: str) -> None:
    one = filled_data.frame(DataFilters(symbols=[first_symbol.lower()]))
    assert set(one["symbol"]) == {first_symbol}

    within = filled_data.frame(DataFilters(start_date=SESSION, end_date=SESSION))
    assert len(within) == 191

    before = filled_data.frame(DataFilters(end_date=_dt.date(2020, 1, 1)))
    assert before.empty


def test_symbol_frame_is_the_single_symbol_slice(filled_data: DataService,
                                                 first_symbol: str) -> None:
    frame = filled_data.symbol_frame(first_symbol)
    assert len(frame) == 1
    assert frame["symbol"].iloc[0] == first_symbol


def test_metrics_add_every_documented_column(filled_data: DataService,
                                             first_symbol: str) -> None:
    metrics = filled_data.metrics(first_symbol)
    assert len(metrics) == 1
    for column in ("symbol", "trade_date", "open", "high", "low", "close", "last",
                   "prevclose", "tottrdqty", "tottrdval", "totaltrades", "delivery_pct",
                   "deliverable_qty", "avg_price", "pct_change", "action", "vol_ma",
                   "vol_ma_window", "vol_ma_error", "vol_level", "v1", "badge_bronze",
                   "badge_silver", "badge_gold", "vol_badge", "trade_indicator"):
        assert column in metrics.columns, column
    row = metrics.iloc[0]
    assert row["symbol"] == first_symbol
    assert row["trade_date"] == SESSION
    # columns the wider catalogue documents but which only a lookback row carries
    for derived in ("cmp", "avg_price_avg", "delivery_avg", "action_avg"):
        assert derived in COLUMN_LABELS


def test_metrics_agree_with_the_raw_row(filled_data: DataService, first_symbol: str) -> None:
    raw = filled_data.symbol_frame(first_symbol).iloc[0]
    row = filled_data.metrics(first_symbol).iloc[0]
    assert row["avg_price"] == pytest.approx(
        (raw["open"] + raw["high"] + raw["low"] + raw["close"]) / 4.0)
    if raw["tottrdqty"] is not None and raw["delivery_pct"] is not None:
        assert row["deliverable_qty"] == pytest.approx(
            raw["delivery_pct"] * raw["tottrdqty"] / 100.0)


def test_lookback_returns_every_window(filled_data: DataService,
                                       first_symbol: str) -> None:
    rows = filled_data.lookback(first_symbol)
    assert [row["window"] for row in rows] == ["1D", "3D", "5D", "7D", "10D"]
    # the sample has a single session, so every window is one session wide
    assert all(row["window_used"] == 1 for row in rows)
    # CMP is the mean of LAST price over the window (workbook column L), which
    # for a single-session symbol is simply its last traded price.
    assert rows[0]["cmp"] == pytest.approx(
        filled_data.symbol_frame(first_symbol)["last"].iloc[0])
    assert rows[0]["avg_price"] == pytest.approx(
        filled_data.metrics(first_symbol)["avg_price"].iloc[0])


def test_screener_covers_every_symbol(filled_data: DataService) -> None:
    screener = filled_data.screener()
    assert len(screener) == 191
    assert set(screener["symbol"]) == set(filled_data.symbols())
    assert set(screener["trade_date"]) == {SESSION}


def test_screener_today_equals_the_only_session(filled_data: DataService) -> None:
    screener = filled_data.screener().set_index("symbol")
    symbol = filled_data.symbols()[0]
    row = screener.loc[symbol]
    if row["avg_volume"] is not None and not pd.isna(row["avg_volume"]):
        assert row["today_volume"] == pytest.approx(row["avg_volume"])
        assert row["today_delivery"] == pytest.approx(row["avg_delivery"])


def test_screener_honours_symbol_filters(filled_data: DataService) -> None:
    wanted = filled_data.symbols()[:3]
    screener = filled_data.screener(DataFilters(symbols=wanted))
    assert set(screener["symbol"]) == set(wanted)


def test_screener_cache_distinguishes_the_universe(filled_data: DataService) -> None:
    symbols = filled_data.symbols()
    assert len(filled_data.screener()) == 191
    # a cached "everything" result must not leak into a universed call
    assert len(filled_data.screener(universe=symbols[:3])) == 3
    assert len(filled_data.screener(universe=symbols[:2])) == 2
    assert len(filled_data.screener()) == 191


def test_screener_cache_follows_the_formula_mode(filled_data: DataService,
                                                 filled_settings: SettingsService) -> None:
    before = filled_data.screener()
    filled_settings.set("formulas.mode", "corrected")
    try:
        corrected = filled_data.screener()
    finally:
        filled_settings.set("formulas.mode", "original")
    assert corrected is not before                 # a different key was computed
    pd.testing.assert_frame_equal(filled_data.screener(), before)


def test_overview_totals_match_the_frame(filled_data: DataService) -> None:
    overview = filled_data.overview()
    frame = filled_data.frame()
    assert overview["unique_stocks"] == 191
    assert overview["total_records"] == 191
    assert overview["total_volume"] == pytest.approx(float(frame["tottrdqty"].sum()))


def test_movers_are_derived_from_the_latest_session(filled_data: DataService) -> None:
    movers = filled_data.movers(limit=5)
    assert len(movers["gainers"]) <= 5
    assert len(movers["losers"]) <= 5
    if not movers["gainers"].empty:
        assert movers["gainers"]["pct_change"].iloc[0] >= \
            movers["losers"]["pct_change"].iloc[0]


def test_classification_counts_cover_every_row(filled_data: DataService) -> None:
    counts = filled_data.classification()
    assert set(counts) == {"D. Ind", "DV1", "DV2"}
    for buckets in counts.values():
        assert sum(buckets.values()) == 191


def test_trend_aggregates_by_date(filled_data: DataService) -> None:
    trend = filled_data.trend("close")
    assert list(trend.columns) == ["trade_date", "value"]
    assert len(trend) == 1
    assert trend["value"].iloc[0] == pytest.approx(
        float(filled_data.frame()["close"].mean()))


def test_trend_supports_every_aggregation(filled_data: DataService) -> None:
    frame = filled_data.frame()
    for aggregation, expected in (
        ("sum", frame["close"].sum()),
        ("avg", frame["close"].mean()),
        ("min", frame["close"].min()),
        ("max", frame["close"].max()),
        ("count", len(frame)),
    ):
        value = filled_data.trend("close", aggregation=aggregation)["value"].iloc[0]
        assert value == pytest.approx(float(expected)), aggregation


def test_trend_of_an_unknown_metric_is_empty(filled_data: DataService) -> None:
    assert filled_data.trend("no_such_metric").empty


def test_group_by_prices_and_screener_sources(filled_data: DataService) -> None:
    by_symbol = filled_data.group_by("close", "symbol", aggregation="avg")
    assert len(by_symbol) == 191
    assert by_symbol["value"].iloc[0] >= by_symbol["value"].iloc[-1]

    by_band = filled_data.group_by("symbol", "d_ind", aggregation="count")
    assert by_band["value"].sum() == 191


def test_group_by_respects_the_limit(filled_data: DataService) -> None:
    assert len(filled_data.group_by("close", "symbol", limit=4)) == 4


def test_group_by_of_an_unknown_dimension_is_empty(filled_data: DataService) -> None:
    assert filled_data.group_by("close", "no_such_dimension").empty


def test_resolve_as_of_accepts_dates_strings_and_none(filled_data: DataService) -> None:
    assert filled_data.resolve_as_of() == SESSION
    assert filled_data.resolve_as_of("2024-05-06") == _dt.date(2024, 5, 6)
    assert filled_data.resolve_as_of(SESSION) == SESSION


def test_params_follow_the_settings(filled_db: Database) -> None:
    settings = SettingsService(filled_db)
    service = DataService(filled_db, settings)
    params = service.params()
    assert params.ma_days == settings.moving_average_days()
    assert params.mode == settings.formula_mode()

    ordered = service.params(symbol_order=["B", "A"])
    assert ordered.symbol_order == ["B", "A"]


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------


def test_frames_are_cached_until_invalidated(data: DataService, db: Database,
                                             workbook_path) -> None:
    assert data.frame() is data.frame()                 # same cached object
    version = data.version

    data.invalidate("test")
    assert data.version == version + 1
    assert data.frame() is not data.summary()           # cache was cleared

    payload = preview_sheet(workbook_path, "Data", rows=1)
    mapping = auto_map_columns(payload["headers"])
    ImportService(db).run(workbook_path, "Data", mapping, duplicate_policy="skip",
                          header_row=payload["header_row"])
    data.invalidate("import")
    assert data.summary()["records"] == 191
    assert len(data.symbols()) == 191


def test_invalidating_without_a_reason_is_silent(data: DataService) -> None:
    data.invalidate()
    assert data.version >= 1


# --------------------------------------------------------------------------
# history and audit
# --------------------------------------------------------------------------


def test_batches_and_their_errors_are_readable(data: DataService, db: Database,
                                               workbook_path, tmp_path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
                  "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "Delivery %"])
    sheet.append(["ACME", _dt.date(2025, 1, 1), 10, 12, 9, 11, 11, 10, 100, 1100, 5, 40])
    sheet.append([None, _dt.date(2025, 1, 2), 10, 12, 9, 11, 11, 10, 100, 1100, 5, 40])
    path = tmp_path / "errors.xlsx"
    workbook.save(path)

    mapping = auto_map_columns(["Symbol", "Date", "OPEN", "HIGH", "LOW", "CLOSE",
                                "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL",
                                "TOTALTRADES", "Delivery %"])
    report = ImportService(db).run(path, "Data", mapping, duplicate_policy="skip")
    assert report.error_count == 1

    batches = data.batches()
    assert len(batches) == 1
    assert batches[0].id == report.batch_id

    errors = data.batch_errors(report.batch_id)
    assert len(errors) == 1
    assert errors[0]["row"] == 3
    assert errors[0]["severity"] == "error"


def test_audit_log_filters(data: DataService, db: Database) -> None:
    db.log("custom", "first message", severity="info")
    db.log("custom", "second message", severity="warning")

    everything = data.audit_log()
    assert any(entry["message"] == "first message" for entry in everything)

    warnings = data.audit_log(severity="warning")
    assert all(entry["severity"] == "warning" for entry in warnings)

    searched = data.audit_log(search="second")
    assert len(searched) == 1
    assert searched[0]["message"] == "second message"

    assert "custom" in data.audit_categories()
    assert data.audit_log(category="nothing-here") == []


def test_audit_log_is_newest_first(data: DataService, db: Database) -> None:
    db.log("custom", "older")
    db.log("custom", "newer")
    entries = [e for e in data.audit_log(limit=10) if e["category"] == "custom"]
    assert entries[0]["message"] == "newer"
