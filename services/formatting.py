"""Display helpers shared by the services and the widgets.

Excel formats a number by rounding **half away from zero**; Python's own
``format`` rounds half to even, so ``f"{1234.5:,.0f}"`` produces ``"1,234"``
where Excel shows ``"1,235"``, and ``0.125`` with two decimals becomes ``0.12``
instead of ``0.13``.  Every value the user sees is formatted through here, so the
application never disagrees with the workbook on a ``.5`` boundary.
"""

from __future__ import annotations

import datetime as _dt
import math
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Optional

import pandas as pd

#: displayed when a value is unavailable, matching the rest of the application
NOT_AVAILABLE = "n/a"


def is_missing(value: Any) -> bool:
    """True for ``None``, a blank string and ``NaN``/``NaT``/``NA``.

    Everything unavailable is represented as a blank in the interface, so all
    the "no value" spellings are normalised in one place.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, bool):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def round_display(number: Any, places: int = 2) -> str:
    """Format ``number`` with thousands separators, rounding half away from zero.

    Falls back to Python's formatting only for values ``decimal`` cannot
    quantise (non-finite numbers and absurdly large magnitudes).
    """
    try:
        value = float(number)
    except (TypeError, ValueError):
        return str(number)
    if not math.isfinite(value):
        return str(number)

    places = int(places)
    try:
        with localcontext() as context:
            context.prec = 60
            rounded = Decimal(str(value)).quantize(
                Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f"{rounded:,.{places}f}"
    except (ArithmeticError, ValueError):          # pragma: no cover - extreme values
        return f"{value:,.{places}f}"


def format_number(value: Any, places: int = 2, blank: str = NOT_AVAILABLE) -> str:
    """``round_display`` with a blank for anything unavailable."""
    if is_missing(value):
        return blank
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return round_display(value, places)


def format_int(value: Any, blank: str = NOT_AVAILABLE) -> str:
    return format_number(value, 0, blank)


def format_percent(value: Any, places: int = 2,
                   blank: str = NOT_AVAILABLE) -> str:
    """Format a ratio (``0.125``) as ``12.50%`` — never a pre-multiplied number."""
    if is_missing(value):
        return blank
    try:
        number = float(value) * 100.0
    except (TypeError, ValueError):
        return str(value)
    return f"{round_display(number, places)}%"


def format_date(value: Optional[Any], fmt: str = "%Y-%m-%d",
                blank: str = NOT_AVAILABLE) -> str:
    if is_missing(value):
        return blank
    if isinstance(value, _dt.datetime):
        return value.strftime(fmt)
    if isinstance(value, _dt.date):
        return value.strftime(fmt)
    return str(value)


__all__ = [
    "NOT_AVAILABLE",
    "is_missing",
    "round_display",
    "format_number",
    "format_int",
    "format_percent",
    "format_date",
]
