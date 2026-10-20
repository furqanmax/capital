"""Analytics engine — the verified business rules of the workbook in Python.

Two execution modes are supported for every rule that the audit flagged:

``original``
    Replays the workbook's behaviour *exactly*, including the defects recorded
    in :data:`services.formula_catalog.AUDIT_FINDINGS`. This is the default so
    that parity tests pass against the real workbook.

``corrected``
    Applies the documented intent (backward-only windows, current-row volume,
    closed delivery buckets, the 1.0/1.5/2.0 badge ladder).

Everything works on a tidy :class:`pandas.DataFrame` with the canonical column
names defined in :data:`CANONICAL_COLUMNS`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .formula_catalog import (
    LOOKBACK_WINDOWS,
    MOVING_AVERAGE_DAYS,
    V1_MULTIPLIERS,
)

# --------------------------------------------------------------------------
# Canonical schema
# --------------------------------------------------------------------------

CANONICAL_COLUMNS: Tuple[str, ...] = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "last",
    "prevclose",
    "tottrdqty",
    "tottrdval",
    "totaltrades",
    "delivery_pct",
)

#: Human labels used in the UI / exports.
COLUMN_LABELS: Dict[str, str] = {
    "symbol": "Symbol",
    "trade_date": "Date",
    "open": "OPEN",
    "high": "HIGH",
    "low": "LOW",
    "close": "CLOSE",
    "last": "LAST",
    "prevclose": "PREVCLOSE",
    "tottrdqty": "TOTTRDQTY",
    "tottrdval": "TOTTRDVAL",
    "totaltrades": "TOTALTRADES",
    "delivery_pct": "Delivery %",
    "deliverable_qty": "Deliverable Qty",
    "avg_price": "Avg Price",
    "pct_change": "% Change",
    "action": "Action",
    "vol_ma": "Vol MA",
    "vol_ma_window": "Vol MA Window",
    "vol_ma_error": "Vol MA Unavailable",
    "vol_level": "Vol Level",
    "v1": "V1",
    "vol_badge": "Vol Badge",
    "badge_bronze": "V2 BRONZE",
    "badge_silver": "V2 SILVER",
    "badge_gold": "V2 GOLD",
    "trade_indicator": "D Ind.",
    "cmp": "CMP",
    "avg_price_avg": "Avg Price (window)",
    "delivery_avg": "Delivery % (window)",
    "action_avg": "Action (window)",
    "cmp_verdict": "CMP > Avg Price",
}

#: Fields a user formula may reference.
FORMULA_FIELDS: Tuple[str, ...] = tuple(name.upper() for name in CANONICAL_COLUMNS)

MODE_ORIGINAL = "original"
MODE_CORRECTED = "corrected"


@dataclass
class AnalyticsParams:
    mode: str = MODE_ORIGINAL
    ma_days: int = MOVING_AVERAGE_DAYS
    lookbacks: Tuple[int, ...] = LOOKBACK_WINDOWS
    #: symbol ordering used to replay the Main sheet's row-offset defect (B5).
    symbol_order: Optional[Sequence[str]] = None

    def __post_init__(self) -> None:
        if self.mode not in (MODE_ORIGINAL, MODE_CORRECTED):
            raise ValueError(f"unknown mode {self.mode!r}")
        if int(self.ma_days) < 1:
            raise ValueError("ma_days must be >= 1")
        if not self.lookbacks or any(int(w) < 1 for w in self.lookbacks):
            raise ValueError("lookbacks must be a non-empty sequence of positive windows")


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def _clean_series(values: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and np.isnan(value):
                continue
            out.append(float(value))
    return out


def _column(df: pd.DataFrame, name: str) -> List[Any]:
    """One raw column as a list, with ``NaN`` normalised to ``None``.

    ``Database.price_frame`` yields ``NaN`` for a SQL ``NULL``.  The engine's
    convention is that a missing value is *unavailable* (``None``); letting a
    bare ``NaN`` through would silently poison every average it touches.
    """
    if name not in df.columns:
        return [None] * len(df)
    return [_none_if_nan(value) for value in df[name].tolist()]


def _none_if_nan(value: Any) -> Any:
    return None if (isinstance(value, float) and value != value) else value


def _safe_div(numerator: Any, denominator: Any) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return None
    if den == 0:
        return None
    return num / den


def _mean(values: Iterable[Any]) -> Optional[float]:
    clean = _clean_series(list(values))
    if not clean:
        return None
    return sum(clean) / len(clean)


def _offset_window(
    values: Sequence[Any],
    index: int,
    height: int,
) -> Tuple[List[Any], bool]:
    """Replay ``OFFSET(ref, 0, 0, height, 1)`` over a column.

    Returns ``(window, clipped)``.  ``height`` may be positive (forward, as the
    workbook's broken AA counter produces) or negative (backward).

    ``clipped`` is True when Excel would have referenced cells outside the
    populated data block.  In the real worksheet those cells contain the
    legend/average block above the data (``Analysis!I13`` etc.), which is a
    layout artefact rather than market data; the audit report records this
    (finding B3) and we clip instead of contaminating the average.
    """
    if height == 0:
        return [], True
    if height > 0:
        start = index
        stop = index + height            # exclusive
    else:
        start = index + height + 1       # inclusive
        stop = index + 1                 # exclusive
    clipped = start < 0 or stop > len(values)
    start = max(0, start)
    stop = min(len(values), stop)
    return list(values[start:stop]), clipped


# --------------------------------------------------------------------------
# Per-row metrics for a single symbol (the Analysis sheet)
# --------------------------------------------------------------------------


def compute_symbol_metrics(frame: pd.DataFrame, params: AnalyticsParams | None = None) -> pd.DataFrame:
    """Add the Analysis-sheet calculated columns to one symbol's history.

    ``frame`` must contain the canonical columns and is expected to be sorted
    ascending by ``trade_date``.
    """
    params = params or AnalyticsParams()
    df = frame.copy().reset_index(drop=True)
    if df.empty:
        return df

    qty = _column(df, "tottrdqty")
    delivery = _column(df, "delivery_pct")
    trades = _column(df, "totaltrades")
    last = _column(df, "last")
    prevclose = _column(df, "prevclose")
    o = _column(df, "open")
    h = _column(df, "high")
    low = _column(df, "low")
    c = _column(df, "close")

    deliverable: List[Optional[float]] = []
    avg_price: List[Optional[float]] = []
    pct_change: List[Optional[float]] = []
    action: List[Optional[float]] = []
    for i in range(len(df)):
        deliverable.append(
            None if delivery[i] is None or qty[i] is None else float(delivery[i]) * float(qty[i]) / 100.0
        )
        if None in (o[i], h[i], low[i], c[i]):
            avg_price.append(None)
        else:
            avg_price.append((float(o[i]) + float(h[i]) + float(low[i]) + float(c[i])) / 4.0)
        pct_change.append(_safe_div((last[i] - prevclose[i]) if (last[i] is not None and prevclose[i] is not None) else None,
                                    prevclose[i]))
        action.append(_safe_div(qty[i], trades[i]))

    df["deliverable_qty"] = deliverable
    df["avg_price"] = avg_price
    df["pct_change"] = pct_change
    df["action"] = action

    # ---- volume moving average (finding B3) ----------------------------
    # Workbook: Q{r} = AVERAGE(OFFSET(data[[#This Row],[TOTTRDQTY]],0,0,-$Q$13+AA{r},1))
    # AA{r} counts down from 1 at row 43 (AA43 = 1, AA42 = AA43+1, ...), so
    # AA{r} = 44 - r = 29 - i for data index i = r - 15, and is blank from
    # row 44 onwards.
    #
    # Consequences, all verified against the cached values in the workbook:
    #   * i <= 3  (rows 15..18): height = 4-i >= 1 -> the OFFSET looks FORWARD
    #     and averages FUTURE sessions; these are the only rows where Q returns
    #     a number.
    #   * i == 4  (row 19): height = 0 -> OFFSET is invalid -> #VALUE!.
    #   * i >= 5: height is negative, and because the height reaches above the
    #     table's data body, Excel rejects the OFFSET on a structured
    #     reference and returns #VALUE!. Rows 19..200 are therefore ALL
    #     #VALUE!, i.e. the Mov Avg column is effectively dead.
    # We replay that: original mode yields a value for i <= 3 and None
    # (the application's representation of an unavailable/#VALUE! result)
    # everywhere else.  Corrected mode implements the documented backward,
    # capped window.
    vol_ma: List[Optional[float]] = []
    ma_error: List[bool] = []
    ma_window: List[Optional[int]] = []
    for i in range(len(df)):
        if params.mode == MODE_ORIGINAL:
            aa = (29 - i) if i <= 28 else 0
            height = -params.ma_days + aa
            if height >= 1:
                window, _clipped = _offset_window(qty, i, height)
                vol_ma.append(_mean(window))
                ma_error.append(False)
                ma_window.append(height)
            else:
                vol_ma.append(None)
                ma_error.append(True)
                ma_window.append(None)
        else:
            start = max(0, i - params.ma_days + 1)
            window = list(qty[start:i + 1])
            vol_ma.append(_mean(window))
            ma_error.append(False)
            ma_window.append(i - start + 1)

    df["vol_ma"] = vol_ma
    df["vol_ma_window"] = ma_window
    #: True where the workbook evaluates to #VALUE! (a spreadsheet limitation,
    #: not a market-data condition).
    df["vol_ma_error"] = ma_error

    # ---- volume level ---------------------------------------------------
    df["vol_level"] = [
        None if ma is None or q is None else ("Below" if ma > q else "Above")
        for ma, q in zip(vol_ma, qty)
    ]

    # ---- baselines over the populated block (Analysis row 13) -----------
    base_delivery = _mean(delivery)
    base_action = _mean(action)
    base_trades = _mean(trades)
    df.attrs["baseline_delivery"] = base_delivery
    df.attrs["baseline_action"] = base_action
    df.attrs["baseline_trades"] = base_trades

    # ---- V1 jackpot signal (Analysis!S) --------------------------------
    # S = IFERROR(<nested IF>, <fallback>).  The nested IF never raises an
    # error, so the fallback branch ("--" / "^") is unreachable dead code
    # (finding B9); the column only ever yields Spl / Trpl / Dbl / J / "".
    v1: List[Optional[str]] = []
    for i in range(len(df)):
        d, a = delivery[i], action[i]
        if d is None or a is None or base_delivery is None or base_action is None:
            v1.append(None)
            continue
        label = ""
        if d > base_delivery and a > base_action:
            label = "J"
            for name, dmult, amult in V1_MULTIPLIERS[:3]:
                if d > dmult * base_delivery and a > amult * base_action:
                    label = name
                    break
        elif params.mode == MODE_CORRECTED:
            # the branch the workbook documents but can never reach (finding B9)
            if d > base_delivery and a < base_action:
                label = "--"
            elif d < base_delivery and a > base_action:
                label = "^"
        v1.append(label)
    df["v1"] = v1

    # ---- volume badges (Analysis!T/U/V, findings B4 and B3) -------------
    # Each column is IFERROR(IF(AND(L>60, I<k*Q), BADGE, IF(AND(L>60, I>k*Q),
    # "--", IF(AND(L<60, I<k*Q), "^", ""))), "") — the IFERROR swallows the
    # #VALUE! that the broken Mov Avg produces, so the badges are blank for
    # every row where the window is unavailable.
    if params.mode == MODE_ORIGINAL:
        ladder = (("BRONZE", 1.0), ("SILVER", 0.75), ("GOLD", 0.5))   # decreasing
    else:
        ladder = (("BRONZE", 1.0), ("SILVER", 1.5), ("GOLD", 2.0))    # increasing
    badge_columns: Dict[str, List[Optional[str]]] = {name: [] for name, _ in ladder}
    combined: List[Optional[str]] = []
    for i in range(len(df)):
        d, q, ma = delivery[i], qty[i], vol_ma[i]
        results: Dict[str, str] = {}
        for name, factor in ladder:
            if d is None or q is None or ma is None:
                results[name] = ""
            elif d > 60 and q < factor * ma:
                results[name] = name
            elif d > 60 and q > factor * ma:
                results[name] = "--"
            elif d < 60 and q < factor * ma:
                results[name] = "^"
            else:
                results[name] = ""
            badge_columns[name].append(results[name])
        combined.append(next((results[name] for name, _ in ladder if results[name]), ""))
    for name, _ in ladder:
        df[f"badge_{name.lower()}"] = badge_columns[name]
    df["vol_badge"] = combined
    df.attrs["vol_badge_ladder"] = params.mode

    # ---- trade-count indicator (Analysis!W) ----------------------------
    indicator: List[Optional[str]] = []
    for i in range(len(df)):
        k = trades[i]
        if k is None or base_trades is None:
            indicator.append(None)
        elif k == 0:
            indicator.append("")
        elif k < base_trades:
            indicator.append("Low")
        elif k > 4 * base_trades:
            indicator.append("V.High")
        elif k > 2 * base_trades:
            indicator.append("High")
        else:
            indicator.append("Above")
    df["trade_indicator"] = indicator
    df.attrs["baseline_delivery"] = base_delivery
    df.attrs["baseline_action"] = base_action
    df.attrs["baseline_trades"] = base_trades

    # ``None`` means "unavailable" throughout the application; pandas would
    # otherwise turn it into ``NaN`` as soon as a column holds numbers, so the
    # columns are rebuilt as ``object`` to keep the distinction visible.
    for column in ("deliverable_qty", "avg_price", "pct_change", "action",
                   "vol_ma", "vol_ma_window"):
        if column in df.columns:
            df[column] = pd.Series([_none_if_nan(value) for value in df[column].tolist()],
                                   dtype="object", index=df.index)

    return df


# --------------------------------------------------------------------------
# Lookback summary (the Analysis 1D/3D/5D/7D/10D block)
# --------------------------------------------------------------------------

#: Analysis!I4:I6 (CMP) and L4:L6 (Delivery %) are hard-wired to a four-row
#: window (findings B1 and B2).  Every other metric in the block uses the
#: correct 5/7/10-row window.
_BUGGY_WINDOW = 4


def _window_size(requested: int, mode: str) -> int:
    """Window used by the CMP / Delivery % columns."""
    if mode == MODE_ORIGINAL and requested in (5, 7, 10):
        return _BUGGY_WINDOW
    return requested


def compute_lookback_summary(
    frame: pd.DataFrame,
    params: AnalyticsParams | None = None,
    reference: str = "latest",
) -> List[Dict[str, Any]]:
    """Reproduce the Analysis 1D/3D/5D/7D/10D summary block.

    ``reference``
        ``"latest"``   (application default) the window is the most recent N
                       sessions of the symbol's ascending history.
        ``"earliest"`` (workbook replay) the window starts at the *first* row of
                       the block.  The workbook's summary block references
                       ``$G$15`` onwards, i.e. the first row of the pasted range;
                       it therefore assumes the newest session sits at the top
                       of the block (finding B8), while the supplied sample is
                       stored oldest-first.

    In ``original`` mode the CMP and Delivery % columns use the workbook's
    hard-wired four-row window (findings B1/B2), while Avg Price, % Change and
    Action use the correct ``N``-row window.  ``cmp_window`` and
    ``delivery_window`` make that asymmetry explicit in the returned rows.
    """
    params = params or AnalyticsParams()
    if frame.empty:
        return []
    if reference not in ("latest", "earliest"):
        raise ValueError("reference must be 'latest' or 'earliest'")

    df = frame.sort_values("trade_date").reset_index(drop=True)
    metrics = compute_symbol_metrics(df, params)
    rows: List[Dict[str, Any]] = []

    def pick(size: int) -> pd.DataFrame:
        return metrics.tail(size) if reference == "latest" else metrics.head(size)

    for requested in params.lookbacks:
        buggy = _window_size(requested, params.mode)
        full = pick(requested)
        cmp_window = pick(buggy)

        cmp_value = _mean(cmp_window["last"].tolist())
        avg_price_value = _mean(full["avg_price"].tolist())
        pct_value = _mean(full["pct_change"].tolist())
        delivery_value = _mean(cmp_window["delivery_pct"].tolist())
        action_value = _mean(full["action"].tolist())

        if cmp_value is None or avg_price_value is None:
            verdict = None
        elif cmp_value > avg_price_value:
            verdict = "Above"
        elif cmp_value < avg_price_value:
            verdict = "Below"
        else:
            verdict = ""

        rows.append(
            {
                "window": f"{requested}D",
                #: sessions that actually exist inside the requested window
                "window_used": len(full),
                "cmp_window": buggy,
                "delivery_window": buggy,
                "cmp": cmp_value,
                "avg_price": avg_price_value,
                "pct_change": pct_value,
                "delivery_pct": delivery_value,
                "action": action_value,
                "cmp_verdict": verdict,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Screener (the Main sheet)
# --------------------------------------------------------------------------


def _float_or_none(value: Any) -> Optional[float]:
    """``float(value)``, or ``None`` for a missing/NaN cell (Excel blank)."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def _float_or_zero(value: Any) -> float:
    """``float(value)``, or ``0.0`` — Excel's SUMIFS result for no match."""
    if value is None or pd.isna(value):
        return 0.0
    return float(value)


def _trade_indicator(today: Optional[float], average: Optional[float]) -> Optional[str]:
    """Main!H — ``IF(F>4G,"V.High",IF(F>2G,"High",IF(F>G,"Above","Low")))``.

    Excel evaluates the comparisons eagerly, so a missing history (``G`` is
    ``#DIV/0!``) makes the whole expression an error; we return ``None`` for
    that case rather than inventing a label.
    """
    if today is None or average is None:
        return None
    if today > 4 * average:
        return "V.High"
    if today > 2 * average:
        return "High"
    if today > average:
        return "Above"
    return "Low"


def _dv1(today_delivery: Optional[float], today_volume: Optional[float],
         avg_volume: Optional[float], other_volume: Optional[float],
         mode: str) -> Optional[str]:
    """Main!I — DV1 badge.

    ``IF(AND(B>60,D<E),"BRONZE",IF(AND(B>60,D<1.5E),"SILVER",
    IF(AND(B>60,D{r+38}<2E),"GOLD","")))``

    Two faithful details:

    * ``AND`` is eager in Excel, so an errored ``E`` (no history) poisons the
      whole expression even when ``B<=60``; we return ``None`` there.
    * in ``original`` mode the GOLD test reads the volume of the symbol 38 rows
      further down the sheet (finding B5), replayed via ``other_volume``.
    """
    if avg_volume is None:
        return None
    delivery = 0.0 if today_delivery is None else float(today_delivery)
    volume = 0.0 if today_volume is None else float(today_volume)
    if delivery > 60 and volume < avg_volume:
        return "BRONZE"
    if delivery > 60 and volume < 1.5 * avg_volume:
        return "SILVER"
    gold_volume = volume if mode == MODE_CORRECTED else other_volume
    if gold_volume is not None and delivery > 60 and gold_volume < 2.0 * avg_volume:
        return "GOLD"
    return ""


def _dv2(today_delivery: Optional[float], mode: str) -> str:
    if today_delivery is None:
        return ""
    b = today_delivery
    if mode == MODE_CORRECTED:
        if b < 40:
            return "Low Del"
        if b <= 50:
            return "40% Plus"
        if b <= 60:
            return "50% Plus"
        if b <= 80:
            return "60% Plus"
        return "80% Plus"
    # original: strict > chain, reproduced verbatim (finding B6)
    if b < 40:
        return "Low Del"
    if b > 80:
        return "80% Plus"
    if b > 60:
        return "60% Plus"
    if b > 50:
        return "50% Plus"
    if b > 40:
        return "40% Plus"
    return ""


def screener_aggregates(
    frame: pd.DataFrame,
    as_of: _dt.date | str | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[_dt.date]]:
    """Reduce a price frame to the two tables the screener actually needs.

    Returns ``(averages, today, as_of)`` where ``averages`` holds one row per
    symbol (mean delivery / volume / trades over the whole frame) and ``today``
    holds the ``as_of`` session per symbol.  Splitting this out lets the same
    business rules run on aggregates computed by SQL (or DuckDB) instead of a
    materialised frame, which is what keeps a 100-million-row database out of
    RAM.
    """
    if isinstance(as_of, str):
        as_of = pd.to_datetime(as_of).date()
    elif as_of is not None and not isinstance(as_of, _dt.date):
        raise TypeError("as_of must be a date, an ISO-8601 string or None")

    df = frame
    if not df.empty:
        df = df.copy()
        # ``.date()`` matters: the frame stores ``datetime.date`` objects, so a
        # raw Timestamp here would silently match no row and every "today"
        # aggregate would come back as 0.
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        if as_of is None:
            as_of = pd.to_datetime(df["trade_date"]).max().date()

    if df.empty:
        averages = pd.DataFrame(columns=["avg_delivery", "avg_volume", "avg_trades"])
        today = pd.DataFrame(columns=["delivery_pct", "tottrdqty", "totaltrades"])
        return averages, today, as_of

    averages = df.groupby("symbol", sort=True).agg(
        avg_delivery=("delivery_pct", "mean"),
        avg_volume=("tottrdqty", "mean"),
        avg_trades=("totaltrades", "mean"),
    )
    today = (df[df["trade_date"] == as_of]
             [["symbol", "delivery_pct", "tottrdqty", "totaltrades"]]
             .set_index("symbol"))
    return averages, today, as_of


def build_screener(
    averages: pd.DataFrame,
    today: pd.DataFrame,
    as_of: Optional[_dt.date],
    params: Optional[AnalyticsParams] = None,
    universe: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Turn per-symbol aggregates into the Main-sheet screener rows."""
    params = params or AnalyticsParams()

    order = list(params.symbol_order) if params.symbol_order else sorted(averages.index)
    order_index = {symbol: idx for idx, symbol in enumerate(order)}

    if universe:
        available = {str(s).upper() for s in universe}
    else:
        available = {str(s) for s in averages.index}

    if params.symbol_order:
        # The caller asked for a specific presentation order (the sheet's own
        # order, in the parity replay).  Symbols the caller did not mention are
        # appended in alphabetical order so nothing is ever dropped.
        all_symbols = [s for s in order if s in available]
        all_symbols += sorted(available - set(all_symbols))
    else:
        all_symbols = sorted(available)

    # ``.loc`` per symbol builds a Series each time; a dict of rows is the same
    # data with a fraction of the cost.  Duplicate (symbol, date) rows cannot
    # occur in the database, but a caller may pass a frame that has them, so the
    # first-match semantics of ``.loc`` are kept for that case.
    avg_rows = averages.to_dict("index") if not averages.empty else {}
    duplicates = bool(today.index.duplicated().any()) if not today.empty else False
    today_rows = ({} if duplicates or today.empty else today.to_dict("index"))

    def today_row(symbol: str):
        if duplicates:
            row = today.loc[symbol]
            return row.iloc[0] if isinstance(row, pd.DataFrame) else row
        return today_rows.get(symbol)

    records: List[Dict[str, Any]] = []
    for symbol in all_symbols:
        avg_row = avg_rows.get(symbol)
        if avg_row is not None:
            avg_delivery = _float_or_none(avg_row["avg_delivery"])
            avg_volume = _float_or_none(avg_row["avg_volume"])
            avg_trades = _float_or_none(avg_row["avg_trades"])
        else:
            avg_delivery = avg_volume = avg_trades = None

        # Excel SUMIFS with no matching rows returns 0, not blank.
        today_delivery = 0.0
        today_volume = 0.0
        today_trades = 0.0
        t = today_row(symbol) if symbol in today.index else None
        if t is not None:
            today_delivery = _float_or_zero(t["delivery_pct"])
            today_volume = _float_or_zero(t["tottrdqty"])
            today_trades = _float_or_zero(t["totaltrades"])

        # --- replay the D{r+38} row offset from Main!I (finding B5) ------
        other_volume: Optional[float] = None
        if params.mode == MODE_ORIGINAL and symbol in order_index:
            other_index = order_index[symbol] + 38
            if 0 <= other_index < len(order):
                other_symbol = order[other_index]
                if other_symbol in today.index:
                    ot = today_row(other_symbol)
                    if ot is not None:
                        other_volume = _float_or_none(ot["tottrdqty"])

        records.append(
            {
                "symbol": symbol,
                "trade_date": as_of,
                "today_delivery": today_delivery,
                "avg_delivery": avg_delivery,
                "today_volume": today_volume,
                "avg_volume": avg_volume,
                "today_trades": today_trades,
                "avg_trades": avg_trades,
                "d_ind": _trade_indicator(today_trades, avg_trades),
                "dv1": _dv1(today_delivery, today_volume, avg_volume, other_volume, params.mode),
                "dv2": _dv2(today_delivery, params.mode),
            }
        )

    if not records:
        return pd.DataFrame(columns=SCREENER_COLUMNS)
    return pd.DataFrame.from_records(records)


#: Output columns of :func:`compute_screener` / :func:`build_screener`.
SCREENER_COLUMNS = [
    "symbol", "trade_date", "today_delivery", "avg_delivery", "today_volume",
    "avg_volume", "today_trades", "avg_trades", "d_ind", "dv1", "dv2",
]


def compute_screener(
    frame: pd.DataFrame,
    as_of: _dt.date | str | None = None,
    params: AnalyticsParams | None = None,
    symbols: Optional[Iterable[str]] = None,
    universe: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Reproduce the Main screener for one trade date.

    Columns mirror ``Main!B:J``: Today Delivery, Avg. Delivery, Today Volume,
    Avg. Volume, Today Trades, Avg. Trades, D. Ind, DV1, DV2.

    ``symbols`` restricts the *data* that feeds the aggregates.  ``universe``
    forces the output to contain those symbols even when the database has no
    rows for them — the workbook's ``Main`` sheet lists 825 symbols
    unconditionally and Excel then yields ``SUMIFS`` = 0 and ``AVERAGEIF`` =
    ``#DIV/0!``.  We represent that combination as ``today_* = 0.0`` and
    ``avg_* = None``.
    """
    params = params or AnalyticsParams()
    if frame.empty and not universe:
        return pd.DataFrame(columns=SCREENER_COLUMNS)

    df = frame
    if symbols is not None:
        wanted = {str(s).upper() for s in symbols}
        df = df[df["symbol"].astype(str).str.upper().isin(wanted)] if not df.empty else df

    averages, today, as_of = screener_aggregates(df, as_of)
    return build_screener(averages, today, as_of, params, universe)



# --------------------------------------------------------------------------
# Dashboard aggregates
# --------------------------------------------------------------------------


def dashboard_overview(frame: pd.DataFrame) -> Dict[str, Any]:
    """Headline KPIs computed directly from the stored data."""
    if frame.empty:
        return {
            "unique_stocks": 0,
            "total_records": 0,
            "latest_date": None,
            "earliest_date": None,
            "total_volume": 0.0,
            "total_value": 0.0,
            "avg_delivery": None,
            "symbols": [],
        }
    dates = pd.to_datetime(frame["trade_date"])
    return {
        "unique_stocks": int(frame["symbol"].nunique()),
        "total_records": int(len(frame)),
        "latest_date": dates.max().date(),
        "earliest_date": dates.min().date(),
        "total_volume": float(pd.to_numeric(frame["tottrdqty"], errors="coerce").fillna(0).sum()),
        "total_value": float(pd.to_numeric(frame["tottrdval"], errors="coerce").fillna(0).sum()),
        "avg_delivery": float(pd.to_numeric(frame["delivery_pct"], errors="coerce").mean())
        if frame["delivery_pct"].notna().any() else None,
        "symbols": sorted(frame["symbol"].dropna().unique().tolist()),
    }


def classification_counts(screener: pd.DataFrame) -> Dict[str, Dict[str, int]]:
    """Counts of each classification across a screener result."""
    out: Dict[str, Dict[str, int]] = {}
    for column, label in (("d_ind", "D. Ind"), ("dv1", "DV1"), ("dv2", "DV2")):
        if column in screener.columns:
            counts = screener[column].fillna("").replace({"": "(none)"}).value_counts()
            out[label] = {str(k): int(v) for k, v in counts.items()}
    return out


def top_movers(frame: pd.DataFrame, as_of: Any, limit: int = 10) -> Dict[str, pd.DataFrame]:
    """Best and worst performers by % change on a given date."""
    empty = pd.DataFrame(columns=["symbol", "pct_change"])
    if frame.empty:
        return {"gainers": empty, "losers": empty}
    df = frame.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    if isinstance(as_of, str):
        as_of = pd.to_datetime(as_of).date()
    day = df[df["trade_date"] == as_of].copy()
    if day.empty:
        return {"gainers": empty, "losers": empty}
    day = day[["symbol", "last", "prevclose"]].copy()
    day["pct_change"] = (day["last"] - day["prevclose"]) / day["prevclose"].replace(0, np.nan)
    day = day.dropna(subset=["pct_change"])
    gainers = day.sort_values("pct_change", ascending=False).head(limit)
    losers = day.sort_values("pct_change", ascending=True).head(limit)
    return {
        "gainers": gainers[["symbol", "pct_change"]].reset_index(drop=True),
        "losers": losers[["symbol", "pct_change"]].reset_index(drop=True),
    }


__all__ = [
    "CANONICAL_COLUMNS",
    "COLUMN_LABELS",
    "FORMULA_FIELDS",
    "MODE_ORIGINAL",
    "MODE_CORRECTED",
    "AnalyticsParams",
    "compute_symbol_metrics",
    "compute_lookback_summary",
    "compute_screener",
    "screener_aggregates",
    "build_screener",
    "SCREENER_COLUMNS",
    "dashboard_overview",
    "classification_counts",
    "top_movers",
]
