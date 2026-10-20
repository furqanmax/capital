"""The formula sandbox: what it computes, and what it refuses to compute.

These tests are the security boundary.  If any of them has to be relaxed, the
sandbox has been weakened and that must be a deliberate decision, not an accident.
"""

from __future__ import annotations

import pytest

from services.safe_parser import (
    ALLOWED_FUNCTIONS,
    CONSTANTS,
    FORBIDDEN_TOKENS,
    FormulaCompiler,
    FormulaError,
    FormulaSecurityError,
    evaluate_column,
    rewrite_label_aliases,
)

FIELDS = ("SYMBOL", "TRADE_DATE", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
          "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES", "DELIVERY_PCT")


@pytest.fixture()
def compiler() -> FormulaCompiler:
    return FormulaCompiler()


def values(expression: str, rows, null_policy: str = "blank", fields=FIELDS):
    series = {name: [row.get(name.lower()) for row in rows] for name in fields}
    result, error = evaluate_column(expression, series, fields, null_policy)
    assert error is None, error
    return result


ROWS = [
    {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "last": 106.0,
     "prevclose": 100.0, "tottrdqty": 1000.0, "tottrdval": 105000.0,
     "totaltrades": 50.0, "delivery_pct": 40.0},
    {"open": 106.0, "high": 112.0, "low": 104.0, "close": 110.0, "last": 111.0,
     "prevclose": 106.0, "tottrdqty": 1200.0, "tottrdval": 132000.0,
     "totaltrades": 60.0, "delivery_pct": 55.0},
]


# --------------------------------------------------------------------------
# arithmetic and functions
# --------------------------------------------------------------------------


def test_arithmetic_and_precedence(compiler) -> None:
    assert values("2 + 3 * 4", ROWS) == [14.0, 14.0]
    assert values("(2 + 3) * 4", ROWS) == [20.0, 20.0]
    assert values("10 / 4", ROWS) == [2.5, 2.5]
    assert values("2 ^ 3", ROWS) == [8.0, 8.0]
    assert values("-5 + 2", ROWS) == [-3.0, -3.0]


def test_comparison_and_boolean_operators(compiler) -> None:
    assert values("1 < 2", ROWS) == [True, True]
    assert values("1 = 1 AND 2 = 2", ROWS) == [True, True]
    assert values("1 = 2 OR 2 = 2", ROWS) == [True, True]
    assert values("NOT 1 = 2", ROWS) == [True, True]      # NOT (1 = 2)
    assert values("NOT 1 = 1", ROWS) == [False, False]
    assert values("NOT 1 = 2 AND 1 = 1", ROWS) == [True, True]


def test_aggregate_functions(compiler) -> None:
    assert values("AVERAGE(OPEN, HIGH, LOW, CLOSE)", ROWS) == [101.25, 108.0]
    assert values("SUM(OPEN, CLOSE)", ROWS) == [205.0, 216.0]
    assert values("MIN(OPEN, CLOSE)", ROWS) == [100.0, 106.0]
    assert values("MAX(OPEN, CLOSE)", ROWS) == [105.0, 110.0]
    assert values("ROUND(AVERAGE(OPEN, CLOSE), 1)", ROWS) == [102.5, 108.0]
    assert values("ABS(0 - 7)", ROWS) == [7.0, 7.0]
    assert values("SQRT(16)", ROWS) == [4.0, 4.0]
    assert values("POWER(2, 5)", ROWS) == [32.0, 32.0]


def test_if_and_coalesce(compiler) -> None:
    assert values('IF(CLOSE > OPEN, "up", "down")', ROWS) == ["up", "up"]
    assert values('COALESCE(CLOSE, 0)', ROWS) == [105.0, 110.0]
    assert values('IF(CLOSE > 1000, "big", "small")', ROWS) == ["small", "small"]


def test_average_of_a_field_uses_a_trailing_window(compiler) -> None:
    """``AVERAGE(field, n)`` looks backwards; short windows are not padded."""
    first = values("AVERAGE(CLOSE, 3)", ROWS)
    assert first == [105.0, 107.5]  # 1 row available, then 2


