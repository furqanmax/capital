"""Aggregate queries for the dashboard, reports and screener.

SQLite is the system of record.  Loading it into pandas works beautifully for a
few hundred thousand rows and falls over at a hundred million, so every
aggregate the UI needs is also expressed as SQL:

* :class:`SqliteBackend` runs that SQL against SQLite itself — no new
  dependency, no second copy of the data.
* :class:`DuckDbBackend` runs the *same* SQL against a columnar mirror of the
  price table when the optional ``duckdb`` package is installed.  The mirror is
  derived data: it can always be rebuilt from SQLite, and any failure falls back
  to :class:`SqliteBackend`.

Both backends share one query builder (they differ only in the relation they
expose), so the numbers cannot drift between engines.

The mirror is synchronised lazily, once per import, and incrementally: rows are
appended in ``price_id`` order after deleting anything past the watermark, which
makes a re-run after a crash idempotent.  A shrink (a clear or a restore) trips
a full rebuild.
"""

from __future__ import annotations

import datetime as _dt
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd


#: Above this many rows the SQL/columnar paths are used instead of a DataFrame.
#: (Below it, pandas is both faster and easier to keep bit-identical.)
LARGE_DATASET_ROWS = 200_000

#: Canonical numeric columns (mirrors ``analytics.CANONICAL_COLUMNS``).
PRICE_COLUMNS: Tuple[str, ...] = (
    "open", "high", "low", "close", "last", "prevclose",
    "tottrdqty", "tottrdval", "totaltrades", "delivery_pct",
)

#: Columns that may be grouped / aggregated by a report.
GROUPABLE: Tuple[str, ...] = ("symbol", "trade_date") + PRICE_COLUMNS

_AGGREGATES = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX",
               "count": "COUNT"}

#: Rows per batch when copying SQLite -> DuckDB.
SYNC_BATCH = 500_000


def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def _ident(name: str) -> str:
    """Quote an identifier (``last`` is a keyword in SQLite)."""
    return '"' + str(name).replace('"', '""') + '"'


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (_dt.date, _dt.datetime)) else value


@dataclass(frozen=True)
class Scope:
    """The filter set shared by every aggregate query."""

    symbols: Tuple[str, ...] = ()
    start_date: Optional[_dt.date] = None
    end_date: Optional[_dt.date] = None


