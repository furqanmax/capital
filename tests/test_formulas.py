"""The Formula Lab service: CRUD, versioning, sandboxed preview and application."""

from __future__ import annotations

import pandas as pd
import pytest

from database.db import Database
from models import AuditLog, FormulaVersion
from services.formula_service import (
    SYNTAX_REFERENCE,
    VALID_DATA_TYPES,
    VALID_NULL_POLICIES,
    FormulaService,
)
from services.safe_parser import ALLOWED_FUNCTIONS, FormulaError

BUILTIN_NAMES = {
    "delivery_band", "avg_price", "pct_change", "close_ma_25", "close_ma_5",
    "deliverable_qty", "qty_per_trade",
}


@pytest.fixture()
def service(db: Database) -> FormulaService:
    return FormulaService(db)


@pytest.fixture()
def frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": "TEST", "trade_date": "2025-01-01", "open": 100.0, "high": 110.0,
         "low": 90.0, "close": 105.0, "last": 106.0, "prevclose": 100.0,
         "tottrdqty": 1000.0, "tottrdval": 105000.0, "totaltrades": 50.0,
         "delivery_pct": 40.0},
        {"symbol": "TEST", "trade_date": "2025-01-02", "open": 106.0, "high": 112.0,
         "low": 104.0, "close": 110.0, "last": 111.0, "prevclose": 106.0,
         "tottrdqty": 1200.0, "tottrdval": 132000.0, "totaltrades": 60.0,
         "delivery_pct": 55.0},
    ])


# --------------------------------------------------------------------------
# the seeded catalogue
# --------------------------------------------------------------------------


def test_builtins_are_seeded(service: FormulaService) -> None:
    names = {formula.name for formula in service.list_formulas()}
    assert BUILTIN_NAMES <= names
    assert all(formula.builtin for formula in service.list_formulas()
               if formula.name in BUILTIN_NAMES)


def test_every_builtin_validates_and_previews(service: FormulaService,
                                              frame: pd.DataFrame) -> None:
    for formula in service.list_formulas():
        ok, message = service.validate(formula.expression)
        assert ok, f"{formula.name}: {message}"
        preview = service.preview(formula.expression, frame, formula.null_policy)
        assert preview.ok, f"{formula.name}: {preview.error}"
        assert len(preview.values) == len(frame)
        assert preview.fields


def test_enabled_only_filter(service: FormulaService) -> None:
    target = service.get_by_name("delivery_band")
    service.set_enabled(target.id, False)
    enabled = {formula.name for formula in service.list_formulas(enabled_only=True)}
    assert "delivery_band" not in enabled
    assert "delivery_band" in {formula.name for formula in service.list_formulas()}
    service.set_enabled(target.id, True)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_validate_never_raises(service: FormulaService) -> None:
    assert service.validate("CLOSE + 1")[0] is True
    ok, message = service.validate("CLOSE + ")
    assert ok is False and message
    ok, message = service.validate("__import__('os')")
    assert ok is False and message


def test_required_fields_lists_the_inputs(service: FormulaService) -> None:
    assert service.required_fields("(CLOSE - OPEN) / OPEN") == ["CLOSE", "OPEN"]


def test_required_fields_raises_on_a_bad_expression(service: FormulaService) -> None:
    with pytest.raises(FormulaError):
        service.required_fields("NOPE + 1")


def test_syntax_reference_is_accurate() -> None:
    assert "AVERAGE" in SYNTAX_REFERENCE
    assert "SAFE_DIV" in SYNTAX_REFERENCE
    for name in ("ROLLING_AVG", "DELTA", "PCT_CHANGE", "RANK_PCT", "COALESCE"):
        assert name in SYNTAX_REFERENCE
        assert name in ALLOWED_FUNCTIONS
    # the documented operators
    for token in ("%", "^", "<>", "AND", "OR", "NOT"):
        assert token in SYNTAX_REFERENCE


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


