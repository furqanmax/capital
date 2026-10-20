"""Query layer shared by the user interface, the reports and the exports.

Everything that reads market data goes through :class:`DataService`.  That keeps
three concerns in one place:

* **filtering** — symbol lists, date ranges and an "as of" date;
* **caching** — an in-memory cache keyed by a monotonically increasing data
  version, so a page switch never re-queries SQLite needlessly while an import
  always invalidates instantly;
* **engine parameters** — the active *original* / *corrected* formula mode and
  the moving-average length come from the settings, not from hard-coded values.
"""

from __future__ import annotations

import datetime as _dt
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from database.db import Database
from models import AuditLog, ImportBatch, ImportError_, Price, PriceExtra, Stock

from . import query_engine
from .analytics import (
    AnalyticsParams,
    build_screener,
    compute_lookback_summary,
    compute_screener,
    compute_symbol_metrics,
    dashboard_overview,
    classification_counts,
    top_movers,
)
from .settings_service import SettingsService

_MISS = object()


class _FrameCache:
    """A small LRU that budgets by rows, not just by entries.

    Cached frames can be hundreds of megabytes each, so an unbounded dict is a
    memory leak with a friendly name.  Entries are evicted least-recently-used
    first once either the entry count or the row budget is exceeded; callers
    that already hold a reference keep it, so eviction never breaks a page.
    """

    def __init__(self, max_entries: int = 48, max_rows: int = 6_000_000):
        self.max_entries = max(1, int(max_entries))
        self.max_rows = max(1, int(max_rows))
        self._items: "OrderedDict[str, Any]" = OrderedDict()
        self.rows = 0

    @staticmethod
    def _weight(value: Any) -> int:
        if isinstance(value, pd.DataFrame):
            return max(1, int(len(value)))
        return 1

    def get(self, key: str, default: Any = None) -> Any:
        try:
            value = self._items.pop(key)
        except KeyError:
            return default
        self._items[key] = value          # most-recently-used
        return value

    def put(self, key: str, value: Any) -> None:
        if key in self._items:
            self.rows -= self._weight(self._items.pop(key))
        self._items[key] = value
        self.rows += self._weight(value)
        while self._items and (len(self._items) > self.max_entries
                              or self.rows > self.max_rows):
            _key, old = self._items.popitem(last=False)
            self.rows -= self._weight(old)
        self.rows = max(0, self.rows)

    def clear(self) -> None:
        self._items.clear()
        self.rows = 0



@dataclass
class DataFilters:
    """The filter set used by the explorer, dashboard, reports and exports."""

    symbols: List[str] = field(default_factory=list)
    start_date: Optional[_dt.date] = None
    end_date: Optional[_dt.date] = None
    as_of: Optional[_dt.date] = None

    def normalised(self) -> "DataFilters":
        symbols = sorted({str(s).strip().upper() for s in self.symbols if str(s).strip()})
        return DataFilters(symbols=symbols, start_date=self.start_date,
                           end_date=self.end_date, as_of=self.as_of)

    def describe(self) -> Dict[str, Any]:
        return {
            "symbols": ", ".join(self.symbols) if self.symbols else "all symbols",
            "start_date": self.start_date.isoformat() if self.start_date else "—",
            "end_date": self.end_date.isoformat() if self.end_date else "—",
            "as_of": self.as_of.isoformat() if self.as_of else "latest session",
        }


