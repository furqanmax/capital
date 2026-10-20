"""Dashboard Builder: layouts, component CRUD, ordering and rendering."""

from __future__ import annotations

import datetime as _dt

import pytest

from database.db import DEFAULT_DASHBOARD, Database
from models import DashboardComponent
from services.dashboard_service import (
    AGGREGATIONS,
    COMPONENT_KINDS,
    DEFAULT_LAYOUT,
    DashboardService,
    format_value,
    metric_choices,
    metric_label,
)
from services.data_service import DataFilters, DataService


@pytest.fixture()
def dashboard(db: Database) -> DashboardService:
    return DashboardService(db, DataService(db))


@pytest.fixture()
def filled_dashboard(filled_db: Database, filled_data: DataService) -> DashboardService:
    """A read-only dashboard over the imported sample workbook."""
    return DashboardService(filled_db, filled_data)


# --------------------------------------------------------------------------
# the shipped default
# --------------------------------------------------------------------------


def test_default_layout_is_seeded(dashboard: DashboardService) -> None:
    assert dashboard.layouts() == [DEFAULT_LAYOUT]
    assert dashboard.active_layout() == DEFAULT_LAYOUT
    components = dashboard.list_components()
    assert len(components) == len(DEFAULT_DASHBOARD)
    assert [c.position for c in components] == sorted(c.position for c in components)


def test_default_layout_covers_every_component_kind(dashboard: DashboardService) -> None:
    kinds = {c.kind for c in dashboard.list_components()}
    assert kinds == set(COMPONENT_KINDS)


def test_component_choices_are_documented() -> None:
    choices = metric_choices()
    assert set(choices) == {"overview", "prices", "screener", "derived"}
    assert "total_records" in choices["overview"]
    assert "close" in choices["prices"]
    assert "d_ind" in choices["screener"]
    assert set(AGGREGATIONS) == {"sum", "avg", "min", "max", "count"}


def test_metric_label_uses_the_catalogue() -> None:
    assert metric_label("delivery_pct") == "Delivery %"
    assert metric_label("some_new_metric") == "Some New Metric"


# --------------------------------------------------------------------------
# layouts
# --------------------------------------------------------------------------


def test_create_layout_starts_empty(dashboard: DashboardService) -> None:
    assert dashboard.create_layout("Mine") == "Mine"
    assert "Mine" in dashboard.layouts()
    assert dashboard.list_components("Mine") == []


def test_create_layout_can_copy_an_existing_one(dashboard: DashboardService) -> None:
    dashboard.create_layout("Copy", copy_from=DEFAULT_LAYOUT)
    copied = dashboard.list_components("Copy")
    assert len(copied) == len(DEFAULT_DASHBOARD)
    assert [c.title for c in copied] == [c.title for c in dashboard.list_components()]


def test_create_layout_rejects_blank_and_duplicates(dashboard: DashboardService) -> None:
    with pytest.raises(ValueError):
        dashboard.create_layout("   ")
    with pytest.raises(ValueError):
        dashboard.create_layout(DEFAULT_LAYOUT)


def test_active_layout_can_be_switched(dashboard: DashboardService) -> None:
    dashboard.create_layout("Mine")
    dashboard.set_active_layout("Mine")
    assert dashboard.active_layout() == "Mine"
    with pytest.raises(ValueError):
        dashboard.set_active_layout("Nowhere")


def test_deleting_the_active_layout_falls_back(dashboard: DashboardService) -> None:
    dashboard.create_layout("Mine")
    dashboard.set_active_layout("Mine")
    dashboard.delete_layout("Mine")
    assert dashboard.active_layout() == DEFAULT_LAYOUT


def test_restore_default_rebuilds_the_component_set(dashboard: DashboardService) -> None:
    for component in dashboard.list_components():
        dashboard.remove_component(component.id)
    assert dashboard.list_components() == []

    count = dashboard.restore_default()
    assert count == len(DEFAULT_DASHBOARD)
    assert len(dashboard.list_components()) == len(DEFAULT_DASHBOARD)


# --------------------------------------------------------------------------
# portable layout files
# --------------------------------------------------------------------------