def test_rolling_functions_are_backward_looking(compiler) -> None:
    assert values("ROLLING_AVG(CLOSE, 2)", ROWS) == [105.0, 107.5]
    assert values("ROLLING_SUM(CLOSE, 2)", ROWS) == [105.0, 215.0]
    assert values("ROLLING_MIN(CLOSE, 2)", ROWS) == [105.0, 105.0]
    assert values("ROLLING_MAX(CLOSE, 2)", ROWS) == [105.0, 110.0]
    std = values("ROLLING_STD(CLOSE, 2)", ROWS)
    assert std[0] is None and std[1] == pytest.approx(3.5355339)
    assert values("DELTA(CLOSE, 2)", ROWS)[0] is None
    pct = values("PCT_CHANGE(CLOSE, 2)", ROWS)
    assert pct[0] is None and pct[1] == pytest.approx(5 / 105)
    assert values("RANK_PCT(CLOSE, HIGH, LOW)", ROWS) == [0.5, 0.5]


def test_safe_div_never_raises(compiler) -> None:
    rows = [{"tottrdqty": 10.0, "totaltrades": 0.0},
            {"tottrdqty": 10.0, "totaltrades": 2.0}]
    assert values("SAFE_DIV(TOTTRDQTY, TOTALTRADES)", rows) == [None, 5.0]
    assert values("SAFE_DIV(CLOSE, 0)", ROWS) == [None, None]
    assert values("10 / 0", ROWS) == [None, None]


def test_delivery_percentage_label_aliases(compiler) -> None:
    """The worksheet writes ``Delivery %``; the parser accepts it as is."""
    assert values("DELIVERY % * TOTTRDQTY / 100", ROWS) == [400.0, 660.0]
    assert values("Delivery % * TOTTRDQTY / 100", ROWS) == [400.0, 660.0]
    assert values("delivery pct * TOTTRDQTY / 100", ROWS) == [400.0, 660.0]
    assert rewrite_label_aliases("Delivery %") == "DELIVERY_PCT"
    assert rewrite_label_aliases("Avg Price") == "AVG_PRICE"
    assert rewrite_label_aliases("D Ind.") == "TRADE_INDICATOR"
    # a label inside a string literal is text and must never be rewritten
    assert rewrite_label_aliases('"Delivery %"') == '"Delivery %"'
    assert rewrite_label_aliases('IF(CLOSE > 10, "Delivery %", "Avg Price")') == \
        'IF(CLOSE > 10, "Delivery %", "Avg Price")'


def test_remainder_operator_is_excel_mod(compiler) -> None:
    assert values("TOTTRDQTY % 100", ROWS) == [0.0, 0.0]
    assert values("7 % 3", ROWS) == [1.0, 1.0]


def test_field_lookup_is_case_insensitive(compiler) -> None:
    assert values("close - open", ROWS) == [5.0, 4.0]
    assert values("Close - Open", ROWS) == [5.0, 4.0]
    assert values("Total Traded Qty - TOTTRDQTY", ROWS) == [0.0, 0.0]
    assert values("Prev Close + OPEN", ROWS) == [200.0, 212.0]
    assert values("DATE", [{"trade_date": "2025-01-01"}], fields=("TRADE_DATE",)) == ["2025-01-01"]


def test_constants(compiler) -> None:
    assert values("PI", ROWS)[0] == pytest.approx(3.14159265)
    assert values("TRUE", ROWS) == [True, True]
    assert values("FALSE", ROWS) == [False, False]
    assert "PI" in CONSTANTS


# --------------------------------------------------------------------------
# null policies
# --------------------------------------------------------------------------


@pytest.mark.parametrize("policy,expected", [("blank", None), ("zero", 0.0)])
def test_null_policy_applies_to_missing_inputs(policy, expected) -> None:
    rows = [{"delivery_pct": None, "tottrdqty": 100.0}]
    result = values("DELIVERY_PCT * TOTTRDQTY / 100", rows, null_policy=policy)
    assert result == [expected]


def test_evaluate_column_reports_a_compile_failure_with_no_values() -> None:
    result, error = evaluate_column("NOPE + 1", {"CLOSE": [1.0]}, ("CLOSE",))
    assert result == []                    # nothing could be evaluated
    assert error and "NOPE" in error


def test_evaluate_column_treats_nan_as_missing() -> None:
    """A mixed column turns a blank into ``NaN``; it must not leak into results."""
    series = {"CLOSE": [float("nan"), 5.0]}
    blank, error = evaluate_column("CLOSE + 1", series, ("CLOSE",), "blank")
    assert blank == [None, 6.0]
    assert error is None

    zero, _ = evaluate_column("CLOSE + 1", series, ("CLOSE",), "zero")
    assert zero == [1.0, 6.0]

    reported, message = evaluate_column("CLOSE + 1", series, ("CLOSE",), "error")
    assert reported == [None, 6.0]
    assert message and "1 of 2" in message