def test_preview_evaluates_every_row(service: FormulaService,
                                     frame: pd.DataFrame) -> None:
    preview = service.preview("(CLOSE - OPEN) / OPEN", frame)
    assert preview.ok
    assert preview.values[0] == pytest.approx(0.05)
    assert preview.values[1] == pytest.approx(4 / 106)
    assert preview.fields == ["CLOSE", "OPEN"]


def test_preview_accepts_workbook_labels(service: FormulaService,
                                         frame: pd.DataFrame) -> None:
    preview = service.preview("DELIVERY % * TOTTRDQTY / 100", frame)
    assert preview.ok
    assert preview.values == [pytest.approx(400.0), pytest.approx(660.0)]


def test_preview_reports_a_bad_expression(service: FormulaService,
                                         frame: pd.DataFrame) -> None:
    preview = service.preview("CLOSE +", frame)
    assert not preview.ok
    assert preview.values == []
    assert preview.error


def test_preview_of_an_empty_frame_is_empty(service: FormulaService) -> None:
    preview = service.preview("CLOSE + 1", pd.DataFrame())
    assert preview.ok
    assert preview.values == []


def test_preview_limit(service: FormulaService, frame: pd.DataFrame) -> None:
    preview = service.preview("CLOSE", frame, limit=1)
    assert len(preview.values) == 1


def test_preview_result_to_frame(service: FormulaService,
                                 frame: pd.DataFrame) -> None:
    preview = service.preview("CLOSE", frame)
    assert list(preview.to_frame().columns) == ["value"]
    assert preview.to_frame()["value"].tolist() == [105.0, 110.0]


def test_preview_honours_the_null_policy(service: FormulaService) -> None:
    frame = pd.DataFrame([{"close": None, "open": 100.0}])
    blank = service.preview("CLOSE + 1", frame, "blank")
    zero = service.preview("CLOSE + 1", frame, "zero")
    assert blank.values == [None]
    assert zero.values == [1.0]


# --------------------------------------------------------------------------
# create / update / versioning
# --------------------------------------------------------------------------


def test_create_stores_the_definition_and_a_first_version(service: FormulaService) -> None:
    formula = service.create(
        name="body_ratio", expression="(CLOSE - OPEN) / (HIGH - LOW)",
        output_column="body_ratio", description="Candle body relative to range",
        category="custom", data_type="float", null_policy="blank",
        display_format="{:,.3f}", parameters={"precision": 3},
    )
    assert formula.id is not None
    assert formula.current_version == 1
    assert formula.enabled is True
    assert formula.expression == "(CLOSE - OPEN) / (HIGH - LOW)"

    versions = service.versions(formula.id)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].change_note == "Initial definition"
    assert versions[0].snapshot["output_column"] == "body_ratio"


def test_create_rejects_duplicate_names(service: FormulaService) -> None:
    service.create(name="dup", expression="CLOSE", output_column="dup")
    with pytest.raises(ValueError):
        service.create(name="dup", expression="OPEN", output_column="dup2")


@pytest.mark.parametrize("kwargs", [
    {"name": "", "expression": "CLOSE", "output_column": "x"},
    {"name": "ok", "expression": "CLOSE", "output_column": ""},
    {"name": "ok", "expression": "CLOSE", "output_column": "x", "data_type": "complex"},
    {"name": "ok", "expression": "CLOSE", "output_column": "x", "null_policy": "explode"},
])
def test_create_rejects_bad_input(service: FormulaService, kwargs) -> None:
    with pytest.raises(ValueError):
        service.create(**kwargs)


def test_create_rejects_an_unsafe_expression(service: FormulaService) -> None:
    with pytest.raises(FormulaError):
        service.create(name="evil", expression="__import__('os')", output_column="evil")


def test_update_expression_creates_a_new_version(service: FormulaService) -> None:
    formula = service.create(name="evolving", expression="CLOSE", output_column="evolving")
    updated = service.update(formula.id, expression="CLOSE * 2", change_note="doubled")
    assert updated.current_version == 2
    versions = service.versions(formula.id)
    assert [v.version for v in versions] == [2, 1]
    assert versions[0].change_note == "doubled"
    assert versions[0].expression == "CLOSE * 2"
    assert versions[1].expression == "CLOSE"