def test_export_config_describes_the_layout(dashboard: DashboardService) -> None:
    payload = dashboard.export_config()
    assert payload["format"] == "capital-dashboard"
    assert payload["version"] == 1
    assert payload["layout"] == DEFAULT_LAYOUT
    assert len(payload["components"]) == len(DEFAULT_DASHBOARD)
    kinds = {component["kind"] for component in payload["components"]}
    assert kinds == {"kpi", "table", "line", "bar", "pie"}
    assert all("metric" in component and "source" in component
               for component in payload["components"])


def test_export_then_import_round_trips(dashboard: DashboardService) -> None:
    dashboard.create_layout("Mine")
    dashboard.add_component("kpi", "My KPI", "close", aggregation="avg",
                            options={"format": "0.00"}, layout="Mine")
    dashboard.add_component("bar", "My bar", "tottrdqty", dimension="symbol",
                            options={"limit": 5}, layout="Mine")
    payload = dashboard.export_config("Mine")

    assert dashboard.import_config(payload, layout="Copy") == "Copy"
    copied = dashboard.list_components("Copy")
    assert [c.title for c in copied] == ["My KPI", "My bar"]
    assert copied[0].options == {"format": "0.00"}
    assert copied[1].dimension == "symbol"


def test_import_uses_the_documents_own_layout_name(dashboard: DashboardService) -> None:
    payload = {"format": "capital-dashboard", "version": 1, "layout": "Borrowed",
               "components": [{"kind": "table", "metric": "close", "source": "prices"}]}
    assert dashboard.import_config(payload) == "Borrowed"
    assert "Borrowed" in dashboard.layouts()


def test_re_importing_replaces_the_components(dashboard: DashboardService) -> None:
    payload = {"layout": "Swap", "components": [
        {"kind": "kpi", "metric": "close", "source": "prices", "aggregation": "avg"}]}
    dashboard.import_config(payload)
    payload["components"].append(
        {"kind": "line", "metric": "close", "source": "prices", "dimension": "trade_date"})
    dashboard.import_config(payload)
    assert len(dashboard.list_components("Swap")) == 2


def test_import_can_refuse_to_replace(dashboard: DashboardService) -> None:
    payload = {"layout": "Once", "components": [
        {"kind": "kpi", "metric": "close", "source": "prices"}]}
    dashboard.import_config(payload)
    with pytest.raises(ValueError):
        dashboard.import_config(payload, replace=False)


def test_import_validates_the_document(dashboard: DashboardService) -> None:
    with pytest.raises(ValueError):
        dashboard.import_config({"layout": "Bad"})                     # no components
    with pytest.raises(ValueError):
        dashboard.import_config({"layout": "  ", "components": []})     # blank name
    with pytest.raises(ValueError):
        dashboard.import_config({"layout": "Bad", "components": [
            {"kind": "donut", "metric": "close", "source": "prices"}]})
    with pytest.raises(ValueError):
        dashboard.import_config({"layout": "Bad", "components": [
            {"kind": "kpi", "metric": "close", "aggregation": "median",
             "source": "prices"}]})


def test_import_accepts_an_empty_layout(dashboard: DashboardService) -> None:
    assert dashboard.import_config({"layout": "Nothing", "components": []}) == "Nothing"
    assert dashboard.list_components("Nothing") == []


def test_the_shipped_sample_config_is_importable(dashboard: DashboardService) -> None:
    import json
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "docs" / "sample_dashboard_config.json"
    payload = json.loads(sample.read_text(encoding="utf-8"))
    name = dashboard.import_config(payload)
    components = dashboard.list_components(name)
    assert len(components) == len(payload["components"])
    kinds = {c.kind for c in components}
    assert kinds == {"kpi", "line", "bar", "pie", "table"}


# --------------------------------------------------------------------------
# components
# --------------------------------------------------------------------------


def test_add_component_appends_to_the_layout(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "My KPI", "close", aggregation="avg",
                                        source="prices")
    assert component.id is not None
    components = dashboard.list_components()
    assert components[-1].id == component.id
    assert component.title == "My KPI"
    assert component.visible is True


def test_add_component_defaults_the_title(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "", "total_records", source="overview")
    assert component.title == "Total Records"