def test_null_policy_error_is_reported_not_raised() -> None:
    series = {"CLOSE": [None, 5.0]}
    result, error = evaluate_column("CLOSE + 1", series, ("CLOSE",), "error")
    assert result == [None, 6.0]
    assert error is not None and "1 of 2" in error

    blank_result, blank_error = evaluate_column("CLOSE + 1", series, ("CLOSE",), "blank")
    assert blank_result == [None, 6.0]
    assert blank_error is None


# --------------------------------------------------------------------------
# security
# --------------------------------------------------------------------------


@pytest.mark.parametrize("expression", [
    '__import__("os")',
    'open("/etc/passwd")',
    'eval("1+1")',
    'exec("x=1")',
    'compile("1", "<s>", "eval")',
    "import os",
    "globals()",
    "getattr(CLOSE, 'imag')",
    "CLOSE.real",
    "CLOSE[0]",
    "lambda: 1",
    "CLOSE if 1 else 2",
    "{1: 2}",
    "[1, 2]",
])
def test_dangerous_expressions_are_rejected(compiler, expression) -> None:
    ok, message = compiler.validate(expression, FIELDS)
    assert not ok, f"{expression!r} was accepted: {message}"


def test_forbidden_token_list_covers_the_obvious_escapes() -> None:
    for token in ("__", "import", "eval", "exec", "open", "globals", "getattr",
                  "subprocess", "os.system"):
        assert any(bad in token for bad in FORBIDDEN_TOKENS), token


def test_unknown_function_is_rejected_with_a_helpful_message(compiler) -> None:
    ok, message = compiler.validate("TOTALLY_MADE_UP(1)", FIELDS)
    assert not ok
    assert "not allowed" in message
    assert "AVERAGE" in message


def test_unknown_field_is_rejected_and_lists_the_fields(compiler) -> None:
    ok, message = compiler.validate("NOPE + 1", FIELDS)
    assert not ok
    assert "NOPE" in message
    assert "CLOSE" in message


def test_field_outside_the_allowed_set_is_rejected(compiler) -> None:
    ok, message = compiler.validate("SECRET + 1", FIELDS)
    assert not ok and "SECRET" in message


def test_window_function_requires_a_field_and_a_number(compiler) -> None:
    ok, message = compiler.validate("ROLLING_AVG(1, 5)", FIELDS)
    assert not ok
    ok, message = compiler.validate("ROLLING_AVG(CLOSE, CLOSE)", FIELDS)
    assert not ok
    ok, message = compiler.validate("ROLLING_AVG(CLOSE, 5, 6)", FIELDS)
    assert not ok


def test_empty_and_invalid_expressions(compiler) -> None:
    for expression in ("", "   ", "=", "1 +", "(1", "1)", "1 2 3 +"):
        ok, message = compiler.validate(expression, FIELDS)
        assert not ok, expression


def test_allowlist_is_small_and_closed() -> None:
    assert "EVAL" not in ALLOWED_FUNCTIONS
    assert "OPEN" not in ALLOWED_FUNCTIONS
    assert "IMPORT" not in ALLOWED_FUNCTIONS
    assert set(ALLOWED_FUNCTIONS) == set(ALLOWED_FUNCTIONS)


def test_compiled_formula_reports_its_fields(compiler) -> None:
    compiled = compiler.compile("(CLOSE - OPEN) / OPEN * 100", FIELDS)
    assert set(compiled.fields) == {"CLOSE", "OPEN"}


def test_compile_is_pure(compiler) -> None:
    first = compiler.compile("CLOSE + 1", FIELDS)
    second = compiler.compile("CLOSE + 1", FIELDS)
    assert first.fields == second.fields == ["CLOSE"]
    assert first.text == second.text == "CLOSE + 1"


def test_error_positions_point_at_the_problem(compiler) -> None:
    with pytest.raises(FormulaError) as info:
        compiler.compile("CLOSE + @", FIELDS)
    assert "@" in str(info.value)


def test_security_error_type_is_used_for_escapes(compiler) -> None:
    with pytest.raises(FormulaSecurityError):
        compiler.compile('__import__("os")', FIELDS)