class DataService:
    """Cached access to market data and the analytics engine."""

    #: Above this many rows the aggregate methods are answered by SQL (or the
    #: DuckDB mirror) instead of materialising a pandas frame.  Below it pandas
    #: is faster and keeps the numbers bit-identical; tests flip this to 0 to
    #: exercise the SQL path against the same expectations.
    large_dataset_rows: int = query_engine.LARGE_DATASET_ROWS

    def __init__(self, database: Database, settings: Optional[SettingsService] = None,
                 prefer_duckdb: bool = True):
        self.db = database
        self.settings = settings or SettingsService(database)
        self._lock = threading.RLock()
        self._cache = _FrameCache()
        self._version = 0
        # A database that was never initialised has no ``app_settings`` table;
        # fall back to the built-in defaults rather than refusing to construct a
        # service (the rest of the class already tolerates an empty schema).
        try:
            self.large_dataset_rows = self.settings.large_dataset_rows()
            analytics_enabled = self.settings.duckdb_enabled()
            analytics_path = self.settings.analytics_path()
        except SQLAlchemyError:
            self.large_dataset_rows = query_engine.LARGE_DATASET_ROWS
            analytics_enabled, analytics_path = prefer_duckdb, None
        # The threshold is a per-instance value so tests (and the settings page)
        # can force either strategy without touching the module constant.
        self.backend = query_engine.create_backend(
            database,
            prefer_duckdb=prefer_duckdb and analytics_enabled,
            path=analytics_path)

    # ------------------------------------------------------------------
    # cache plumbing
    # ------------------------------------------------------------------

    def invalidate(self, reason: str = "") -> None:
        """Drop every cached frame (called after an import or a settings change)."""
        with self._lock:
            self._cache.clear()
            self._version += 1
        self.backend.invalidate(force="restore" in reason.lower())
        if reason:
            self.db.log("cache", f"cache invalidated: {reason}")

    @property
    def version(self) -> int:
        return self._version

    def _cached(self, key: str, factory):
        with self._lock:
            found = self._cache.get(key, _MISS)
            if found is not _MISS:
                return found
        value = factory()
        with self._lock:
            self._cache.put(key, value)
        return value

    # ------------------------------------------------------------------
    # execution strategy
    # ------------------------------------------------------------------

    def dataset_is_large(self) -> bool:
        """True when loading the whole table into pandas would be unreasonable."""
        return int(self.summary().get("records") or 0) > self.large_dataset_rows

    def _scope(self, filters: DataFilters) -> query_engine.Scope:
        return query_engine.Scope(symbols=tuple(filters.symbols),
                                  start_date=filters.start_date,
                                  end_date=filters.end_date)

    @staticmethod
    def _filter_key(filters: DataFilters) -> str:
        return (f"{','.join(filters.symbols)}|{filters.start_date}|{filters.end_date}")


    # ------------------------------------------------------------------
    # engine configuration
    # ------------------------------------------------------------------

    def params(self, symbol_order: Optional[Sequence[str]] = None) -> AnalyticsParams:
        return AnalyticsParams(
            mode=self.settings.formula_mode(),
            ma_days=self.settings.moving_average_days(),
            symbol_order=list(symbol_order) if symbol_order else None,
        )

    def _params_key(self) -> str:
        """A short, cache-key-safe signature of the analytics parameters.

        Anything that changes the numbers must change the key: otherwise a
        cached frame computed under the old parameters would be served for the
        new ones.
        """
        params = self.params()
        lookbacks = ",".join(str(int(w)) for w in params.lookbacks)
        return f"{params.mode}|{params.ma_days}|{lookbacks}"

    # ------------------------------------------------------------------
    # catalogues
    # ------------------------------------------------------------------

    def symbols(self) -> List[str]:
        return self._cached("symbols", self.db.symbol_list)

    def dates(self) -> List[_dt.date]:
        return self._cached("dates", self.db.date_list)

    def summary(self) -> Dict[str, Any]:
        return self._cached("summary", self.db.summary_counts)

    def latest_date(self) -> Optional[_dt.date]:
        dates = self.dates()
        return dates[-1] if dates else None

    def resolve_as_of(self, as_of: Optional[_dt.date | str] = None) -> Optional[_dt.date]:
        if as_of is None:
            return self.latest_date()
        if isinstance(as_of, str):
            return pd.to_datetime(as_of).date()
        return as_of

    # ------------------------------------------------------------------
    # frames
    # ------------------------------------------------------------------

    def frame(self, filters: Optional[DataFilters] = None,
              limit: Optional[int] = None) -> pd.DataFrame:
        filters = (filters or DataFilters()).normalised()
        key = (f"frame|{','.join(filters.symbols)}|{filters.start_date}"
               f"|{filters.end_date}|{limit or ''}")
        return self._cached(
            key,
            lambda: self.db.price_frame(
                symbols=filters.symbols or None,
                start_date=filters.start_date,
                end_date=filters.end_date,
                limit=limit,
            ),
        )

    def symbol_frame(self, symbol: str) -> pd.DataFrame:
        return self.frame(DataFilters(symbols=[symbol]))

    def metrics(self, symbol: str) -> pd.DataFrame:
        """Per-session calculated columns for one symbol (the Analysis block)."""
        key = f"metrics|{symbol}|{self._params_key()}"
        return self._cached(key, lambda: compute_symbol_metrics(
            self.symbol_frame(symbol), self.params()))

    def lookback(self, symbol: str, reference: str = "latest") -> List[Dict[str, Any]]:
        key = f"lookback|{symbol}|{reference}|{self._params_key()}"
        return self._cached(
            key,
            lambda: compute_lookback_summary(
                self.symbol_frame(symbol), self.params(), reference=reference),
        )

    def screener(
        self,
        filters: Optional[DataFilters] = None,
        universe: Optional[Iterable[str]] = None,
    ) -> pd.DataFrame:
        filters = (filters or DataFilters()).normalised()
        as_of = self.resolve_as_of(filters.as_of)
        # The universe and the analytics parameters both change the result, so
        # both belong in the key — otherwise the first call would poison every
        # later one for the rest of the session.
        universe_key = ",".join(sorted(str(s) for s in universe)) if universe else ""
        key = (f"screener|{','.join(filters.symbols)}|{filters.start_date}"
               f"|{filters.end_date}|{as_of}"
               f"|{self._params_key()}|{universe_key}")
        if self.dataset_is_large():
            return self._cached(
                key,
                lambda: self._screener_from_sql(filters, as_of, universe),
            )
        frame = self.frame(filters) if (filters.symbols or filters.start_date or filters.end_date) \
            else self.frame()
        return self._cached(
            key,
            lambda: compute_screener(
                frame, as_of=as_of, params=self.params(), universe=universe),
        )

    def _screener_from_sql(self, filters: DataFilters, as_of: Optional[_dt.date],
                           universe: Optional[Iterable[str]]) -> pd.DataFrame:
        """Screener from per-symbol SQL aggregates (no full frame in memory)."""
        averages, today, resolved = self.backend.screener_aggregates(
            as_of, self._scope(filters))
        return build_screener(averages, today, resolved, self.params(), universe)

    def overview(self) -> Dict[str, Any]:
        if self.dataset_is_large():
            return self._cached("overview|sql", self.backend.overview)
        return self._cached("overview", lambda: dashboard_overview(self.frame()))

    def movers(self, as_of: Optional[_dt.date] = None, limit: int = 10) -> Dict[str, pd.DataFrame]:
        as_of = self.resolve_as_of(as_of)
        key = f"movers|{as_of}|{limit}"
        if self.dataset_is_large():
            return self._cached(
                key, lambda: self.backend.movers(as_of, limit=limit))
        return self._cached(key, lambda: top_movers(self.frame(), as_of, limit))

    def classification(self, filters: Optional[DataFilters] = None) -> Dict[str, Dict[str, int]]:
        return classification_counts(self.screener(filters))

    def trend(self, metric: str, filters: Optional[DataFilters] = None,
              aggregation: str = "avg") -> pd.DataFrame:
        """Time series of ``metric`` grouped by trade date."""
        filters = (filters or DataFilters()).normalised()
        if metric not in query_engine.PRICE_COLUMNS:
            return pd.DataFrame(columns=["trade_date", "value"])
        if self.dataset_is_large():
            key = f"trend|sql|{metric}|{aggregation}|{self._filter_key(filters)}"
            return self._cached(
                key,
                lambda: self.backend.trend(metric, self._scope(filters), aggregation))
        frame = self.frame(filters)
        if frame.empty or metric not in frame.columns:
            return pd.DataFrame(columns=["trade_date", "value"])
        df = frame[["trade_date", metric]].copy()
        df[metric] = pd.to_numeric(df[metric], errors="coerce")
        grouped = df.groupby("trade_date")[metric]
        series = {
            "sum": grouped.sum,
            "avg": grouped.mean,
            "min": grouped.min,
            "max": grouped.max,
            "count": grouped.count,
        }.get(aggregation, grouped.mean)()
        out = series.reset_index()
        out.columns = ["trade_date", "value"]
        out["trade_date"] = pd.to_datetime(out["trade_date"])
        return out.sort_values("trade_date").reset_index(drop=True)

    #: Columns that only exist in a screener result, not in the price frame.
    SCREENER_COLUMNS = ("d_ind", "dv1", "dv2", "today_delivery", "avg_delivery",
                        "today_volume", "avg_volume", "today_trades", "avg_trades")

    def group_by(self, metric: str, dimension: str,
                 filters: Optional[DataFilters] = None,
                 aggregation: str = "count", limit: Optional[int] = None,
                 source: Optional[str] = None) -> pd.DataFrame:
        """Aggregate ``metric`` by ``dimension``.

        ``source`` selects the frame explicitly; when it is omitted the screener
        result is used if either name is a screener column, otherwise the price
        frame.
        """
        filters = (filters or DataFilters()).normalised()
        if source is None:
            source = ("screener" if dimension in self.SCREENER_COLUMNS
                      or metric in self.SCREENER_COLUMNS else "prices")
        if (source == "prices" and self.dataset_is_large()
                and dimension in query_engine.GROUPABLE
                and metric in query_engine.PRICE_COLUMNS):
            key = (f"group|sql|{metric}|{dimension}|{aggregation}|{limit}"
                   f"|{self._filter_key(filters)}")
            return self._cached(
                key,
                lambda: self.backend.group_by(
                    metric, dimension, self._scope(filters), aggregation, limit))
        frame = self.screener(filters) if source == "screener" else self.frame(filters)

        if frame.empty or dimension not in frame.columns or metric not in frame.columns:
            return pd.DataFrame(columns=[dimension, "value"])

        columns = [dimension] if dimension == metric else [dimension, metric]
        df = frame[columns].copy()

        if aggregation == "count":
            out = df.groupby(dimension).size().reset_index(name="value")
        else:
            values = pd.to_numeric(df[metric], errors="coerce")
            grouped = values.groupby(df[dimension])
            fn = {
                "sum": grouped.sum,
                "avg": grouped.mean,
                "min": grouped.min,
                "max": grouped.max,
            }.get(aggregation, grouped.mean)()
            out = fn.reset_index()
            out.columns = [dimension, "value"]

        out = out.sort_values("value", ascending=False).reset_index(drop=True)
        if limit:
            out = out.head(int(limit))
        return out

    # ------------------------------------------------------------------
    # history / audit
    # ------------------------------------------------------------------

    def batches(self, limit: int = 200) -> List[ImportBatch]:
        with self.db.session() as session:
            return list(session.scalars(
                select(ImportBatch).order_by(ImportBatch.started_at.desc()).limit(limit)))

    def batch_errors(self, batch_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        with self.db.session() as session:
            rows = session.scalars(
                select(ImportError_).where(ImportError_.batch_id == batch_id)
                .order_by(ImportError_.row_number).limit(limit))
            return [{
                "row": r.row_number, "column": r.column_name, "severity": r.severity,
                "message": r.message, "value": r.raw_value,
            } for r in rows]

    def audit_log(self, category: Optional[str] = None, severity: Optional[str] = None,
                  search: str = "", limit: int = 500,
                  offset: int = 0) -> List[Dict[str, Any]]:
        with self.db.session() as session:
            stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            if category and category != "all":
                stmt = stmt.where(AuditLog.category == category)
            if severity and severity != "all":
                stmt = stmt.where(AuditLog.severity == severity)
            if search:
                stmt = stmt.where(AuditLog.message.like(f"%{search}%"))
            stmt = stmt.limit(limit).offset(offset)
            return [{
                "id": row.id,
                "time": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "category": row.category,
                "severity": row.severity,
                "entity": row.entity or "",
                "message": row.message,
                "payload": row.payload,
            } for row in session.scalars(stmt)]

    def audit_categories(self) -> List[str]:
        with self.db.session() as session:
            rows = session.execute(select(AuditLog.category).distinct().order_by(AuditLog.category))
            return [row[0] for row in rows]

    def audit_counts(self) -> Dict[str, int]:
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(AuditLog)) or 0
            warnings = session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.severity == "warning")
            ) or 0
            errors = session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.severity == "error")
            ) or 0
        return {"total": int(total), "warnings": int(warnings), "errors": int(errors)}

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------

    def clear_prices(self) -> Dict[str, int]:
        """Delete every imported market row (the schema and settings survive)."""
        with self.db.session() as session:
            prices = session.scalar(select(func.count()).select_from(Price)) or 0
            session.execute(delete(PriceExtra))
            session.execute(delete(Price))
            session.execute(delete(Stock))
            session.flush()
            stocks = session.scalar(select(func.count()).select_from(Stock)) or 0
        self.invalidate("market data cleared")
        self.db.log("maintenance", f"Cleared {prices} price rows", severity="warning")
        return {"prices_removed": int(prices), "stocks_remaining": int(stocks)}

    def vacuum(self) -> None:
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")

    def extra_columns(self, limit: int = 50) -> List[str]:
        with self.db.session() as session:
            rows = session.execute(
                select(PriceExtra.column_name).distinct().limit(limit))
            return [row[0] for row in rows]


__all__ = ["DataService", "DataFilters"]
