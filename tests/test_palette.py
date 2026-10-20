"""Workbook colour-scheme tests.

The expected values below are the conditional-formatting fills read out of
``Saif-Stock Market Data File-Sample.xlsm`` (dxfs 151-174 on ``Main`` and the
``Analysis`` legend blocks).  They are duplicated here on purpose: if someone
edits the JSON by accident, this test fails.
"""

from __future__ import annotations

import json

import pytest

from services.palette import (
    auto_text,
    build_scheme,
    contrast_ratio,
    default_palette,
    is_hex,
    luminance,
    normalise_hex,
    palette_candidates,
    scheme_entry,
)
from services.palette_service import PaletteService
from services.settings_service import SettingsService

#: (group, label) -> (fill, text)
WORKBOOK = {
    ("d_ind", "V.High"): ("#7030A0", "#FFFFFF"),
    ("d_ind", "High"): ("#00B050", "#FFFFFF"),
    ("d_ind", "Above"): ("#FFC000", "#000000"),
    ("d_ind", "Low"): ("#FF0000", "#FFFFFF"),
    ("dv1", "BRONZE"): ("#BF9000", "#FFFFFF"),
    ("dv1", "SILVER"): ("#AFABAB", "#000000"),
    ("dv1", "GOLD"): ("#FFC000", "#000000"),
    ("dv2", "Low Del"): ("#FDD7D5", "#C00000"),
    ("dv2", "40% Plus"): ("#00B0F0", "#000000"),
    ("dv2", "50% Plus"): ("#92D050", "#000000"),
    ("dv2", "60% Plus"): ("#00B050", "#FFFFFF"),
    ("dv2", "80% Plus"): ("#7030A0", "#FFFFFF"),
    ("range", "Above"): ("#C6EFCE", "#006100"),
    ("range", "Below"): ("#FFC7CE", "#9C0006"),
}


# --------------------------------------------------------------------------
# the shipped palette
# --------------------------------------------------------------------------


def test_palette_file_ships_with_the_application() -> None:
    assert any(path.exists() for path in palette_candidates())
    data = json.loads(next(p for p in palette_candidates() if p.exists()).read_text())
    assert data["source"].endswith(".xlsm")
    assert len(data["series"]) == 10


def test_defaults_match_the_workbook_fills() -> None:
    document = build_scheme()
    for (group, label), (fill, text) in WORKBOOK.items():
        entry = scheme_entry(document, group, label)
        assert entry is not None, (group, label)
        assert (entry["fill"], entry["text"]) == (fill, text), (group, label)


def test_v1_is_uncoloured_like_the_workbook() -> None:
    document = build_scheme()
    for label in ("J", "Spl", "Trpl", "Dbl", "--", "^"):
        assert scheme_entry(document, "v1", label) is None


def test_main_sheet_silver_wins_over_the_analysis_grey() -> None:
    # Main!N23 paints #AFABAB; Analysis uses #A6A6A6.  The dashboard wins.
    assert scheme_entry(build_scheme(), "dv1", "SILVER")["fill"] == "#AFABAB"


def test_legend_low_del_text_matches_the_rule() -> None:
    entry = scheme_entry(build_scheme(), "dv2", "Low Del")
    assert (entry["fill"], entry["text"]) == ("#FDD7D5", "#C00000")


def test_exact_mode_uses_the_workbook_white_text() -> None:
    document = build_scheme(text_mode="exact")
    assert scheme_entry(document, "d_ind", "Above")["text"] == "#FFFFFF"
    assert scheme_entry(document, "dv1", "SILVER")["text"] == "#FFFFFF"
    # An explicit text colour still wins.
    assert scheme_entry(document, "dv2", "Low Del")["text"] == "#C00000"


# --------------------------------------------------------------------------
# colour maths / validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("#aabbcc", "#AABBCC"),
    ("aabbcc", "#AABBCC"),
    ("#abc", "#AABBCC"),
    ("FFAABBCC", "#AABBCC"),
])
def test_normalise_hex(value: str, expected: str) -> None:
    assert normalise_hex(value) == expected


@pytest.mark.parametrize("value", ["", "red", "#12345", "#GGGGGG", None, 12])
def test_invalid_hex_is_rejected(value) -> None:
    assert not is_hex(value)
    with pytest.raises(ValueError):
        normalise_hex(value)


def test_auto_text_keeps_white_on_saturated_fills() -> None:
    assert auto_text("#FF0000") == "#FFFFFF"      # Low
    assert auto_text("#00B050") == "#FFFFFF"      # High / 60% Plus
    assert auto_text("#7030A0") == "#FFFFFF"      # V.High / 80% Plus
    assert auto_text("#BF9000") == "#FFFFFF"      # BRONZE


def test_auto_text_switches_to_black_on_light_fills() -> None:
    for fill in ("#FFC000", "#AFABAB", "#92D050", "#00B0F0", "#FDD7D5"):
        assert auto_text(fill) == "#000000", fill


