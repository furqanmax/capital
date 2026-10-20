"""The analytics engine: calculated columns, lookback windows and the screener.

These are deterministic, pure-data tests — no database, no workbook.  The
workbook-parity tests live in ``test_parity.py``.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import pytest

from services.analytics import (
    MODE_CORRECTED,
    MODE_ORIGINAL,
    AnalyticsParams,
    classification_counts,
    compute_lookback_summary,
    compute_screener,
    compute_symbol_metrics,
    dashboard_overview,
    top_movers,
)


def frame(rows):
    """Build a canonical frame from ``(date, open, high, low, close, last,
    prevclose, qty, value, trades, delivery)`` tuples for one symbol."""
    records = []
    for index, row in enumerate(rows):
        (date, o, h, low, c, last, prev, qty, value, trades, delivery) = row
        records.append({
            "symbol": "TEST", "trade_date": date, "open": o, "high": h, "low": low,
            "close": c, "last": last, "prevclose": prev, "tottrdqty": qty,
            "tottrdval": value, "totaltrades": trades, "delivery_pct": delivery,
        })
    return pd.DataFrame.from_records(records)


D1 = _dt.date(2025, 1, 1)
D2 = _dt.date(2025, 1, 2)
D3 = _dt.date(2025, 1, 3)
D4 = _dt.date(2025, 1, 6)
D5 = _dt.date(2025, 1, 7)

TWO_ROWS = frame([
    (D1, 100.0, 110.0, 90.0, 105.0, 106.0, 100.0, 1000.0, 105000.0, 50.0, 40.0),
    (D2, 106.0, 112.0, 104.0, 110.0, 111.0, 106.0, 1200.0, 132000.0, 60.0, 55.0),
])

FIVE_ROWS = frame([
    (D1, 100.0, 110.0, 90.0, 105.0, 105.0, 100.0, 1000.0, 1.0, 50.0, 40.0),
    (D2, 105.0, 115.0, 100.0, 110.0, 110.0, 105.0, 1100.0, 2.0, 55.0, 41.0),
    (D3, 110.0, 120.0, 105.0, 115.0, 115.0, 110.0, 1200.0, 3.0, 60.0, 42.0),
    (D4, 115.0, 125.0, 110.0, 120.0, 120.0, 115.0, 1300.0, 4.0, 65.0, 43.0),
    (D5, 120.0, 130.0, 115.0, 125.0, 125.0, 120.0, 1400.0, 5.0, 70.0, 44.0),
])


# --------------------------------------------------------------------------
# per-session metrics
# --------------------------------------------------------------------------


def test_metrics_columns_match_the_documented_set() -> None:
    result = compute_symbol_metrics(TWO_ROWS, AnalyticsParams())
    for column in ("deliverable_qty", "avg_price", "pct_change", "action", "vol_ma",
                   "vol_level", "v1", "badge_bronze", "badge_silver", "badge_gold",
                   "vol_badge", "trade_indicator"):
        assert column in result.columns, column
    assert len(result) == len(TWO_ROWS)


def test_deliverable_quantity_and_avg_price() -> None:
    result = compute_symbol_metrics(TWO_ROWS, AnalyticsParams())
    assert result["deliverable_qty"].tolist() == [400.0, 660.0]
    assert result["avg_price"].tolist() == [101.25, 108.0]


def test_pct_change_is_a_ratio_not_a_percentage() -> None:
    """Excel stores ``(LAST-PREVCLOSE)/PREVCLOSE`` — no ×100 anywhere."""
    result = compute_symbol_metrics(TWO_ROWS, AnalyticsParams())
    assert result["pct_change"].iloc[0] == pytest.approx(0.06)
    assert result["pct_change"].iloc[1] == pytest.approx(5 / 106)


def test_pct_change_handles_a_zero_previous_close() -> None:
    rows = frame([
        (D1, 100.0, 110.0, 90.0, 105.0, 106.0, 0.0, 1000.0, 1.0, 50.0, 40.0),
    ])
    result = compute_symbol_metrics(rows, AnalyticsParams())
    assert result["pct_change"].iloc[0] is None


def test_action_is_quantity_per_trade() -> None:
    result = compute_symbol_metrics(TWO_ROWS, AnalyticsParams())
    assert result["action"].iloc[0] == pytest.approx(20.0)
    assert result["action"].iloc[1] == pytest.approx(20.0)


def test_action_handles_zero_trades() -> None:
    rows = frame([
        (D1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 500.0, 1.0, 0.0, 40.0),
    ])
    result = compute_symbol_metrics(rows, AnalyticsParams())
    assert result["action"].iloc[0] is None


def test_avg_price_is_blank_when_any_component_is_missing() -> None:
    rows = frame([(D1, 100.0, 110.0, 90.0, 105.0, 106.0, 100.0, 1000.0, 1.0, 50.0, 40.0)])
    rows.loc[0, "high"] = None
    result = compute_symbol_metrics(rows, AnalyticsParams())
    assert result["avg_price"].iloc[0] is None


def test_volume_moving_average_is_backward_looking_when_corrected() -> None:
    params = AnalyticsParams(mode=MODE_CORRECTED, ma_days=3)
    result = compute_symbol_metrics(FIVE_ROWS, params)
    assert result["vol_ma"].iloc[0] == pytest.approx(1000.0)
    assert result["vol_ma"].iloc[1] == pytest.approx(1050.0)
    assert result["vol_ma"].iloc[2] == pytest.approx(1100.0)
    # the window is capped at ``ma_days``
    assert result["vol_ma"].iloc[3] == pytest.approx((1100 + 1200 + 1300) / 3)
    assert result["vol_ma_window"].tolist() == [1, 2, 3, 3, 3]
    assert not result["vol_ma_error"].any()


def test_volume_level_banding() -> None:
    params = AnalyticsParams(mode=MODE_CORRECTED, ma_days=3)
    result = compute_symbol_metrics(FIVE_ROWS, params)
    assert set(result["vol_level"].dropna().unique()) <= {"Below", "Above"}


def test_trade_indicator_splits_on_four_times_the_average() -> None:
    rows = frame([
        (D1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 100.0, 1.0, 100.0, 40.0),
        (D2, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 100.0, 1.0, 401.0, 40.0),
    ])
    result = compute_symbol_metrics(rows, AnalyticsParams())
    assert result["trade_indicator"].iloc[1] != result["trade_indicator"].iloc[0]


def test_original_mode_reproduces_the_broken_forward_volume_window() -> None:
    """Finding B3: ``Q = AVERAGE(OFFSET(.., -Q13+AA{r}, 1))``.

    With the workbook's ``Q13 = 25`` the height is ``4-i``, so only the first
    four data rows get a value — and they average *future* sessions.  Every
    later row is ``#VALUE!``, which the application represents as unavailable.
    """
    params = AnalyticsParams()                      # original, ma_days=25
    rows = compute_symbol_metrics(FIVE_ROWS, params)
    assert rows["vol_ma_window"].tolist() == [4, 3, 2, 1, None]
    assert rows["vol_ma_error"].tolist() == [False, False, False, False, True]
    # row 0 averages the *forward* window qty[0:4]
    assert rows["vol_ma"].iloc[0] == pytest.approx((1000 + 1100 + 1200 + 1300) / 4)
    # and 1..4 for the rows where the offset shrinks
    assert rows["vol_ma"].iloc[3] == pytest.approx(1300.0)
    assert rows["vol_ma"].iloc[4] is None


def test_corrected_mode_uses_the_configured_window() -> None:
    params = AnalyticsParams(mode=MODE_CORRECTED, ma_days=3)
    result = compute_symbol_metrics(FIVE_ROWS, params)
    assert result["vol_ma_window"].iloc[0] == 1
    assert result["vol_ma_window"].iloc[3] == 3
    assert not bool(result["vol_ma_error"].any())


def test_empty_frame_returns_an_empty_frame() -> None:
    result = compute_symbol_metrics(pd.DataFrame(columns=[
        "symbol", "trade_date", "open", "high", "low", "close", "last", "prevclose",
        "tottrdqty", "tottrdval", "totaltrades", "delivery_pct"]), AnalyticsParams())
    assert result.empty


def test_missing_optional_columns_are_tolerated() -> None:
    """A trimmed import may lack delivery or trades; nothing may crash."""
    rows = TWO_ROWS.drop(columns=["delivery_pct", "totaltrades"])
    result = compute_symbol_metrics(rows, AnalyticsParams())
    assert result["deliverable_qty"].isna().all()


# --------------------------------------------------------------------------
# lookback summary
# --------------------------------------------------------------------------


def test_lookback_windows_are_the_documented_set() -> None:
    rows = compute_lookback_summary(FIVE_ROWS, AnalyticsParams())
    assert [row["window"] for row in rows] == ["1D", "3D", "5D", "7D", "10D"]


def test_lookback_uses_the_latest_n_sessions() -> None:
    rows = {row["window"]: row for row in compute_lookback_summary(FIVE_ROWS, AnalyticsParams())}
    assert rows["1D"]["cmp"] == 125.0
    assert rows["3D"]["cmp"] == pytest.approx((115 + 120 + 125) / 3)
    assert rows["3D"]["avg_price"] == pytest.approx((115 + 120 + 125 + 110) / 4)
    assert rows["1D"]["delivery_pct"] == 44.0
    assert rows["3D"]["delivery_pct"] == pytest.approx((42 + 43 + 44) / 3)


def test_lookback_shorter_than_the_window_uses_what_exists() -> None:
    rows = compute_lookback_summary(TWO_ROWS, AnalyticsParams())
    ten = next(row for row in rows if row["window"] == "10D")
    assert ten["window_used"] == 2


def test_lookback_earliest_replays_the_workbook_block() -> None:
    params = AnalyticsParams(mode=MODE_ORIGINAL)
    latest = {r["window"]: r for r in compute_lookback_summary(FIVE_ROWS, params, "latest")}
    earliest = {r["window"]: r for r in compute_lookback_summary(FIVE_ROWS, params, "earliest")}
    assert earliest["1D"]["cmp"] == 105.0          # first row, not last
    assert latest["1D"]["cmp"] == 125.0
    # B1/B2: CMP and Delivery use a hard-wired 4-row window in original mode
    assert earliest["10D"]["cmp_window"] == 4
    assert earliest["10D"]["delivery_window"] == 4
    assert earliest["10D"]["window_used"] == 5   # only 5 sessions exist


def test_lookback_corrected_mode_uses_the_real_window() -> None:
    params = AnalyticsParams(mode=MODE_CORRECTED)
    rows = {r["window"]: r for r in compute_lookback_summary(FIVE_ROWS, params, "earliest")}
    assert rows["5D"]["cmp_window"] == 5
    assert rows["5D"]["delivery_window"] == 5
    assert rows["5D"]["cmp"] == pytest.approx((105 + 110 + 115 + 120 + 125) / 5)


def test_lookback_on_empty_data_is_empty() -> None:
    empty = TWO_ROWS.iloc[0:0]
    assert compute_lookback_summary(empty, AnalyticsParams()) == []


def test_lookback_rejects_an_unknown_reference() -> None:
    with pytest.raises(ValueError):
        compute_lookback_summary(FIVE_ROWS, AnalyticsParams(), "sideways")


def test_lookback_rows_are_json_friendly() -> None:
    for row in compute_lookback_summary(FIVE_ROWS, AnalyticsParams()):
        assert isinstance(row["window"], str)
        for value in row.values():
            assert value is None or isinstance(value, (int, float, str, bool))


# --------------------------------------------------------------------------
# screener
# --------------------------------------------------------------------------


def test_screener_columns_match_the_main_sheet() -> None:
    result = compute_screener(TWO_ROWS, as_of=D2, params=AnalyticsParams())
    assert list(result.columns) == [
        "symbol", "trade_date", "today_delivery", "avg_delivery", "today_volume",
        "avg_volume", "today_trades", "avg_trades", "d_ind", "dv1", "dv2"]


def test_screener_today_values_come_from_the_as_of_date() -> None:
    result = compute_screener(TWO_ROWS, as_of=D2, params=AnalyticsParams())
    row = result.iloc[0]
    assert row["today_delivery"] == 55.0
    assert row["today_volume"] == 1200.0
    assert row["today_trades"] == 60.0
    assert row["avg_delivery"] == pytest.approx(47.5)
    assert row["avg_volume"] == pytest.approx(1100.0)


def test_screener_defaults_to_the_latest_session() -> None:
    """A derived ``as_of`` must be a ``date`` or nothing would match."""
    result = compute_screener(TWO_ROWS, params=AnalyticsParams())
    assert result["trade_date"].iloc[0] == D2
    assert result["today_volume"].iloc[0] == 1200.0


def test_screener_rejects_a_non_date_as_of() -> None:
    with pytest.raises(TypeError):
        compute_screener(TWO_ROWS, AnalyticsParams())


def test_screener_date_with_no_rows_yields_excel_sumifs_semantics() -> None:
    """SUMIFS with no match is 0; AVERAGEIF with no match is unavailable."""
    later = _dt.date(2030, 1, 1)
    result = compute_screener(TWO_ROWS, as_of=later, params=AnalyticsParams())
    row = result.iloc[0]
    assert row["today_delivery"] == 0.0
    assert row["today_volume"] == 0.0
    assert row["avg_delivery"] == pytest.approx(47.5)   # averages ignore the date
    assert row["trade_date"] == later


def test_screener_universe_forces_rows_for_unknown_symbols() -> None:
    result = compute_screener(TWO_ROWS, as_of=D2, params=AnalyticsParams(),
                              universe=["TEST", "GHOST"])
    assert set(result["symbol"]) == {"GHOST", "TEST"}
    ghost = result[result["symbol"] == "GHOST"].iloc[0]
    assert ghost["today_volume"] == 0.0
    assert pd.isna(ghost["avg_volume"])
    assert pd.isna(ghost["avg_delivery"])
    assert ghost["d_ind"] is None


def test_screener_symbols_argument_restricts_the_aggregates() -> None:
    other = TWO_ROWS.copy()
    other["symbol"] = "OTHER"
    both = pd.concat([TWO_ROWS, other], ignore_index=True)
    result = compute_screener(both, as_of=D2, params=AnalyticsParams(), symbols=["TEST"])
    assert set(result["symbol"]) == {"TEST"}


def test_screener_on_empty_data_without_a_universe() -> None:
    empty = TWO_ROWS.iloc[0:0]
    result = compute_screener(empty, params=AnalyticsParams())
    assert result.empty
    assert "symbol" in result.columns


def test_screener_on_empty_data_with_a_universe() -> None:
    empty = TWO_ROWS.iloc[0:0]
    result = compute_screener(empty, as_of=D2, params=AnalyticsParams(), universe=["A", "B"])
    assert set(result["symbol"]) == {"A", "B"}


def test_screener_replays_the_original_mode_row_offset() -> None:
    """Finding B5: ``Main!I`` reads ``D{r+38}`` — reproduced only in original mode."""
    universe = [f"S{i:02d}" for i in range(45)]
    records = []
    for index, symbol in enumerate(universe):
        records.append({
            "symbol": symbol, "trade_date": D2, "open": 1.0, "high": 1.0, "low": 1.0,
            "close": 1.0, "last": 1.0, "prevclose": 1.0,
            "tottrdqty": float(100 + index), "tottrdval": 1.0, "totaltrades": 1.0,
            "delivery_pct": 40.0})
    data = pd.DataFrame.from_records(records)

    original = compute_screener(data, as_of=D2, params=AnalyticsParams(mode=MODE_ORIGINAL),
                                universe=universe)
    corrected = compute_screener(data, as_of=D2, params=AnalyticsParams(mode=MODE_CORRECTED),
                                 universe=universe)
    assert original["dv1"].notna().any()
    # the two modes must not be identical, otherwise the defect is not reproduced
    assert not original["dv1"].equals(corrected["dv1"]) or not original["dv2"].equals(corrected["dv2"])


def test_screener_symbol_order_is_honoured() -> None:
    data = pd.concat([
        TWO_ROWS.assign(symbol="B"),
        TWO_ROWS.assign(symbol="A"),
    ], ignore_index=True)
    unordered = compute_screener(data, as_of=D2, params=AnalyticsParams())
    assert list(unordered["symbol"]) == ["A", "B"]
    ordered = compute_screener(data, as_of=D2, params=AnalyticsParams(
        symbol_order=("B", "A")))
    assert list(ordered["symbol"]) == ["B", "A"]


# --------------------------------------------------------------------------
# aggregates used by the dashboard
# --------------------------------------------------------------------------


def test_overview_totals() -> None:
    overview = dashboard_overview(TWO_ROWS)
    assert overview["unique_stocks"] == 1
    assert overview["total_records"] == 2
    assert overview["total_volume"] == pytest.approx(2200.0)
    assert overview["total_value"] == pytest.approx(237000.0)
    assert overview["avg_delivery"] == pytest.approx(47.5)
    assert overview["latest_date"] == D2
    assert overview["earliest_date"] == D1
    assert overview["symbols"] == ["TEST"]


def test_overview_of_empty_data_is_all_zero() -> None:
    overview = dashboard_overview(TWO_ROWS.iloc[0:0])
    assert overview["unique_stocks"] == 0
    assert overview["total_records"] == 0
    assert overview["latest_date"] is None


def test_classification_counts_add_up() -> None:
    screener = compute_screener(TWO_ROWS, as_of=D2, params=AnalyticsParams())
    counts = classification_counts(screener)
    assert set(counts) == {"D. Ind", "DV1", "DV2"}
    for buckets in counts.values():
        assert sum(buckets.values()) == len(screener)


def test_top_movers_are_ordered_and_bounded() -> None:
    rows = pd.concat([
        TWO_ROWS.assign(symbol="UP"),
        TWO_ROWS.assign(symbol="DOWN", last=[90.0, 95.0]),
    ], ignore_index=True)
    movers = top_movers(rows, D2, limit=1)
    assert set(movers) == {"gainers", "losers"}
    assert len(movers["gainers"]) <= 1
    gainer = movers["gainers"].iloc[0]
    assert gainer["symbol"] == "UP"
    assert gainer["pct_change"] > 0


def test_top_movers_before_any_data_is_empty() -> None:
    movers = top_movers(TWO_ROWS, _dt.date(2000, 1, 1), limit=5)
    assert movers["gainers"].empty and movers["losers"].empty


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------


def test_params_defaults_match_the_workbook() -> None:
    params = AnalyticsParams()
    assert params.mode == MODE_ORIGINAL
    assert params.ma_days == 25
    assert params.lookbacks == (1, 3, 5, 7, 10)


def test_params_validate_their_inputs() -> None:
    with pytest.raises(ValueError):
        AnalyticsParams(mode="sideways")
    with pytest.raises(ValueError):
        AnalyticsParams(ma_days=0)


def test_metrics_do_not_mutate_the_input_frame() -> None:
    before = TWO_ROWS.copy(deep=True)
    compute_symbol_metrics(TWO_ROWS, AnalyticsParams())
    pd.testing.assert_frame_equal(TWO_ROWS, before)