class _QueryBuilder:
    """The SQL every backend shares; subclasses only supply a relation."""

    name = "sql"

    # -- to be provided by the backend ----------------------------------

    def _run(self, sql: str, params: Sequence[Any]) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError

    def _scalar(self, sql: str, params: Sequence[Any]) -> Any:  # pragma: no cover
        raise NotImplementedError

    @property
    def relation(self) -> str:  # pragma: no cover
        raise NotImplementedError

    def invalidate(self, force: bool = False) -> None:
        """Called after the underlying data changed."""

    def close(self) -> None:
        """Release any resources held by the backend."""

    # -- helpers ---------------------------------------------------------

    def _where(self, scope: Scope, extra: Sequence[str] = ()) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if scope.symbols:
            placeholders = ", ".join("?" for _ in scope.symbols)
            clauses.append(f'"symbol" IN ({placeholders})')
            params.extend(scope.symbols)
        if scope.start_date is not None:
            clauses.append('"trade_date" >= ?')
            params.append(_iso(scope.start_date))
        if scope.end_date is not None:
            clauses.append('"trade_date" <= ?')
            params.append(_iso(scope.end_date))
        clauses.extend(extra)
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params

    def row_count(self, scope: Scope = Scope()) -> int:  # noqa: B008 - immutable default
        where, params = self._where(scope)
        return int(self._scalar(f"SELECT COUNT(*) FROM {self.relation}{where}", params) or 0)

    # -- aggregates ------------------------------------------------------

    def overview(self, scope: Scope = Scope()) -> Dict[str, Any]:  # noqa: B008
        where, params = self._where(scope)
        row = self._run(
            "SELECT COUNT(*) AS total_records, "
            'COUNT(DISTINCT "symbol") AS unique_stocks, '
            'MIN("trade_date") AS earliest_date, MAX("trade_date") AS latest_date, '
            'COALESCE(SUM("tottrdqty"), 0) AS total_volume, '
            'COALESCE(SUM("tottrdval"), 0) AS total_value, '
            'AVG("delivery_pct") AS avg_delivery '
            f"FROM {self.relation}{where}", params)
        symbols = self._run(
            f'SELECT DISTINCT "symbol" AS symbol FROM {self.relation}{where} '
            'ORDER BY "symbol"', params)

        empty = {
            "unique_stocks": 0, "total_records": 0, "latest_date": None,
            "earliest_date": None, "total_volume": 0.0, "total_value": 0.0,
            "avg_delivery": None, "symbols": [],
        }
        if row.empty:
            return empty
        record = row.iloc[0]
        total = int(record["total_records"] or 0)
        if total == 0:
            return empty

        def _date(value: Any) -> Optional[_dt.date]:
            if value is None or (isinstance(value, float) and value != value):
                return None
            return pd.to_datetime(value).date()

        avg_delivery = record["avg_delivery"]
        return {
            "unique_stocks": int(record["unique_stocks"] or 0),
            "total_records": total,
            "latest_date": _date(record["latest_date"]),
            "earliest_date": _date(record["earliest_date"]),
            "total_volume": float(record["total_volume"] or 0.0),
            "total_value": float(record["total_value"] or 0.0),
            "avg_delivery": None if avg_delivery is None or pd.isna(avg_delivery)
            else float(avg_delivery),
            "symbols": [str(s) for s in symbols["symbol"].tolist()],
        }

    def trend(self, metric: str, scope: Scope = Scope(),  # noqa: B008
              aggregation: str = "avg") -> pd.DataFrame:
        if metric not in PRICE_COLUMNS:
            raise ValueError(f"cannot group {metric!r} by trade date")
        function = _AGGREGATES.get(aggregation)
        if function is None:
            raise ValueError(f"unknown aggregation {aggregation!r}")
        column = _ident(metric)
        target = "COUNT(*)" if aggregation == "count" else f"{function}({column})"
        where, params = self._where(scope, ['"trade_date" IS NOT NULL'])
        frame = self._run(
            f'SELECT "trade_date" AS trade_date, {target} AS value '
            f"FROM {self.relation}{where} GROUP BY \"trade_date\" "
            'ORDER BY "trade_date"', params)
        if frame.empty:
            return pd.DataFrame(columns=["trade_date", "value"])
        # Normalise the resolution: DuckDB yields microsecond timestamps, SQLite
        # nanosecond ones, and callers should not care which backend ran.
        frame["trade_date"] = pd.to_datetime(
            frame["trade_date"]).astype("datetime64[ns]")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        return frame.reset_index(drop=True)

    def group_by(self, metric: str, dimension: str, scope: Scope = Scope(),  # noqa: B008
                 aggregation: str = "count",
                 limit: Optional[int] = None) -> pd.DataFrame:
        if dimension not in GROUPABLE:
            raise ValueError(f"cannot group by {dimension!r}")
        if metric not in PRICE_COLUMNS:
            raise ValueError(f"cannot aggregate {metric!r}")
        function = _AGGREGATES.get(aggregation)
        if function is None:
            raise ValueError(f"unknown aggregation {aggregation!r}")
        target = "COUNT(*)" if aggregation == "count" else f"{function}({_ident(metric)})"
        where, params = self._where(scope, [f'{_ident(dimension)} IS NOT NULL'])
        # The secondary sort key only breaks ties (where pandas' order is
        # arbitrary anyway) so repeated runs always return the same table.
        sql = (f'SELECT {_ident(dimension)} AS dimension, {target} AS value '
               f"FROM {self.relation}{where} GROUP BY {_ident(dimension)} "
               f'ORDER BY value DESC, {_ident(dimension)} ASC')
        if limit:
            sql += " LIMIT ?"
            params = [*params, int(limit)]
        frame = self._run(sql, params)
        if frame.empty:
            return pd.DataFrame(columns=[dimension, "value"])
        frame = frame.rename(columns={"dimension": dimension})
        if dimension == "trade_date":
            # The pandas path groups ``datetime.date`` objects, so match it.
            frame[dimension] = pd.to_datetime(frame[dimension]).dt.date
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        return frame.reset_index(drop=True)

    def movers(self, as_of: _dt.date | str | None, scope: Scope = Scope(),  # noqa: B008
               limit: int = 10) -> Dict[str, pd.DataFrame]:
        empty = pd.DataFrame(columns=["symbol", "pct_change"])
        if as_of is None:
            as_of = self._scalar(
                f"SELECT MAX(\"trade_date\") FROM {self.relation}", [])
        if as_of is None:
            return {"gainers": empty, "losers": empty}
        if isinstance(as_of, str):
            as_of = pd.to_datetime(as_of).date()
        where, params = self._where(scope, ['"trade_date" = ?'])
        params = [*params, _iso(as_of)]
        inner = (f'SELECT "symbol" AS symbol, ("last" - "prevclose") '
                 f'/ NULLIF("prevclose", 0) AS pct_change '
                 f"FROM {self.relation}{where}")
        query = (f"SELECT symbol, pct_change FROM ({inner}) AS movers "
                 "WHERE pct_change IS NOT NULL "
                 "ORDER BY pct_change {direction} LIMIT ?")
        gainers = self._run(query.format(direction="DESC"), [*params, int(limit)])
        losers = self._run(query.format(direction="ASC"), [*params, int(limit)])
        return {
            "gainers": gainers.reset_index(drop=True),
            "losers": losers.reset_index(drop=True),
        }

    def screener_aggregates(self, as_of: _dt.date | str | None,
                            scope: Scope = Scope()) -> Tuple[pd.DataFrame, pd.DataFrame, Any]:
        """Per-symbol averages plus the ``as_of`` session, straight from SQL."""
        if isinstance(as_of, str):
            as_of = pd.to_datetime(as_of).date()
        where, params = self._where(scope)
        if as_of is None:
            value = self._scalar(f'SELECT MAX("trade_date") FROM {self.relation}{where}', params)
            as_of = None if value is None else pd.to_datetime(value).date()

        averages = self._run(
            'SELECT "symbol" AS symbol, '
            'AVG("delivery_pct") AS avg_delivery, '
            'AVG("tottrdqty") AS avg_volume, '
            'AVG("totaltrades") AS avg_trades '
            f'FROM {self.relation}{where} GROUP BY "symbol" ORDER BY "symbol"', params)
        averages = averages.set_index("symbol") if not averages.empty else pd.DataFrame(
            columns=["avg_delivery", "avg_volume", "avg_trades"])

        if as_of is None:
            today = pd.DataFrame(columns=["delivery_pct", "tottrdqty", "totaltrades"])
        else:
            today_where, today_params = self._where(scope, ['"trade_date" = ?'])
            today = self._run(
                'SELECT "symbol" AS symbol, "delivery_pct" AS delivery_pct, '
                '"tottrdqty" AS tottrdqty, "totaltrades" AS totaltrades '
                f"FROM {self.relation}{today_where}",
                [*today_params, _iso(as_of)])
            today = (today.set_index("symbol") if not today.empty
                     else pd.DataFrame(columns=["delivery_pct", "tottrdqty", "totaltrades"]))
        return averages, today, as_of