def test_update_without_changing_the_expression_keeps_the_version(service: FormulaService) -> None:
    formula = service.create(name="stable", expression="CLOSE", output_column="stable")
    updated = service.update(formula.id, expression="CLOSE", description="still the same")
    assert updated.current_version == 1
    assert updated.description == "still the same"
    assert len(service.versions(formula.id)) == 1


def test_update_metadata_only(service: FormulaService) -> None:
    formula = service.create(name="meta", expression="CLOSE", output_column="meta")
    updated = service.update(formula.id, category="volume", display_format="{:,.0f}",
                             parameters={"unit": "shares"}, enabled=False)
    assert updated.category == "volume"
    assert updated.parameters == {"unit": "shares"}
    assert updated.enabled is False


def test_update_rejects_an_unsafe_expression_without_touching_the_formula(
        service: FormulaService) -> None:
    formula = service.create(name="safe", expression="CLOSE", output_column="safe")
    with pytest.raises(FormulaError):
        service.update(formula.id, expression="eval('1')")
    assert service.get(formula.id).expression == "CLOSE"
    assert service.get(formula.id).current_version == 1


def test_update_rejects_unknown_data_types(service: FormulaService) -> None:
    formula = service.create(name="typed", expression="CLOSE", output_column="typed")
    with pytest.raises(ValueError):
        service.update(formula.id, data_type="nonsense")


def test_update_of_a_missing_formula_raises(service: FormulaService) -> None:
    with pytest.raises(ValueError):
        service.update(999_999, description="ghost")


def test_restore_version_rewrites_the_expression(service: FormulaService) -> None:
    formula = service.create(name="rewind", expression="CLOSE", output_column="rewind")
    service.update(formula.id, expression="CLOSE * 3", change_note="tripled")
    restored = service.restore_version(formula.id, 1)
    assert restored.expression == "CLOSE"
    assert restored.current_version == 3
    assert service.versions(formula.id)[0].change_note == "restored from version 1"


def test_restore_of_an_unknown_version_raises(service: FormulaService) -> None:
    formula = service.create(name="nope", expression="CLOSE", output_column="nope")
    with pytest.raises(ValueError):
        service.restore_version(formula.id, 99)


def test_delete_removes_the_formula_and_its_versions(service: FormulaService) -> None:
    formula = service.create(name="temporary", expression="CLOSE",
                             output_column="temporary")
    formula_id = formula.id
    service.delete(formula_id)
    assert service.get(formula_id) is None
    assert service.versions(formula_id) == []
    with service.db.session() as session:
        assert session.query(FormulaVersion).filter(
            FormulaVersion.formula_id == formula_id).count() == 0


def test_mutations_are_audited(service: FormulaService, db: Database) -> None:
    formula = service.create(name="logged", expression="CLOSE", output_column="logged")
    service.update(formula.id, expression="OPEN", change_note="swapped")
    service.delete(formula.id)
    with db.session() as session:
        messages = [row.message for row in session.query(AuditLog).filter(
            AuditLog.category == "formula").all()]
    assert any("Created formula 'logged'" in message for message in messages)
    assert any("Updated formula 'logged'" in message for message in messages)
    assert any("Deleted formula 'logged'" in message for message in messages)


def test_lookup_helpers(service: FormulaService) -> None:
    formula = service.create(name="lookup", expression="CLOSE", output_column="lookup")
    assert service.get(formula.id).name == "lookup"
    assert service.get_by_name("lookup").id == formula.id
    assert service.get(999_999) is None
    assert service.get_by_name("absent") is None


# --------------------------------------------------------------------------
# applying formulas to a frame
# --------------------------------------------------------------------------