def test_contrast_ratio_is_ordered() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == 1.0
    assert contrast_ratio("#000000", "#FFC000") > contrast_ratio("#FFFFFF", "#FFC000")
    assert luminance("#FFFFFF") == pytest.approx(1.0)
    assert luminance("#000000") == pytest.approx(0.0)


# --------------------------------------------------------------------------
# overrides
# --------------------------------------------------------------------------


def test_override_changes_one_entry_only() -> None:
    document = build_scheme({"groups": {"d_ind": {"Low": {"fill": "#123456"}}}})
    assert scheme_entry(document, "d_ind", "Low") == {"fill": "#123456", "text": "#FFFFFF"}
    assert scheme_entry(document, "d_ind", "High")["fill"] == "#00B050"


def test_override_can_uncolour_a_label() -> None:
    document = build_scheme({"groups": {"d_ind": {"Low": {"fill": None}}}})
    assert scheme_entry(document, "d_ind", "Low") is None


def test_series_and_chrome_overrides() -> None:
    document = build_scheme({
        "series": ["#111111", "#222222"],
        "chrome": {"table_header": "#ABCDEF"},
    })
    assert document["series"] == ["#111111", "#222222"]
    assert document["chrome"]["table_header"] == "#ABCDEF"


def test_invalid_override_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_scheme({"groups": {"d_ind": {"Low": {"fill": "red"}}}})
    with pytest.raises(ValueError):
        build_scheme({"series": ["nope"]})


def test_cleared_chrome_and_series_fall_back_to_the_defaults() -> None:
    document = build_scheme({
        "chrome": {"table_header": None},
        "series": ["#111111", None, "#333333"],
    })
    assert document["chrome"]["table_header"] == "#FFD966"
    assert document["series"] == ["#111111", "#333333"]


def test_lookup_is_case_insensitive_and_tolerates_unknowns() -> None:
    document = build_scheme()
    assert scheme_entry(document, "d_ind", "v.high")["fill"] == "#7030A0"
    assert scheme_entry(document, "d_ind", "Nope") is None
    assert scheme_entry(document, "nope", "Low") is None
    assert scheme_entry(None, "d_ind", "Low") is None


def test_default_palette_returns_a_copy() -> None:
    first = default_palette()
    first["groups"]["d_ind"]["Low"]["fill"] = "#000000"
    assert default_palette()["groups"]["d_ind"]["Low"]["fill"] == "#FF0000"


# --------------------------------------------------------------------------
# service persistence
# --------------------------------------------------------------------------


@pytest.fixture()
def service(db) -> PaletteService:
    return PaletteService(SettingsService(db))


def test_service_defaults_to_the_workbook(service: PaletteService) -> None:
    assert service.name() == "workbook"
    assert service.text_mode() == "auto"
    assert service.show_legend() is True
    assert service.overrides() == {}
    assert scheme_entry(service.scheme(), "d_ind", "Low")["fill"] == "#FF0000"


def test_service_stores_only_the_differences(service: PaletteService) -> None:
    groups = {
        group: {
            label: {"fill": entry["fill"] if entry else None,
                    "text": entry.get("text") if entry else None}
            for label, entry in labels.items()
        }
        for group, labels in service.defaults()["groups"].items()
    }
    groups["d_ind"]["Low"] = {"fill": "#123456", "text": None}
    service.save_edits(scheme_name="workbook", text_mode="auto",
                       show_legend=False, groups=groups, chrome={}, series=None)
    overrides = service.overrides()
    assert overrides["groups"] == {"d_ind": {"Low": {"fill": "#123456", "text": None}}}
    assert "chrome" not in overrides
    assert "series" not in overrides
    assert service.show_legend() is False
    assert scheme_entry(service.scheme(), "d_ind", "Low") == {
        "fill": "#123456", "text": "#FFFFFF"}


def test_service_set_and_reset_entry(service: PaletteService) -> None:
    service.set_entry("dv2", "Low Del", "#EEEEEE", "#111111")
    entry = scheme_entry(service.scheme(), "dv2", "Low Del")
    assert entry == {"fill": "#EEEEEE", "text": "#111111"}
    service.reset_entry("dv2", "Low Del")
    assert service.overrides() == {}
    assert scheme_entry(service.scheme(), "dv2", "Low Del")["fill"] == "#FDD7D5"


def test_service_classic_scheme_has_no_document(service: PaletteService) -> None:
    service.save_edits(scheme_name="classic", text_mode="auto", show_legend=True)
    assert service.scheme() is None


def test_service_rejects_unknown_options(service: PaletteService) -> None:
    with pytest.raises(ValueError):
        service.save_edits(scheme_name="neon", text_mode="auto", show_legend=True)
    with pytest.raises(ValueError):
        service.save_edits(scheme_name="workbook", text_mode="neon", show_legend=True)


def test_service_ignores_a_cleared_chrome_field(service: PaletteService) -> None:
    service.save_edits(scheme_name="workbook", text_mode="auto", show_legend=True,
                       groups={}, chrome={"table_header": None}, series=None)
    assert service.overrides() == {}
    assert service.scheme()["chrome"]["table_header"] == "#FFD966"