class SqliteBackend(_QueryBuilder):
    """Runs the aggregate SQL against the SQLite file itself."""

    name = "sqlite"

    def __init__(self, database):
        self.db = database

    @property
    def relation(self) -> str:
        columns = ", ".join(
            f"p.{_ident(column)} AS {_ident(column)}" for column in PRICE_COLUMNS)
        return ("(SELECT s.symbol AS symbol, p.trade_date AS trade_date, "
                f"{columns} FROM prices p JOIN stocks s ON s.id = p.stock_id)")

    def _run(self, sql: str, params: Sequence[Any]) -> pd.DataFrame:
        # ``exec_driver_sql`` passes ``?`` placeholders straight to sqlite3 and
        # treats a *tuple* as one positional parameter set (a list would be
        # interpreted as executemany()).
        with self.db.engine.connect() as conn:
            result = conn.exec_driver_sql(sql, tuple(params))
            columns = list(result.keys())
            rows = result.fetchall()
        return pd.DataFrame(rows, columns=columns)

    def _scalar(self, sql: str, params: Sequence[Any]) -> Any:
        with self.db.engine.connect() as conn:
            return conn.exec_driver_sql(sql, tuple(params)).scalar()


class DuckDbBackend(_QueryBuilder):
    """Runs the same SQL against a columnar mirror of the ``prices`` table."""

    name = "duckdb"

    def __init__(self, database, path: str | Path, batch_size: int = SYNC_BATCH):
        import duckdb  # noqa: F401 - guarded by :func:`duckdb_available`

        self.db = database
        self.path = Path(path)
        self.batch_size = max(1_000, int(batch_size))
        self._lock = threading.RLock()
        self._connection = None
        self._dirty = True
        self._force_rebuild = False
        self.last_error: Optional[str] = None

    # -- lifecycle -------------------------------------------------------

    @property
    def relation(self) -> str:
        return "prices"

    def invalidate(self, force: bool = False) -> None:
        self._dirty = True
        self._force_rebuild = self._force_rebuild or bool(force)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                finally:
                    self._connection = None

    def _connect(self):
        with self._lock:
            if self._connection is None:
                import duckdb

                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = duckdb.connect(str(self.path))
                threads = max(1, min(8, (os.cpu_count() or 1)))
                connection.execute(f"PRAGMA threads={threads}")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS mirror_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
                columns = ", ".join(f"{_ident(column)} DOUBLE" for column in PRICE_COLUMNS)
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS prices (symbol VARCHAR, trade_date DATE, "
                    f"{columns}, price_id BIGINT)")
                self._connection = connection
            return self._connection

    # -- mirror maintenance ---------------------------------------------

    def _meta(self, connection, key: str) -> Optional[str]:
        row = connection.execute(
            "SELECT value FROM mirror_meta WHERE key = ?", [key]).fetchone()
        return None if row is None else row[0]

    def _set_meta(self, connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO mirror_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            [key, str(value)])

    def _sqlite_watermark(self) -> int:
        """Highest ``prices.id`` in SQLite (an O(1) index lookup).

        A deleted *individual* row would not move this value, but the only
        delete in the app is "clear all market data" (which empties the table)
        and a restore forces a full rebuild.
        """
        with self.db.engine.connect() as conn:
            return int(conn.exec_driver_sql(
                "SELECT COALESCE(MAX(id), 0) FROM prices").scalar() or 0)

    def _read_batch(self, last_id: int) -> pd.DataFrame:
        """Read the next slice of SQLite rows in ``price_id`` order.

        Raw driver SQL rather than an ORM ``select``: this is a bulk copy, and
        the ORM's per-row result processing roughly doubles the cost.
        """
        columns = ", ".join(f'p.{_ident(column)} AS {_ident(column)}'
                            for column in PRICE_COLUMNS)
        sql = (
            "SELECT p.id AS price_id, s.symbol AS symbol, "
            f"p.trade_date AS trade_date, {columns} "
            "FROM prices p JOIN stocks s ON s.id = p.stock_id "
            "WHERE p.id > ? ORDER BY p.id LIMIT ?"
        )
        with self.db.engine.connect() as conn:
            result = conn.exec_driver_sql(sql, (int(last_id), int(self.batch_size)))
            names = list(result.keys())
            rows = result.fetchall()
        return pd.DataFrame(rows, columns=names)

    def sync(self, force: bool = False) -> None:
        """Bring the mirror up to date with SQLite (incremental)."""
        with self._lock:
            if not self._dirty and not force:
                return
            connection = self._connect()
            last_id = int(self._meta(connection, "last_price_id") or 0)
            meta_count = int(self._meta(connection, "row_count") or 0)

            # The mirror must agree with its own watermark before we trust it;
            # a half-written batch or a truncated file means a rebuild.
            mirror_count, mirror_max = connection.execute(
                "SELECT COUNT(*), MAX(price_id) FROM prices").fetchone()
            mirror_count = int(mirror_count or 0)
            mirror_max = int(mirror_max or 0)
            consistent = mirror_count == meta_count and mirror_max == last_id

            sqlite_max = self._sqlite_watermark()
            if (force or self._force_rebuild or not consistent
                    or sqlite_max < last_id):
                # Rows disappeared (clear / restore) or the mirror is damaged:
                # start over rather than reason about a partial watermark.
                connection.execute("DELETE FROM prices")
                last_id, mirror_count = 0, 0
                self._set_meta(connection, "last_price_id", 0)
                self._set_meta(connection, "row_count", 0)
                self._force_rebuild = False

            columns = ", ".join(_ident(column) for column in PRICE_COLUMNS)
            while True:
                batch = self._read_batch(last_id)
                if batch.empty:
                    break
                last_id = int(batch["price_id"].iloc[-1])
                connection.register("_import_batch", batch)
                try:
                    connection.execute(
                        "INSERT INTO prices (symbol, trade_date, "
                        f"{columns}, price_id) "
                        "SELECT symbol, trade_date, "
                        f"{columns}, price_id FROM _import_batch")
                finally:
                    connection.unregister("_import_batch")
                mirror_count += len(batch)
                self._set_meta(connection, "last_price_id", last_id)
                self._set_meta(connection, "row_count", mirror_count)

            self._set_meta(connection, "row_count", mirror_count)
            self._set_meta(connection, "sqlite_max_id", sqlite_max)
            self._set_meta(connection, "synced_at",
                           _dt.datetime.now().isoformat(timespec="seconds"))
            self._dirty = False

    # -- query execution -------------------------------------------------

    def _run(self, sql: str, params: Sequence[Any]) -> pd.DataFrame:
        with self._lock:
            self.sync()
            connection = self._connect()
            try:
                return connection.execute(sql, list(params)).df()
            except Exception as exc:  # pragma: no cover - defensive
                self.last_error = str(exc)
                raise

    def _scalar(self, sql: str, params: Sequence[Any]) -> Any:
        with self._lock:
            self.sync()
            connection = self._connect()
            row = connection.execute(sql, list(params)).fetchone()
            return None if row is None else row[0]


def analytics_path(database) -> Path:
    """Where the DuckDB mirror lives (next to the SQLite file)."""
    return Path(database.path).parent / "analytics" / (Path(database.path).stem + ".duckdb")


def create_backend(database, prefer_duckdb: bool = True,
                   path: Optional[str | Path] = None):
    """Return the best available backend for ``database``.

    DuckDB is used only when it is installed *and* the mirror opens cleanly;
    anything else transparently degrades to SQLite.
    """
    if prefer_duckdb and duckdb_available():
        target = Path(path) if path else analytics_path(database)
        try:
            backend = DuckDbBackend(database, target)
            backend._connect()
            return backend
        except Exception as exc:  # pragma: no cover - environment dependent
            try:
                database.log("analytics",
                             f"DuckDB mirror unavailable, using SQLite: {exc}",
                             severity="warning")
            except Exception:
                pass
    return SqliteBackend(database)


__all__ = [
    "LARGE_DATASET_ROWS",
    "PRICE_COLUMNS",
    "GROUPABLE",
    "Scope",
    "SqliteBackend",
    "DuckDbBackend",
    "create_backend",
    "analytics_path",
    "duckdb_available",
]
