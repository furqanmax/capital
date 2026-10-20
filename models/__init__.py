"""SQLAlchemy ORM models for the Capital analytics database.

Schema highlights
-----------------

* ``stocks`` is a dimension table; ``prices`` holds one row per
  (symbol, trade date) so re-imports cannot double-count a session.
* Original imported values live in ``prices``; calculated metrics are kept
  separate (they are recomputed by the engine, never overwritten into the raw
  columns).
* ``price_extras`` stores any incoming column that has no canonical mapping so
  nothing from a source file is silently discarded.
* ``import_batches`` / ``import_errors`` provide the import history and
  validation trail required for auditability.
* ``formulas`` / ``formula_versions`` implement the Formula Lab with versioning.
* ``dashboard_components`` / ``filter_presets`` / ``app_settings`` persist user
  customisation across restarts.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every model."""


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------


class Stock(Base, TimestampMixin):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    series: Mapped[Optional[str]] = mapped_column(String(16))
    first_date: Mapped[Optional[_dt.date]] = mapped_column(Date)
    last_date: Mapped[Optional[_dt.date]] = mapped_column(Date)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    prices: Mapped[list["Price"]] = relationship(back_populates="stock", cascade="all, delete-orphan")


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sheet_name: Mapped[Optional[str]] = mapped_column(String(120))
    duplicate_policy: Mapped[str] = mapped_column(String(16), default="skip", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    started_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    finished_at: Mapped[Optional[_dt.datetime]] = mapped_column(DateTime)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    column_mapping: Mapped[Optional[dict]] = mapped_column(JSON)

    errors: Mapped[list["ImportError_"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class Price(Base):
    """One trading session for one symbol — the canonical ``Data`` record."""

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_prices_stock_date"),
        CheckConstraint("tottrdqty IS NULL OR tottrdqty >= 0", name="ck_prices_qty_nonneg"),
        Index("ix_prices_stock_date", "stock_id", "trade_date"),
        Index("ix_prices_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    trade_date: Mapped[_dt.date] = mapped_column(Date, nullable=False)
    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    close: Mapped[Optional[float]] = mapped_column(Float)
    last: Mapped[Optional[float]] = mapped_column(Float)
    prevclose: Mapped[Optional[float]] = mapped_column(Float)
    tottrdqty: Mapped[Optional[float]] = mapped_column(Float)
    tottrdval: Mapped[Optional[float]] = mapped_column(Float)
    totaltrades: Mapped[Optional[float]] = mapped_column(Float)
    delivery_pct: Mapped[Optional[float]] = mapped_column(Float)

    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    source_row: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    stock: Mapped[Stock] = relationship(back_populates="prices")


class PriceExtra(Base):
    """Unmapped source columns, preserved verbatim."""

    __tablename__ = "price_extras"
    __table_args__ = (
        Index("ix_price_extras_lookup", "symbol", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_id: Mapped[Optional[int]] = mapped_column(ForeignKey("prices.id", ondelete="CASCADE"))
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[Optional[_dt.date]] = mapped_column(Date)
    column_name: Mapped[str] = mapped_column(String(200), nullable=False)
    value_text: Mapped[Optional[str]] = mapped_column(Text)


class ImportError_(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    row_number: Mapped[Optional[int]] = mapped_column(Integer)
    column_name: Mapped[Optional[str]] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(16), default="error", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[Optional[str]] = mapped_column(Text)

    batch: Mapped[ImportBatch] = relationship(back_populates="errors")


# --------------------------------------------------------------------------
# Formula Lab
# --------------------------------------------------------------------------


class Formula(Base, TimestampMixin):
    __tablename__ = "formulas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), default="custom", nullable=False)
    output_column: Mapped[str] = mapped_column(String(120), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), default="float", nullable=False)
    null_policy: Mapped[str] = mapped_column(String(16), default="blank", nullable=False)
    display_format: Mapped[Optional[str]] = mapped_column(String(40))
    parameters: Mapped[Optional[dict]] = mapped_column(JSON)
    validation_rules: Mapped[Optional[dict]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    versions: Mapped[list["FormulaVersion"]] = relationship(
        back_populates="formula", cascade="all, delete-orphan", order_by="FormulaVersion.version"
    )


class FormulaVersion(Base):
    __tablename__ = "formula_versions"
    __table_args__ = (UniqueConstraint("formula_id", "version", name="uq_formula_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    formula_id: Mapped[int] = mapped_column(ForeignKey("formulas.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    change_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    formula: Mapped[Formula] = relationship(back_populates="versions")


# --------------------------------------------------------------------------
# Customisation
# --------------------------------------------------------------------------


class DashboardComponent(Base, TimestampMixin):
    __tablename__ = "dashboard_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    layout_name: Mapped[str] = mapped_column(String(80), default="Default", nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)     # kpi|table|line|bar|pie
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    metric: Mapped[str] = mapped_column(String(120), nullable=False)
    dimension: Mapped[Optional[str]] = mapped_column(String(120))
    aggregation: Mapped[str] = mapped_column(String(24), default="sum", nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="prices", nullable=False)
    filters: Mapped[Optional[dict]] = mapped_column(JSON)
    options: Mapped[Optional[dict]] = mapped_column(JSON)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FilterPreset(Base, TimestampMixin):
    __tablename__ = "filter_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(40), default="explorer", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_category", "category", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    entity: Mapped[Optional[str]] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)


__all__ = [
    "Base",
    "Stock",
    "Price",
    "PriceExtra",
    "ImportBatch",
    "ImportError_",
    "Formula",
    "FormulaVersion",
    "DashboardComponent",
    "FilterPreset",
    "AppSetting",
    "AuditLog",
]