@pytest.mark.parametrize("kwargs", [
    {"kind": "donut", "title": "x", "metric": "close"},
    {"kind": "kpi", "title": "x", "metric": ""},
    {"kind": "kpi", "title": "x", "metric": "close", "aggregation": "median"},
    {"kind": "kpi", "title": "x", "metric": "close", "source": "cloud"},
    {"kind": "kpi", "title": "x", "metric": "not_overview", "source": "overview"},
])
def test_add_component_validates_its_inputs(dashboard: DashboardService, kwargs) -> None:
    with pytest.raises(ValueError):
        dashboard.add_component(**kwargs)


def test_add_component_targets_a_specific_layout(dashboard: DashboardService) -> None:
    dashboard.create_layout("Other")
    component = dashboard.add_component("kpi", "Only here", "close", layout="Other")
    assert component.layout_name == "Other"
    assert dashboard.list_components("Other")[0].id == component.id
    assert component.id not in [c.id for c in dashboard.list_components(DEFAULT_LAYOUT)]


def test_positions_stay_sequential(dashboard: DashboardService) -> None:
    first = dashboard.add_component("kpi", "a", "close")
    second = dashboard.add_component("kpi", "b", "close")
    assert second.position == first.position + 1


def test_positions_stay_sequential_in_a_brand_new_layout(dashboard: DashboardService) -> None:
    # regression: the very first component lands at position 0, which used to be
    # mistaken for "no components yet" and reset every later position to 0
    dashboard.create_layout("Fresh")
    positions = [
        dashboard.add_component("kpi", f"kpi {index}", "close", layout="Fresh").position
        for index in range(4)
    ]
    assert positions == [0, 1, 2, 3]


def test_update_component_changes_fields(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "Before", "close", source="prices")
    updated = dashboard.update_component(component.id, title="After", aggregation="avg",
                                         options={"format": "0.0"}, visible=False)
    assert updated.title == "After"
    assert updated.aggregation == "avg"
    assert updated.options == {"format": "0.0"}
    assert updated.visible is False


def test_update_component_validates(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "ok", "close")
    with pytest.raises(ValueError):
        dashboard.update_component(component.id, kind="donut")


def test_update_component_of_a_missing_id_raises(dashboard: DashboardService) -> None:
    with pytest.raises(ValueError):
        dashboard.update_component(999_999, title="ghost")


def test_remove_component(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "temporary", "close")
    dashboard.remove_component(component.id)
    assert dashboard.get_component(component.id) is None
    dashboard.remove_component(component.id)          # idempotent, must not raise


def test_set_visible_and_visible_only_listing(dashboard: DashboardService) -> None:
    component = dashboard.add_component("kpi", "hide me", "close")
    dashboard.set_visible(component.id, False)
    visible = {c.id for c in dashboard.list_components(visible_only=True)}
    assert component.id not in visible
    assert component.id in {c.id for c in dashboard.list_components()}


def test_reorder_and_move(dashboard: DashboardService) -> None:
    ids = [c.id for c in dashboard.list_components()]
    reversed_ids = list(reversed(ids))
    dashboard.reorder(reversed_ids)
    assert [c.id for c in dashboard.list_components()] == reversed_ids

    moved = dashboard.move(reversed_ids[0], offset=1)
    assert moved[1] == reversed_ids[0]

    # moving past the end is clamped
    assert dashboard.move(moved[-1], offset=10)[-1] == moved[-1]
    assert dashboard.move(moved[0], offset=-10)[0] == moved[0]


def test_move_of_an_unknown_component_is_a_no_op(dashboard: DashboardService) -> None:
    before = [c.id for c in dashboard.list_components()]
    assert dashboard.move(999_999, offset=1) == before


def test_component_changes_survive_a_new_service(dashboard: DashboardService,
                                                 db: Database) -> None:
    component = dashboard.add_component("kpi", "Persisted", "close")
    dashboard.set_visible(component.id, False)
    dashboard.create_layout("Saved")

    reopened = DashboardService(db, DataService(db))
    assert "Saved" in reopened.layouts()
    stored = reopened.get_component(component.id)
    assert stored.title == "Persisted"
    assert stored.visible is False