def test_apply_adds_a_column_per_enabled_formula(service: FormulaService,
                                                 frame: pd.DataFrame) -> None:
    out, results = service.apply(frame)
    assert len(results) == len(service.list_formulas(enabled_only=True))
    for result in results:
        assert result["ok"], result
        assert result["output_column"] in out.columns


def test_apply_honours_the_enabled_flag(service: FormulaService,
                                        frame: pd.DataFrame) -> None:
    target = service.get_by_name("close_ma_5")
    service.set_enabled(target.id, False)
    out, _ = service.apply(frame)
    assert "close_ma_5" not in out.columns
    service.set_enabled(target.id, True)


def test_apply_can_be_restricted_to_specific_formulas(service: FormulaService,
                                                      frame: pd.DataFrame) -> None:
    formula = service.create(name="just_close", expression="CLOSE",
                             output_column="just_close")
    out, results = service.apply(frame, formulas=[formula])
    assert [result["output_column"] for result in results] == ["just_close"]
    assert "just_close" in out.columns


def test_apply_coerces_the_declared_data_type(service: FormulaService,
                                              frame: pd.DataFrame) -> None:
    as_int = service.create(name="rounded", expression="CLOSE + 0.6",
                            output_column="rounded", data_type="int")
    as_text = service.create(name="label", expression='IF(CLOSE > OPEN, "up", "down")',
                             output_column="label", data_type="str")
    as_flag = service.create(name="flag", expression="CLOSE > OPEN",
                             output_column="flag", data_type="bool")

    out, _ = service.apply(frame, formulas=[as_int, as_text, as_flag])
    assert out["rounded"].tolist() == [106, 111]
    assert out["label"].tolist() == ["up", "up"]
    assert out["flag"].tolist() == [True, True]


def test_apply_does_not_mutate_the_input_frame(service: FormulaService,
                                               frame: pd.DataFrame) -> None:
    before = frame.copy(deep=True)
    service.apply(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_apply_on_an_empty_frame_is_a_no_op(service: FormulaService) -> None:
    out, results = service.apply(pd.DataFrame())
    assert out.empty
    assert results == []


def test_apply_returns_a_blank_rather_than_failing(service: FormulaService,
                                                   frame: pd.DataFrame) -> None:
    """``SAFE_DIV(CLOSE, 0)`` is unavailable data, not a broken formula."""
    guarded = service.create(name="guarded", expression="SAFE_DIV(CLOSE, 0)",
                             output_column="guarded")
    out, results = service.apply(frame, formulas=[guarded])
    assert results[0]["ok"] is True
    assert out["guarded"].isna().all()


def test_apply_reports_error_policy_failures_and_can_be_strict(
        service: FormulaService) -> None:
    incomplete = service.create(name="incomplete", expression="CLOSE + 1",
                                output_column="incomplete", null_policy="error")
    frame = pd.DataFrame([{"close": None}, {"close": 5.0}])

    out, results = service.apply(frame, formulas=[incomplete])
    assert results[0]["ok"] is False
    assert results[0]["error"]
    assert pd.isna(out["incomplete"].iloc[0])
    assert out["incomplete"].iloc[1] == 6.0

    # ``strict`` turns the same situation into an exception
    with pytest.raises(FormulaError):
        service.apply(frame, formulas=[incomplete], strict=True)


def test_apply_uses_the_formulas_null_policy(service: FormulaService) -> None:
    frame = pd.DataFrame([{"close": None, "open": 1.0}])
    blank = service.create(name="null_blank", expression="CLOSE", output_column="nb",
                           null_policy="blank")
    zero = service.create(name="null_zero", expression="CLOSE", output_column="nz",
                          null_policy="zero")
    out, _ = service.apply(frame, formulas=[blank, zero])
    assert pd.isna(out["nb"].iloc[0])
    assert out["nz"].iloc[0] == 0.0


def test_valid_data_types_and_null_policies_are_documented() -> None:
    assert VALID_DATA_TYPES == ("float", "int", "str", "bool")
    assert VALID_NULL_POLICIES == ("blank", "zero", "error")