def test_service_export_import_round_trip(service: PaletteService, tmp_path) -> None:
    service.set_entry("d_ind", "Low", "#0A0B0C")
    target = service.export_json(tmp_path / "scheme.json")
    payload = json.loads(target.read_text())
    assert payload["scheme"] == "workbook"
    assert payload["overrides"]["groups"]["d_ind"]["Low"]["fill"] == "#0A0B0C"

    service.reset()
    assert service.overrides() == {}
    service.import_json(target)
    assert scheme_entry(service.scheme(), "d_ind", "Low")["fill"] == "#0A0B0C"


def test_import_rejects_invalid_colours(service: PaletteService, tmp_path) -> None:
    target = tmp_path / "bad.json"
    target.write_text(json.dumps({
        "scheme": "workbook",
        "overrides": {"groups": {"d_ind": {"Low": {"fill": "nope"}}}},
    }))
    with pytest.raises(ValueError):
        service.import_json(target)


# --------------------------------------------------------------------------
# settings integration
# --------------------------------------------------------------------------


def test_settings_defaults_include_colours(db) -> None:
    settings = SettingsService(db)
    assert settings.colour_scheme() == "workbook"
    assert settings.colour_text_mode() == "auto"
    assert settings.show_colour_legend() is True


def test_settings_validate_the_scheme_name(db) -> None:
    settings = SettingsService(db)
    with pytest.raises(ValueError):
        settings.set("colors.scheme", "neon")
    settings.set("colors.scheme", "classic")
    assert settings.colour_scheme() == "classic"


def test_context_applies_the_scheme(tmp_path) -> None:
    from ui.context import AppContext
    from services import palette as palette_module

    context = AppContext.create(db_path=tmp_path / "ctx.db")
    try:
        assert palette_module.active() is not None
        assert palette_module.entry("d_ind", "V.High")["fill"] == "#7030A0"
    finally:
        palette_module.set_scheme(None)


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def active_scheme():
    from services import palette as palette_module

    palette_module.set_scheme(build_scheme())
    yield
    palette_module.set_scheme(None)


def test_badge_uses_the_workbook_fill(qapp, active_scheme) -> None:
    from PySide6.QtCore import Qt
    from ui.widgets import Badge

    badge = Badge("V.High", "dark", group="d_ind")
    style = badge.styleSheet().lower()
    assert "background: #7030a0" in style
    assert "color: #ffffff" in style
    badge.set_class("Low", "d_ind")
    assert "background: #ff0000" in badge.styleSheet().lower()


def test_badge_falls_back_for_unknown_labels(qapp, active_scheme) -> None:
    from ui.widgets import Badge

    badge = Badge("something-else", "dark")
    assert "border" in badge.styleSheet()


def test_dataframe_model_paints_indicator_columns(qapp, active_scheme) -> None:
    import pandas as pd
    from PySide6.QtCore import Qt
    from ui.widgets import DataFrameModel

    model = DataFrameModel(
        pd.DataFrame({"symbol": ["OIL"], "d_ind": ["Low"], "dv2": ["Low Del"]}),
        theme="dark")
    assert model.data(model.index(0, 0), Qt.BackgroundRole) is None
    background = model.data(model.index(0, 1), Qt.BackgroundRole)
    assert background is not None and background.name().upper() == "#FF0000"
    foreground = model.data(model.index(0, 2), Qt.ForegroundRole)
    assert foreground is not None and foreground.name().upper() == "#C00000"


def test_legend_matches_the_workbook_labels(qapp, active_scheme) -> None:
    from ui.widgets import ScreenerLegend

    legend = ScreenerLegend("dark")
    texts = [badge.text() for badge in legend._badges]
    assert "V.High" in texts and "GOLD" in texts and "80% Plus" in texts
    assert "Low Del" in texts and "Above" in texts


def test_dataframe_model_handles_report_preview_labels(qapp, active_scheme) -> None:
    """Reports append `` *`` to calculated columns; the colour code must survive."""
    import pandas as pd
    from PySide6.QtCore import Qt
    from ui.widgets import DataFrameModel

    model = DataFrameModel(
        pd.DataFrame({"Symbol": ["OIL"], "D Ind *": ["Low"], "Dv1 *": ["GOLD"],
                      "Dv2 *": ["Low Del"]}),
        theme="dark")
    assert model.data(model.index(0, 1), Qt.BackgroundRole).name().upper() == "#FF0000"
    assert model.data(model.index(0, 2), Qt.BackgroundRole).name().upper() == "#FFC000"
    assert model.data(model.index(0, 3), Qt.ForegroundRole).name().upper() == "#C00000"


def test_kpi_signal_status_paints_a_cell(qapp, active_scheme) -> None:
    from ui.widgets import KpiCard

    card = KpiCard("Delivery", "dark")
    card.set_value("60.55", "Delivery", "d_ind:High")
    style = card.value_label.styleSheet().lower()
    assert "background: #00b050" in style
    assert "color: #ffffff" in style
    assert card.value_label.minimumHeight() >= 30