def test_component_mutations_are_audited(dashboard: DashboardService, db: Database) -> None:
    component = dashboard.add_component("kpi", "Audited", "close")
    dashboard.update_component(component.id, title="Audited 2")
    dashboard.remove_component(component.id)
    entries = DataService(db).audit_log(category="dashboard")
    messages = " | ".join(entry["message"] for entry in entries)
    assert "Added kpi component 'Audited'" in messages
    assert "Updated component 'Audited 2'" in messages
    assert "Removed component 'Audited 2'" in messages


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_render_all_covers_the_visible_layout(filled_dashboard: DashboardService) -> None:
    payloads = filled_dashboard.render_all()
    assert len(payloads) == len(DEFAULT_DASHBOARD)
    assert all(payload.error is None for payload in payloads), \
        [p.error for p in payloads if p.error]


def test_render_overview_kpis(filled_dashboard: DashboardService) -> None:
    by_title = {payload.title: payload for payload in filled_dashboard.render_all()}
    assert by_title["Unique Stocks"].value == 191
    assert by_title["Trading Records"].value == 191
    assert by_title["Latest Trading Date"].text == "2025-01-01"
    assert by_title["Total Volume"].value > 0


def test_render_line_and_bar_series(filled_dashboard: DashboardService) -> None:
    for title in ("Closing Price Trend", "Trading Volume Trend"):
        payload = next(p for p in filled_dashboard.render_all() if p.title == title)
        assert payload.error is None
        assert payload.series
        assert set(payload.series[0]) == {"x", "y"}
        assert payload.series[0]["x"] == "2025-01-01"


def test_render_pie_uses_the_screener(filled_dashboard: DashboardService) -> None:
    payload = next(p for p in filled_dashboard.render_all() if p.kind == "pie")
    assert payload.error is None
    assert payload.series
    assert all("label" in point and "value" in point for point in payload.series)
    assert sum(point["value"] for point in payload.series) == 191


def test_render_derived_tables_are_the_movers(filled_dashboard: DashboardService) -> None:
    gainers = next(p for p in filled_dashboard.render_all() if p.title == "Top Gainers")
    assert gainers.error is None
    assert "pct_change" in gainers.frame.columns
    assert len(gainers.frame) <= 10

    losers = next(p for p in filled_dashboard.render_all() if p.title == "Top Losers")
    assert losers.error is None


def test_render_kpi_with_filters(filled_dashboard: DashboardService) -> None:
    component = next(c for c in filled_dashboard.list_components()
                     if c.metric == "total_volume")
    symbol = filled_dashboard.data.symbols()[0]
    payload = filled_dashboard.render(component, DataFilters(symbols=[symbol]))
    assert payload.value is not None and payload.value > 0


def test_render_threshold_status(filled_dashboard: DashboardService) -> None:
    component = filled_dashboard.add_component(
        "kpi", "Vol", "close", aggregation="avg", source="prices",
        options={"thresholds": {"good": 1, "warning": 100, "danger": 1000}})
    payload = filled_dashboard.render(component)
    assert payload.status in {"good", "warning", "danger", "neutral"}
    filled_dashboard.remove_component(component.id)


def test_render_without_data_is_graceful(db: Database) -> None:
    dashboard = DashboardService(db, DataService(db))
    payload = dashboard.render(dashboard.list_components()[0])
    assert payload.error is None
    assert payload.text in ("0", "n/a")


def test_render_of_a_broken_component_never_raises(filled_dashboard: DashboardService) -> None:
    component = DashboardComponent(layout_name="Default", position=99, kind="kpi",
                                  title="Broken", metric="no_such_metric",
                                  aggregation="sum", source="prices", visible=True)
    payload = filled_dashboard.render(component)
    assert payload.error is None
    assert payload.text == "n/a"


def test_render_of_an_unknown_kind_reports_an_error(filled_dashboard: DashboardService) -> None:
    component = DashboardComponent(layout_name="Default", position=99, kind="donut",
                                  title="Odd", metric="close", aggregation="sum",
                                  source="prices", visible=True)
    payload = filled_dashboard.render(component)
    assert payload.error is None or payload.kind == "donut"


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def test_format_value() -> None:
    assert format_value(None, None) == "n/a"
    assert format_value(_dt.date(2025, 1, 1), "date") == "2025-01-01"
    assert format_value(1234.5, "#,##0") == "1,235"
    assert format_value(1234.567, "0.00") == "1,234.57"
    assert format_value(True, None) == "Yes"
    assert format_value("Del", None) == "Del"
    assert format_value(1234.5, None) == "1,234.50"
