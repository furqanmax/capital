"""Dashboard Builder.

A dashboard is an ordered list of components persisted in
``dashboard_components``.  Each component declares *what* to show (kind), *which*
number to show (metric), *how* to aggregate it and *where* it comes from.  This
service owns the CRUD, the ordering, the saved layouts and the translation of a
component into a payload the widgets can draw.

Nothing here is hard-coded to the sample workbook: metrics and dimensions are
validated against the columns the engine actually produces, so a dashboard keeps
working after an import with different columns.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy import func, select

from database.db import Database, DEFAULT_DASHBOARD
from models import DashboardComponent

from .analytics import CANONICAL_COLUMNS, COLUMN_LABELS
from .data_service import DataFilters, DataService
from .formatting import round_display

COMPONENT_KINDS = ("kpi", "table", "line", "bar", "pie")
AGGREGATIONS = ("sum", "avg", "min", "max", "count")
SOURCES = ("overview", "prices", "screener", "derived")
DEFAULT_LAYOUT = "Default"

#: Metrics that exist only inside the screener result.
SCREENER_METRICS = (
    "today_delivery", "avg_delivery", "today_volume", "avg_volume", "today_trades",
    "avg_trades", "d_ind", "dv1", "dv2",
)

#: Metrics that exist per session in the price frame.
PRICE_METRICS = tuple(CANONICAL_COLUMNS) + (
    "deliverable_qty", "avg_price", "pct_change", "action", "vol_ma", "trade_indicator",
    "v1", "vol_badge",
)

OVERVIEW_METRICS = (
    "unique_stocks", "total_records", "latest_date", "earliest_date",
    "total_volume", "total_value", "avg_delivery",
)


def metric_choices() -> Dict[str, List[str]]:
    return {
        "overview": list(OVERVIEW_METRICS),
        "prices": list(PRICE_METRICS),
        "screener": list(SCREENER_METRICS),
        "derived": ["pct_change"],
    }


def metric_label(metric: str) -> str:
    return COLUMN_LABELS.get(metric, metric.replace("_", " ").title())


@dataclass
class Payload:
    kind: str
    title: str
    component: DashboardComponent
    value: Any = None
    text: str = ""
    frame: Optional[pd.DataFrame] = None
    series: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "neutral"
    error: Optional[str] = None


class DashboardService:
    """CRUD + rendering for dashboard components."""

    def __init__(self, database: Database, data: Optional[DataService] = None):
        self.db = database
        self.data = data or DataService(database)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # layouts
    # ------------------------------------------------------------------

    def layouts(self) -> List[str]:
        """Every known layout name, ``Default`` first.

        Names are persisted in settings *and* derived from the stored
        components.  A freshly created layout has no components yet, so
        inferring the list from ``dashboard_components`` alone would make it
        invisible — and lose it on the next start.
        """
        from .settings_service import SettingsService

        saved = SettingsService(self.db).get("dashboard.layouts", [])
        names = [str(name) for name in saved] if isinstance(saved, (list, tuple)) else []
        with self.db.session() as session:
            rows = session.execute(
                select(DashboardComponent.layout_name)
                .distinct().order_by(DashboardComponent.layout_name))
            for (name,) in rows:
                if name and name not in names:
                    names.append(name)
        ordered = [DEFAULT_LAYOUT] + [name for name in names if name != DEFAULT_LAYOUT]
        return ordered

    def _remember_layout(self, name: str) -> None:
        from .settings_service import SettingsService

        settings = SettingsService(self.db)
        saved = settings.get("dashboard.layouts", [])
        names = [str(item) for item in saved] if isinstance(saved, (list, tuple)) else []
        if name not in names:
            names.append(name)
            settings.set("dashboard.layouts", names)

    def _forget_layout(self, name: str) -> None:
        from .settings_service import SettingsService

        settings = SettingsService(self.db)
        saved = settings.get("dashboard.layouts", [])
        names = [str(item) for item in saved] if isinstance(saved, (list, tuple)) else []
        remaining = [item for item in names if item != name]
        if remaining != names:
            settings.set("dashboard.layouts", remaining)

    def active_layout(self) -> str:
        from .settings_service import SettingsService

        name = SettingsService(self.db).get("dashboard.active_layout", DEFAULT_LAYOUT)
        return name if name in self.layouts() else self.layouts()[0]

    def set_active_layout(self, name: str) -> None:
        from .settings_service import SettingsService

        if name not in self.layouts():
            raise ValueError(f"unknown layout {name!r}")
        SettingsService(self.db).set("dashboard.active_layout", name)

    def create_layout(self, name: str, copy_from: Optional[str] = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("layout name cannot be blank")
        if name in self.layouts():
            raise ValueError(f"layout {name!r} already exists")
        base = self.list_components(copy_from) if copy_from else []
        with self.db.session() as session:
            for component in base:
                session.add(DashboardComponent(
                    layout_name=name, position=component.position, kind=component.kind,
                    title=component.title, metric=component.metric,
                    dimension=component.dimension, aggregation=component.aggregation,
                    source=component.source, filters=component.filters,
                    options=component.options, visible=component.visible,
                ))
        self._remember_layout(name)
        self.db.log("dashboard", f"Created layout {name!r}", entity=name)
        return name

    def delete_layout(self, name: str) -> None:
        if len(self.layouts()) <= 1:
            raise ValueError("at least one layout must remain")
        with self.db.session() as session:
            for component in session.scalars(
                select(DashboardComponent).where(DashboardComponent.layout_name == name)
            ):
                session.delete(component)
            session.flush()
        from .settings_service import SettingsService

        self._forget_layout(name)
        settings = SettingsService(self.db)
        if settings.get("dashboard.active_layout") == name:
            settings.set("dashboard.active_layout", self.layouts()[0])
        self.db.log("dashboard", f"Deleted layout {name!r}", severity="warning", entity=name)

    def restore_default(self, layout: str = DEFAULT_LAYOUT) -> int:
        """Replace ``layout`` with the shipped default component set."""
        with self.db.session() as session:
            for component in session.scalars(
                select(DashboardComponent).where(DashboardComponent.layout_name == layout)
            ):
                session.delete(component)
            session.flush()
            for spec in DEFAULT_DASHBOARD:
                session.add(DashboardComponent(
                    layout_name=layout, position=spec["position"], kind=spec["kind"],
                    title=spec["title"], metric=spec["metric"],
                    dimension=spec.get("dimension"), aggregation=spec["aggregation"],
                    source=spec["source"], filters=None, options=spec.get("options"),
                    visible=True,
                ))
        self._remember_layout(layout)
        self.db.log("dashboard", f"Restored default layout {layout!r}", entity=layout)
        return len(DEFAULT_DASHBOARD)

    # ------------------------------------------------------------------
    # components
    # ------------------------------------------------------------------

    def list_components(self, layout: Optional[str] = None,
                        visible_only: bool = False) -> List[DashboardComponent]:
        layout = layout or self.active_layout()
        with self.db.session() as session:
            stmt = (select(DashboardComponent)
                    .where(DashboardComponent.layout_name == layout)
                    .order_by(DashboardComponent.position, DashboardComponent.id))
            if visible_only:
                stmt = stmt.where(DashboardComponent.visible.is_(True))
            return list(session.scalars(stmt))

    def get_component(self, component_id: int) -> Optional[DashboardComponent]:
        with self.db.session() as session:
            return session.get(DashboardComponent, component_id)

    def add_component(self, kind: str, title: str, metric: str,
                      aggregation: str = "sum", source: str = "prices",
                      dimension: Optional[str] = None,
                      options: Optional[Dict[str, Any]] = None,
                      filters: Optional[Dict[str, Any]] = None,
                      layout: Optional[str] = None, visible: bool = True) -> DashboardComponent:
        kind, metric, aggregation, source = self._validate(kind, metric, aggregation, source)
        title = (title or metric_label(metric)).strip()
        layout = layout or self.active_layout()
        with self.db.session() as session:
            current = session.scalar(
                select(func.max(DashboardComponent.position))
                .where(DashboardComponent.layout_name == layout))
            # ``current`` is 0 for the first component, so it must not be tested
            # with ``or -1`` — that would hand every later component position 0.
            position = (current if current is not None else -1) + 1
            component = DashboardComponent(
                layout_name=layout, position=int(position), kind=kind, title=title,
                metric=metric, dimension=dimension, aggregation=aggregation,
                source=source, filters=filters or None, options=options or None,
                visible=bool(visible),
            )
            session.add(component)
            session.flush()
            session.refresh(component)
            self.db.log("dashboard", f"Added {kind} component {title!r}",
                        entity=layout, session=session)
            return component

    def update_component(self, component_id: int, **fields: Any) -> DashboardComponent:
        with self.db.session() as session:
            component = session.get(DashboardComponent, component_id)
            if component is None:
                raise ValueError(f"dashboard component {component_id} not found")
            kind = fields.get("kind", component.kind)
            metric = fields.get("metric", component.metric)
            aggregation = fields.get("aggregation", component.aggregation)
            source = fields.get("source", component.source)
            kind, metric, aggregation, source = self._validate(kind, metric, aggregation, source)
            component.kind, component.metric = kind, metric
            component.aggregation, component.source = aggregation, source
            for key in ("title", "dimension", "filters", "options", "visible", "position"):
                if key in fields and fields[key] is not None:
                    setattr(component, key, fields[key])
            session.flush()
            session.refresh(component)
            self.db.log("dashboard", f"Updated component {component.title!r}",
                        session=session)
            return component

    def remove_component(self, component_id: int) -> None:
        with self.db.session() as session:
            component = session.get(DashboardComponent, component_id)
            if component is None:
                return
            title = component.title
            session.delete(component)
            self.db.log("dashboard", f"Removed component {title!r}",
                        severity="warning", session=session)

    def set_visible(self, component_id: int, visible: bool) -> None:
        self.update_component(component_id, visible=bool(visible))

    def reorder(self, component_ids: Sequence[int]) -> None:
        with self.db.session() as session:
            for position, component_id in enumerate(component_ids):
                component = session.get(DashboardComponent, int(component_id))
                if component is not None:
                    component.position = position
        self.db.log("dashboard", f"Reordered {len(component_ids)} components")

    def move(self, component_id: int, offset: int, layout: Optional[str] = None) -> List[int]:
        """Move one component up/down and return the resulting order."""
        order = [c.id for c in self.list_components(layout)]
        if component_id not in order:
            return order
        index = order.index(component_id)
        target = max(0, min(len(order) - 1, index + offset))
        order.insert(target, order.pop(index))
        self.reorder(order)
        return order

    # ------------------------------------------------------------------
    # portable layout files
    # ------------------------------------------------------------------

    def export_config(self, layout: Optional[str] = None) -> Dict[str, Any]:
        """Serialise one layout to a JSON-friendly document.

        The result round-trips through :meth:`import_config`, so a dashboard can
        be version-controlled, shared or shipped as a sample.
        """
        name = layout or self.active_layout()
        return {
            "format": "capital-dashboard",
            "version": 1,
            "layout": name,
            "components": [
                {
                    "position": component.position,
                    "kind": component.kind,
                    "title": component.title,
                    "metric": component.metric,
                    "dimension": component.dimension,
                    "aggregation": component.aggregation,
                    "source": component.source,
                    "options": component.options,
                    "filters": component.filters,
                    "visible": component.visible,
                }
                for component in self.list_components(name)
            ],
        }

    def import_config(self, payload: Mapping[str, Any],
                      layout: Optional[str] = None,
                      replace: bool = True) -> str:
        """Create or replace a layout from a config document; returns its name.

        Every component is validated exactly as an interactive add would be, so a
        malformed file fails before anything is written.
        """
        if not isinstance(payload, Mapping) or not isinstance(payload.get("components"), list):
            raise ValueError("a dashboard config needs a 'components' list")
        name = str(layout or payload.get("layout") or "Imported").strip()
        if not name:
            raise ValueError("layout name cannot be blank")
        specs = payload["components"]

        staged: List[Dict[str, Any]] = []
        for index, spec in enumerate(specs):
            if not isinstance(spec, Mapping):
                raise ValueError(f"component {index} is not an object")
            kind, metric, aggregation, source = self._validate(
                str(spec.get("kind") or ""), str(spec.get("metric") or ""),
                str(spec.get("aggregation") or "sum"), str(spec.get("source") or "prices"))
            staged.append({
                "position": int(spec.get("position", index)),
                "kind": kind, "metric": metric, "aggregation": aggregation,
                "source": source,
                "title": str(spec.get("title") or metric_label(metric)),
                "dimension": spec.get("dimension"), "options": spec.get("options"),
                "filters": spec.get("filters"), "visible": bool(spec.get("visible", True)),
            })

        with self.db.session() as session:
            existing = list(session.scalars(select(DashboardComponent).where(
                DashboardComponent.layout_name == name)))
            if existing and not replace:
                raise ValueError(f"layout {name!r} already exists")
            for component in existing:
                session.delete(component)
            session.flush()
            for spec in staged:
                session.add(DashboardComponent(layout_name=name, **spec))
            self.db.log("dashboard", f"Imported layout {name!r} "
                                     f"({len(staged)} components)",
                        entity=name, session=session)

        self._remember_layout(name)
        return name

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def render(self, component: DashboardComponent,
               filters: Optional[DataFilters] = None) -> Payload:
        filters = (filters or DataFilters()).normalised()
        options = component.options or {}
        try:
            if component.kind == "kpi":
                payload = self._render_kpi(component, filters, options)
            elif component.kind in ("line", "bar"):
                payload = self._render_series(component, filters, options)
            elif component.kind == "pie":
                payload = self._render_pie(component, filters, options)
            else:
                payload = self._render_table(component, filters, options)
        except Exception as exc:  # pragma: no cover - defensive: never break the page
            payload = Payload(kind=component.kind, title=component.title,
                              component=component, error=str(exc))
        return payload

    def render_all(self, filters: Optional[DataFilters] = None,
                   layout: Optional[str] = None,
                   visible_only: bool = True) -> List[Payload]:
        return [self.render(component, filters)
                for component in self.list_components(layout, visible_only=visible_only)]

    # -- individual kinds ------------------------------------------------

    def _render_kpi(self, component, filters, options) -> Payload:
        if component.source == "overview":
            overview = self.data.overview()
            value = overview.get(component.metric)
        elif component.source == "screener":
            screener = self.data.screener(filters)
            value = self._aggregate(screener.get(component.metric), component.aggregation)
        else:
            frame = self.data.frame(filters)
            value = self._aggregate(frame.get(component.metric), component.aggregation)

        fmt = options.get("format") or self._default_format(component.metric)
        return Payload(kind="kpi", title=component.title, component=component, value=value,
                       text=format_value(value, fmt),
                       status=self._status(value, options.get("thresholds")))

    def _render_series(self, component, filters, options) -> Payload:
        trend = self.data.trend(component.metric, filters, component.aggregation)
        series = [{"x": row.trade_date.strftime("%Y-%m-%d"), "y": _number(row.value)}
                  for row in trend.itertuples(index=False)]
        return Payload(kind=component.kind, title=component.title, component=component,
                       series=series, frame=trend)

    def _render_pie(self, component, filters, options) -> Payload:
        dimension = component.dimension or component.metric
        grouped = self.data.group_by(component.metric, dimension, filters,
                                     component.aggregation, options.get("limit"),
                                     source=component.source)
        series = [{"label": str(label), "value": _number(value)}
                  for label, value in grouped.itertuples(index=False)]
        return Payload(kind="pie", title=component.title, component=component,
                       series=series, frame=grouped)

    def _render_table(self, component, filters, options) -> Payload:
        limit = int(options.get("limit") or 10)
        if component.source == "derived" and component.metric == "pct_change":
            movers = self.data.movers(filters.as_of, limit)
            ascending = options.get("order", "desc") == "asc"
            frame = movers["losers"] if ascending else movers["gainers"]
        else:
            dimension = component.dimension or "symbol"
            frame = self.data.group_by(component.metric, dimension, filters,
                                       component.aggregation, limit,
                                       source=component.source)
            ascending = options.get("order", "desc") == "asc"
            if ascending and not frame.empty:
                frame = frame.sort_values("value").reset_index(drop=True)
        if "pct_change" in frame.columns:
            frame = frame.copy()
            frame["pct_change"] = pd.to_numeric(frame["pct_change"], errors="coerce")
        return Payload(kind="table", title=component.title, component=component, frame=frame)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _aggregate(values, aggregation: str):
        if values is None:
            return None
        series = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
        if series.empty:
            return None
        if aggregation == "count":
            return int(series.count())
        if aggregation == "sum":
            return float(series.sum())
        if aggregation == "avg":
            return float(series.mean())
        if aggregation == "min":
            return float(series.min())
        if aggregation == "max":
            return float(series.max())
        return float(series.sum())

    @staticmethod
    def _status(value: Any, thresholds: Optional[Dict[str, Any]]) -> str:
        if not thresholds or value is None or isinstance(value, (str, _dt.date)):
            return "neutral"
        try:
            number = float(value)
            if "danger" in thresholds and number >= float(thresholds["danger"]):
                return "danger"
            if "warning" in thresholds and number >= float(thresholds["warning"]):
                return "warning"
            if "good" in thresholds and number >= float(thresholds["good"]):
                return "good"
        except (TypeError, ValueError):
            return "neutral"
        return "neutral"

    @staticmethod
    def _default_format(metric: str) -> str:
        if metric in ("latest_date", "earliest_date"):
            return "date"
        if metric in ("total_records", "unique_stocks", "total_volume", "today_volume",
                      "avg_volume", "today_trades", "avg_trades", "count"):
            return "#,##0"
        if metric in ("delivery_pct", "avg_delivery", "today_delivery", "d_ind"):
            return "0.00"
        return "#,##0.00"

    @staticmethod
    def _validate(kind: str, metric: str, aggregation: str, source: str):
        kind = str(kind).lower().strip()
        source = str(source).lower().strip()
        aggregation = str(aggregation).lower().strip() or "sum"
        metric = str(metric).strip()
        if kind not in COMPONENT_KINDS:
            raise ValueError(f"component kind must be one of {', '.join(COMPONENT_KINDS)}")
        if source not in SOURCES:
            raise ValueError(f"component source must be one of {', '.join(SOURCES)}")
        if aggregation not in AGGREGATIONS:
            raise ValueError(f"aggregation must be one of {', '.join(AGGREGATIONS)}")
        if not metric:
            raise ValueError("metric cannot be blank")
        if source == "overview" and metric not in OVERVIEW_METRICS:
            raise ValueError(f"{metric!r} is not an overview metric")
        return kind, metric, aggregation, source


# --------------------------------------------------------------------------
# formatting helpers (shared with the widgets)
# --------------------------------------------------------------------------


def format_value(value: Any, fmt: Optional[str]) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, _dt.datetime):
        value = value.date()
    if fmt == "date" or isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d") if isinstance(value, _dt.date) else str(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt in ("0", "int", "#,##0"):
        return round_display(number, 0)
    if fmt in ("0.00", "percent"):
        return round_display(number, 2)
    if fmt in ("0.0000",):
        return round_display(number, 4)
    if fmt in ("#,##0.00", None):
        return round_display(number, 2)
    try:
        return format(number, fmt)
    except (ValueError, TypeError):
        return round_display(number, 2)


def _number(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DashboardService",
    "Payload",
    "COMPONENT_KINDS",
    "AGGREGATIONS",
    "SOURCES",
    "OVERVIEW_METRICS",
    "PRICE_METRICS",
    "SCREENER_METRICS",
    "metric_choices",
    "metric_label",
    "format_value",
    "DEFAULT_LAYOUT",
]
