"""Database engine, schema bootstrap, settings store and backup/restore.

The database is a single local SQLite file (no server, no cloud account).  All
access funnels through :class:`Database` so that transactions, PRAGMAs and
backup semantics stay in one place.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pandas as pd
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from models import AppSetting, AuditLog, Base, DashboardComponent, Formula, FormulaVersion, ImportBatch, Price, Stock


def _project_root() -> Path:
    """Where the application keeps its data, logs and exports.

    Under PyInstaller the bundled modules live in a temporary folder that is
    wiped when the application exits, so a frozen build must keep its database
    next to the executable instead.
    """
    if getattr(sys, "frozen", False):                       # pragma: no cover - packaging
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "capital.db"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

MARKET_COLUMNS = (
    "open", "high", "low", "close", "last", "prevclose",
    "tottrdqty", "tottrdval", "totaltrades", "delivery_pct",
)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


class Database:
    """Owns the SQLAlchemy engine and the session factory."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH, echo: bool = False):
        self.path = resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            f"sqlite:///{self.path}",
            echo=echo,
            future=True,
            connect_args={"timeout": 30},
        )
        _apply_sqlite_pragmas(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    # -- schema ----------------------------------------------------------

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            _seed_settings(session)
            _seed_builtin_formulas(session)
            _seed_dashboard(session)
            session.commit()

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    def table_names(self) -> List[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
            return [r[0] for r in rows]

    # -- sessions --------------------------------------------------------

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- backup / restore ------------------------------------------------

    def backup(self, destination: str | Path | None = None) -> Path:
        """Consistent online backup using SQLite's backup API."""
        if not self.path.exists():
            raise FileNotFoundError(f"database not found: {self.path}")
        if destination is None:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            destination = DEFAULT_BACKUP_DIR / f"capital-{stamp}.db"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def restore(self, source: str | Path) -> Path:
        """Replace the live database with ``source``; returns the safety copy."""
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(f"backup not found: {source}")
        safety = None
        if self.path.exists():
            safety = self.backup(DEFAULT_BACKUP_DIR / f"pre-restore-{_dt.datetime.now():%Y%m%d-%H%M%S}.db")
        self.engine.dispose()
        shutil.copy2(source, self.path)
        self.engine = create_engine(f"sqlite:///{self.path}", future=True,
                                    connect_args={"timeout": 30})
        _apply_sqlite_pragmas(self.engine)
        self.Session.configure(bind=self.engine)
        return safety or self.path

    # -- market data access ----------------------------------------------

    def price_frame(
        self,
        symbols: Optional[Sequence[str]] = None,
        start_date: Optional[_dt.date | str] = None,
        end_date: Optional[_dt.date | str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Load the price history as a tidy DataFrame (canonical column names)."""
        stmt = select(
            Stock.symbol, Price.trade_date,
            Price.open, Price.high, Price.low, Price.close, Price.last, Price.prevclose,
            Price.tottrdqty, Price.tottrdval, Price.totaltrades, Price.delivery_pct,
        ).join(Stock, Price.stock_id == Stock.id)

        if symbols:
            wanted = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if wanted:
                stmt = stmt.where(Stock.symbol.in_(wanted))
        if start_date is not None:
            stmt = stmt.where(Price.trade_date >= _as_date(start_date))
        if end_date is not None:
            stmt = stmt.where(Price.trade_date <= _as_date(end_date))
        stmt = stmt.order_by(Stock.symbol, Price.trade_date)
        if limit:
            stmt = stmt.limit(int(limit))

        with self.engine.connect() as conn:
            try:
                frame = pd.read_sql(stmt, conn)
            except SQLAlchemyError:
                frame = pd.DataFrame()

        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        else:
            frame = pd.DataFrame(columns=[
                "symbol", "trade_date", "open", "high", "low", "close", "last",
                "prevclose", "tottrdqty", "tottrdval", "totaltrades", "delivery_pct",
            ])
        return frame

    def symbol_list(self) -> List[str]:
        try:
            with self.session() as session:
                return [s.symbol for s in session.scalars(select(Stock).order_by(Stock.symbol))]
        except SQLAlchemyError:
            return []

    def date_list(self) -> List[_dt.date]:
        """Every distinct trading date, oldest first.

        Like :meth:`symbol_list` this tolerates a database whose schema has not
        been created yet (a fresh install, a deleted file) so callers never have
        to special-case "no data" versus "no tables".
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(Price.trade_date).distinct().order_by(Price.trade_date)
                )
                out: List[_dt.date] = []
                for (value,) in rows:
                    if value is None:
                        continue
                    out.append(value if isinstance(value, _dt.date)
                               else pd.to_datetime(value).date())
                return out
        except SQLAlchemyError:
            return []

    def summary_counts(self) -> Dict[str, Any]:
        empty = {"stocks": 0, "records": 0, "latest_date": None,
                 "earliest_date": None, "batches": 0}
        try:
            with self.session() as session:
                stocks = session.scalar(select(func.count()).select_from(Stock)) or 0
                rows = session.scalar(select(func.count()).select_from(Price)) or 0
                latest = session.scalar(select(func.max(Price.trade_date)))
                earliest = session.scalar(select(func.min(Price.trade_date)))
                batches = session.scalar(select(func.count()).select_from(ImportBatch)) or 0
        except SQLAlchemyError:
            # The file exists but has no schema yet (e.g. ``--db`` pointed at a
            # brand-new path).  Report an empty dataset instead of raising.
            return empty
        return {
            "stocks": int(stocks),
            "records": int(rows),
            "latest_date": latest,
            "earliest_date": earliest,
            "batches": int(batches),
        }

    def log(self, category: str, message: str, severity: str = "info",
            entity: Optional[str] = None, payload: Optional[dict] = None,
            session: Optional[Session] = None) -> None:
        """Write one audit entry.

        Pass ``session`` when a transaction is already open.  SQLite permits a
        single writer, so logging through a *second* connection while a write
        transaction is in flight would block until the lock times out; handing
        over the open session keeps the entry in the same transaction.
        """
        entry = AuditLog(category=category, severity=severity, entity=entity,
                         message=message, payload=payload)
        if session is not None:
            session.add(entry)
            return
        with self.session() as own:
            own.add(entry)


# --------------------------------------------------------------------------
# PRAGMAs / seeds
# --------------------------------------------------------------------------


def _apply_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()


def _as_date(value: _dt.date | str) -> _dt.date:
    if isinstance(value, _dt.date):
        return value
    return pd.to_datetime(value).date()


def _seed_settings(session: Session) -> None:
    defaults_file = PROJECT_ROOT / "config" / "default_settings.json"
    if not defaults_file.exists():
        return
    defaults = json.loads(defaults_file.read_text(encoding="utf-8"))
    for key, value in defaults.items():
        if session.get(AppSetting, key) is None:
            session.add(AppSetting(key=key, value=value))


BUILTIN_FORMULAS: List[Dict[str, Any]] = [
    {
        "name": "pct_change",
        "description": "% change of LAST versus PREVCLOSE, expressed as a percentage.",
        "category": "price",
        "output_column": "pct_change_pct",
        "expression": "(LAST - PREVCLOSE) / PREVCLOSE * 100",
        "data_type": "float",
        "display_format": "0.00",
    },
    {
        "name": "avg_price",
        "description": "Unweighted average of the session's OPEN, HIGH, LOW and CLOSE.",
        "category": "price",
        "output_column": "avg_price",
        "expression": "(OPEN + HIGH + LOW + CLOSE) / 4",
        "data_type": "float",
        "display_format": "0.00",
    },
    {
        "name": "close_ma_5",
        "description": "Trailing five-session moving average of the close price.",
        "category": "rolling",
        "output_column": "close_ma_5",
        "expression": "AVERAGE(CLOSE, 5)",
        "data_type": "float",
        "display_format": "0.00",
    },
    {
        "name": "close_ma_25",
        "description": "Trailing twenty-five-session moving average of the close price.",
        "category": "rolling",
        "output_column": "close_ma_25",
        "expression": "ROLLING_AVG(CLOSE, 25)",
        "data_type": "float",
        "display_format": "0.00",
    },
    {
        "name": "deliverable_qty",
        "description": "Deliverable quantity = delivery % applied to traded quantity.",
        "category": "volume",
        "output_column": "deliverable_qty",
        "expression": "DELIVERY % * TOTTRDQTY / 100",
        "data_type": "float",
        "display_format": "#,##0",
    },
    {
        "name": "qty_per_trade",
        "description": "Average traded quantity per trade (the workbook's 'Action').",
        "category": "volume",
        "output_column": "qty_per_trade",
        "expression": "SAFE_DIV(TOTTRDQTY, TOTALTRADES)",
        "data_type": "float",
        "display_format": "0.00",
    },
    {
        "name": "delivery_band",
        "description": "Delivery-percentage band label (corrected closed buckets).",
        "category": "classification",
        "output_column": "delivery_band",
        "expression": (
            'IF(DELIVERY % < 40, "Low Del", '
            'IF(DELIVERY % <= 50, "40% Plus", '
            'IF(DELIVERY % <= 60, "50% Plus", '
            'IF(DELIVERY % <= 80, "60% Plus", "80% Plus"))))'
        ),
        "data_type": "str",
        "display_format": None,
    },
]


def _seed_builtin_formulas(session: Session) -> None:
    for spec in BUILTIN_FORMULAS:
        if session.scalar(select(Formula).where(Formula.name == spec["name"])) is not None:
            continue
        formula = Formula(
            name=spec["name"],
            description=spec["description"],
            category=spec["category"],
            output_column=spec["output_column"],
            expression=spec["expression"],
            data_type=spec["data_type"],
            display_format=spec.get("display_format"),
            enabled=True,
            builtin=True,
            current_version=1,
        )
        session.add(formula)
        session.flush()
        session.add(FormulaVersion(
            formula_id=formula.id, version=1, expression=formula.expression,
            change_note="Initial built-in definition", snapshot=spec,
        ))


DEFAULT_DASHBOARD: List[Dict[str, Any]] = [
    {"position": 0, "kind": "kpi", "title": "Unique Stocks", "metric": "unique_stocks",
     "aggregation": "count", "source": "overview", "options": {"format": "#,##0"}},
    {"position": 1, "kind": "kpi", "title": "Trading Records", "metric": "total_records",
     "aggregation": "count", "source": "overview", "options": {"format": "#,##0"}},
    {"position": 2, "kind": "kpi", "title": "Latest Trading Date", "metric": "latest_date",
     "aggregation": "max", "source": "overview", "options": {"format": "date"}},
    {"position": 3, "kind": "kpi", "title": "Total Volume", "metric": "total_volume",
     "aggregation": "sum", "source": "overview", "options": {"format": "#,##0"}},
    {"position": 4, "kind": "kpi", "title": "Total Traded Value", "metric": "total_value",
     "aggregation": "sum", "source": "overview", "options": {"format": "#,##0.00"}},
    {"position": 5, "kind": "kpi", "title": "Average Delivery %", "metric": "avg_delivery",
     "aggregation": "avg", "source": "overview", "options": {"format": "0.00"}},
    {"position": 6, "kind": "line", "title": "Closing Price Trend", "metric": "close",
     "dimension": "trade_date", "aggregation": "avg", "source": "prices",
     "options": {"filters_control": True}},
    {"position": 7, "kind": "line", "title": "Delivery % Trend", "metric": "delivery_pct",
     "dimension": "trade_date", "aggregation": "avg", "source": "prices",
     "options": {"filters_control": True}},
    {"position": 8, "kind": "bar", "title": "Trading Volume Trend", "metric": "tottrdqty",
     "dimension": "trade_date", "aggregation": "sum", "source": "prices",
     "options": {"filters_control": True}},
    {"position": 9, "kind": "table", "title": "Top Gainers", "metric": "pct_change",
     "dimension": "symbol", "aggregation": "max", "source": "derived",
     "options": {"limit": 10, "filters_control": True}},
    {"position": 10, "kind": "table", "title": "Top Losers", "metric": "pct_change",
     "dimension": "symbol", "aggregation": "min", "source": "derived",
     "options": {"limit": 10, "filters_control": True}},
    {"position": 11, "kind": "pie", "title": "Trade-count Classification (D. Ind)",
     "metric": "d_ind", "dimension": "d_ind", "aggregation": "count", "source": "screener",
     "options": {"filters_control": True}},
]


def _seed_dashboard(session: Session) -> None:
    existing = session.scalar(select(func.count()).select_from(DashboardComponent)) or 0
    if existing:
        return
    for spec in DEFAULT_DASHBOARD:
        session.add(DashboardComponent(
            layout_name="Default",
            position=spec["position"],
            kind=spec["kind"],
            title=spec["title"],
            metric=spec["metric"],
            dimension=spec.get("dimension"),
            aggregation=spec["aggregation"],
            source=spec["source"],
            filters=None,
            options=spec.get("options"),
            visible=True,
        ))


# --------------------------------------------------------------------------
# Convenience accessors used by the UI
# --------------------------------------------------------------------------


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


__all__ = [
    "Database",
    "PROJECT_ROOT",
    "DEFAULT_DB_PATH",
    "DEFAULT_BACKUP_DIR",
    "MARKET_COLUMNS",
    "BUILTIN_FORMULAS",
    "DEFAULT_DASHBOARD",
    "resolve_path",
    "get_setting",
    "set_setting",
]
