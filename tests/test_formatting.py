"""Display formatting must agree with Excel, which rounds half away from zero."""

from __future__ import annotations

import datetime as _dt
import math

import pandas as pd
import pytest

from services.formatting import (
    NOT_AVAILABLE,
    format_date,
    format_int,
    format_number,
    format_percent,
    is_missing,
    round_display,
)


# --------------------------------------------------------------------------
# the rounding rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value,places,expected", [
    (1234.5, 0, "1,235"),          # Python's own format gives "1,234"
    (-1234.5, 0, "-1,235"),        # ...and "-1,234": ours rounds away from zero
    (0.5, 0, "1"),
    (-0.5, 0, "-1"),
    (0.125, 2, "0.13"),
    (-0.125, 2, "-0.13"),
    (2.675, 2, "2.68"),
    (1.005, 2, "1.01"),
    (1234567.891, 2, "1,234,567.89"),
    (999.999, 2, "1,000.00"),
    (0, 2, "0.00"),
    (1234.5, 4, "1,234.5000"),
])
def test_round_display_matches_excel(value, places, expected) -> None:
    assert round_display(value, places) == expected


def test_round_display_does_not_use_bankers_rounding() -> None:
    # documents exactly the behaviour being guarded against
    assert f"{1234.5:,.0f}" == "1,234"
    assert round_display(1234.5, 0) == "1,235"


def test_round_display_is_stable_for_extreme_values() -> None:
    assert round_display(1e300, 2)                      # no exception, some string
    assert round_display(float("inf"), 2) == "inf"
    assert round_display(float("nan"), 2) == "nan"
    assert round_display("not a number", 2) == "not a number"


# --------------------------------------------------------------------------
# "missing" has one meaning
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", float("nan"), math.nan,
                                   pd.NA if hasattr(pd, "NA") else None,
                                   pd.NaT])
def test_missing_values(value) -> None:
    assert is_missing(value) is True


@pytest.mark.parametrize("value", [0, 0.0, False, "x", "0", _dt.date(2025, 1, 1)])
def test_present_values(value) -> None:
    assert is_missing(value) is False


# --------------------------------------------------------------------------
# the display helpers
# --------------------------------------------------------------------------


def test_format_number_uses_the_shared_rounding() -> None:
    assert format_number(1234.5, 0) == "1,235"
    assert format_number(0.125) == "0.13"
    assert format_number(None) == NOT_AVAILABLE
    assert format_number("", blank="—") == "—"
    assert format_number("text") == "text"


def test_format_number_shows_booleans_as_words() -> None:
    assert format_number(True) == "Yes"
    assert format_number(False) == "No"


def test_format_int() -> None:
    assert format_int(1234.5) == "1,235"
    assert format_int(None) == NOT_AVAILABLE


def test_format_percent_multiplies_a_ratio() -> None:
    assert format_percent(0.125) == "12.50%"
    assert format_percent(0.12345) == "12.35%"
    assert format_percent(-0.5) == "-50.00%"
    assert format_percent(None) == NOT_AVAILABLE


def test_format_date() -> None:
    assert format_date(_dt.date(2025, 1, 31)) == "2025-01-31"
    assert format_date(_dt.datetime(2025, 1, 31, 14, 5)) == "2025-01-31"
    assert format_date(None) == NOT_AVAILABLE
    assert format_date(_dt.date(2025, 1, 31), "%d/%m/%Y") == "31/01/2025"
