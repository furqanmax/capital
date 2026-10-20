"""Formula Lab service.

Wraps the :mod:`services.safe_parser` sandbox with persistence, versioning and
test/preview support.

Guarantees
----------

* Every saved formula is compiled through the restricted parser before it is
  stored, so an expression with a forbidden token can never reach the database.
* Every update appends an immutable row to ``formula_versions``; the previous
  expression is always recoverable and can be restored in one call.
* ``apply`` never writes into the raw imported columns — outputs go to new
  columns named after the formula's ``output_column``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import select

from database.db import Database
from models import Formula, FormulaVersion

from .analytics import FORMULA_FIELDS
from .safe_parser import FormulaError, FormulaCompiler, evaluate_column

VALID_DATA_TYPES = ("float", "int", "str", "bool")
VALID_NULL_POLICIES = ("blank", "zero", "error")

#: Syntax reference shown in the Formula Lab help panel and in docs/.
SYNTAX_REFERENCE = """\
Capital Formula Language
========================

Operators
    +  -  *  /  %(remainder)  ^(power)
    =  <>  <  >  <=  >=        (numeric comparison; <> means "not equal")
    AND  OR  NOT               (logical; NOT binds looser than a comparison)

Fields (case-insensitive)
    SYMBOL  DATE  OPEN  HIGH  LOW  CLOSE  LAST  PREVCLOSE
    TOTTRDQTY  TOTTRDVAL  TOTALTRADES  DELIVERY %

    A formula reads the raw session fields only. Workbook headings are accepted
    as written and are matched ignoring case and spacing, so all of these mean
    the same field:
        DELIVERY %   Delivery %   DELIVERY PCT   delivery_pct
        TOTTRDQTY    Total Traded Qty
        PREVCLOSE    Prev Close
        TOTALTRADES  Total Trades
        TRADE_DATE   Trade Date     DATE

Constants
    PI  E  TRUE  FALSE

Functions
    AVERAGE(x, y, ...)          arithmetic mean of the arguments
    AVERAGE(FIELD, n)           trailing mean of FIELD over n sessions
                                (n includes the current session)
    SUM / MIN / MAX(...)        aggregate of the arguments
    ABS(x) ROUND(x, digits) SQRT(x) POWER(x, y)
    IF(condition, a[, b])       Excel-style conditional
    AND(...) OR(...) NOT(x) COALESCE(...)
    SAFE_DIV(a, b[, fallback])  division that returns fallback when b = 0
    ROLLING_AVG(FIELD, n)       trailing moving average
    ROLLING_SUM / ROLLING_MIN / ROLLING_MAX / ROLLING_STD(FIELD, n)
    DELTA(FIELD, n)             value change across the trailing window
    PCT_CHANGE(FIELD, n)        relative change across the trailing window
    RANK_PCT(x, values...)      share of values strictly below x

Semantics that matter
    * Windows look *backwards*: ROLLING_AVG(CLOSE, 5) on the 10th session of a
      symbol averages sessions 6-10. Fewer than n sessions are available for the
      first rows, so the window is shorter there rather than padded with zeros.
    * Blank (missing) values are skipped by aggregates and never coerced to 0
      unless the formula's null policy is set to "zero".
    * Null policies: "blank" (a missing input makes the row unavailable),
      "zero" (a missing input counts as 0) and "error" (like blank, but the
      count of unusable rows is reported). A missing value is never silently
      turned into a wrong number.
    * Division by zero yields a blank result (never an exception); use
      SAFE_DIV when you want a fallback value instead.
    * % is the remainder operator (Excel's MOD), not a percentage sign.
    * Text in double quotes is never re-interpreted ("Delivery %" stays text).
    * There is no access to files, network, modules or attributes: only the
      fields, constants and functions listed above exist.

Examples
    (LAST - PREVCLOSE) / PREVCLOSE * 100
    AVERAGE(CLOSE, 20)
    IF(DELIVERY % > 60 AND TOTTRDQTY < ROLLING_AVG(TOTTRDQTY, 25), "HIGH", "")
    (OPEN + HIGH + LOW + CLOSE) / 4
"""


@dataclass
class PreviewResult:
    expression: str
    values: List[Any]
    error: Optional[str] = None
    fields: List[str] = None  # type: ignore[assignment]

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_frame(self, index: Optional[Sequence[Any]] = None) -> pd.DataFrame:
        return pd.DataFrame({"value": self.values}, index=index)


class FormulaService:
    """CRUD + execution for user-defined metrics."""

    def __init__(self, database: Database):
        self.db = database
        self.compiler = FormulaCompiler()

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def list_formulas(self, enabled_only: bool = False) -> List[Formula]:
        with self.db.session() as session:
            stmt = select(Formula).order_by(Formula.category, Formula.name)
            if enabled_only:
                stmt = stmt.where(Formula.enabled.is_(True))
            return list(session.scalars(stmt))

    def get(self, formula_id: int) -> Optional[Formula]:
        with self.db.session() as session:
            return session.get(Formula, formula_id)

    def get_by_name(self, name: str) -> Optional[Formula]:
        with self.db.session() as session:
            return session.scalar(select(Formula).where(Formula.name == name))

    def versions(self, formula_id: int) -> List[FormulaVersion]:
        with self.db.session() as session:
            return list(session.scalars(
                select(FormulaVersion)
                .where(FormulaVersion.formula_id == formula_id)
                .order_by(FormulaVersion.version.desc())
            ))

    # ------------------------------------------------------------------
    # validation / testing
    # ------------------------------------------------------------------

    def validate(self, expression: str) -> Tuple[bool, str]:
        """``(ok, message)`` — never raises."""
        return self.compiler.validate(expression, FORMULA_FIELDS)

    def required_fields(self, expression: str) -> List[str]:
        ok, message = self.validate(expression)
        if not ok:
            raise FormulaError(message)
        compiled = self.compiler.compile(expression, FORMULA_FIELDS)
        return compiled.fields

    def preview(
        self,
        expression: str,
        frame: pd.DataFrame,
        null_policy: str = "blank",
        limit: Optional[int] = None,
    ) -> PreviewResult:
        """Evaluate ``expression`` against a data frame without saving it."""
        if frame is None or frame.empty:
            return PreviewResult(expression=expression, values=[], fields=[])

        ok, message = self.validate(expression)
        if not ok:
            return PreviewResult(expression=expression, values=[], error=message, fields=[])

        series = {column.upper(): frame[column].tolist() for column in frame.columns}
        values, error = evaluate_column(expression, series, FORMULA_FIELDS, null_policy)
        if error is not None:
            return PreviewResult(expression=expression, values=[], error=error, fields=[])
        if limit:
            values = values[:limit]
        compiled = self.compiler.compile(expression, FORMULA_FIELDS)
        return PreviewResult(expression=expression, values=values, fields=compiled.fields)

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        expression: str,
        output_column: str,
        description: str = "",
        category: str = "custom",
        data_type: str = "float",
        null_policy: str = "blank",
        display_format: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        validation_rules: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
        builtin: bool = False,
        change_note: str = "Initial definition",
    ) -> Formula:
        self._validate_inputs(name, expression, output_column, data_type, null_policy)
        with self.db.session() as session:
            if session.scalar(select(Formula).where(Formula.name == name)) is not None:
                raise ValueError(f"a formula named {name!r} already exists")
            formula = Formula(
                name=name,
                description=description,
                category=category,
                output_column=output_column,
                expression=expression.strip(),
                data_type=data_type,
                null_policy=null_policy,
                display_format=display_format,
                parameters=parameters,
                validation_rules=validation_rules,
                enabled=enabled,
                builtin=builtin,
                current_version=1,
            )
            session.add(formula)
            session.flush()
            session.add(FormulaVersion(
                formula_id=formula.id, version=1, expression=formula.expression,
                change_note=change_note, snapshot=self._snapshot(formula),
            ))
            self.db.log("formula", f"Created formula {name!r}", entity=name,
                           session=session)
            session.flush()
            session.refresh(formula)
            return formula

    def update(
        self,
        formula_id: int,
        expression: Optional[str] = None,
        change_note: str = "",
        **fields: Any,
    ) -> Formula:
        with self.db.session() as session:
            formula = session.get(Formula, formula_id)
            if formula is None:
                raise ValueError(f"formula {formula_id} not found")

            if expression is not None and expression.strip() != formula.expression:
                ok, message = self.validate(expression)
                if not ok:
                    raise FormulaError(message)
                formula.expression = expression.strip()
                formula.current_version += 1
                session.add(FormulaVersion(
                    formula_id=formula.id,
                    version=formula.current_version,
                    expression=formula.expression,
                    change_note=change_note or "expression changed",
                    snapshot=self._snapshot(formula),
                ))

            for key, value in fields.items():
                if value is None or not hasattr(formula, key):
                    continue
                if key in ("name", "output_column") and not str(value).strip():
                    raise ValueError(f"{key} cannot be blank")
                if key == "data_type" and value not in VALID_DATA_TYPES:
                    raise ValueError(f"unknown data type {value!r}")
                if key == "null_policy" and value not in VALID_NULL_POLICIES:
                    raise ValueError(f"unknown null policy {value!r}")
                setattr(formula, key, value)

            self.db.log("formula", f"Updated formula {formula.name!r} "
                                   f"(v{formula.current_version})", entity=formula.name,
                        session=session)
            session.flush()
            session.refresh(formula)
            return formula

    def restore_version(self, formula_id: int, version: int,
                        change_note: str = "") -> Formula:
        with self.db.session() as session:
            row = session.scalar(
                select(FormulaVersion).where(
                    FormulaVersion.formula_id == formula_id,
                    FormulaVersion.version == version,
                )
            )
            if row is None:
                raise ValueError(f"version {version} not found for formula {formula_id}")
            formula = session.get(Formula, formula_id)
            if formula is None:
                raise ValueError(f"formula {formula_id} not found")
            formula.expression = row.expression
            formula.current_version += 1
            session.add(FormulaVersion(
                formula_id=formula.id,
                version=formula.current_version,
                expression=row.expression,
                change_note=change_note or f"restored from version {version}",
                snapshot=self._snapshot(formula),
            ))
            self.db.log("formula", f"Restored {formula.name!r} to version {version}",
                        entity=formula.name, session=session)
            session.flush()
            session.refresh(formula)
            return formula

    def set_enabled(self, formula_id: int, enabled: bool) -> Formula:
        return self.update(formula_id, enabled=enabled,
                           change_note=f"{'enabled' if enabled else 'disabled'}")

    def delete(self, formula_id: int) -> None:
        with self.db.session() as session:
            formula = session.get(Formula, formula_id)
            if formula is None:
                return
            name = formula.name
            session.delete(formula)
            self.db.log("formula", f"Deleted formula {name!r}", severity="warning",
                        entity=name, session=session)

    # ------------------------------------------------------------------
    # execution against a data frame
    # ------------------------------------------------------------------

    def apply(
        self,
        frame: pd.DataFrame,
        formulas: Optional[Iterable[Formula]] = None,
        strict: bool = False,
    ) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
        """Add one output column per enabled formula.

        Returns ``(frame_with_columns, results)`` where each result records the
        formula name, output column, validity and error (if any).
        """
        out = frame.copy()
        selected = list(formulas) if formulas is not None else self.list_formulas(enabled_only=True)
        results: List[Dict[str, Any]] = []
        if out.empty:
            return out, results

        series = {column.upper(): out[column].tolist() for column in out.columns}
        for formula in selected:
            values, error = evaluate_column(
                formula.expression, series, FORMULA_FIELDS, formula.null_policy
            )
            usable = bool(values)
            results.append({
                "formula": formula.name,
                "output_column": formula.output_column,
                "ok": error is None and usable,
                "error": error,
            })
            if not usable:
                # the expression never compiled: there is nothing to store
                if strict:
                    raise FormulaError(f"{formula.name}: {error}")
                continue
            if error is not None and strict:
                # e.g. the "error" null policy found rows with missing inputs
                raise FormulaError(f"{formula.name}: {error}")
            out[formula.output_column] = _coerce(values, formula.data_type)

        return out, results

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _validate_inputs(self, name: str, expression: str, output_column: str,
                         data_type: str, null_policy: str) -> None:
        if not name or not name.strip():
            raise ValueError("formula name cannot be blank")
        if not output_column or not output_column.strip():
            raise ValueError("output column cannot be blank")
        if data_type not in VALID_DATA_TYPES:
            raise ValueError(f"unknown data type {data_type!r}")
        if null_policy not in VALID_NULL_POLICIES:
            raise ValueError(f"unknown null policy {null_policy!r}")
        ok, message = self.validate(expression)
        if not ok:
            raise FormulaError(message)

    @staticmethod
    def _snapshot(formula: Formula) -> Dict[str, Any]:
        return {
            "name": formula.name,
            "description": formula.description,
            "category": formula.category,
            "output_column": formula.output_column,
            "expression": formula.expression,
            "data_type": formula.data_type,
            "null_policy": formula.null_policy,
            "display_format": formula.display_format,
            "parameters": formula.parameters,
            "validation_rules": formula.validation_rules,
            "enabled": bool(formula.enabled),
        }


def _coerce(values: Sequence[Any], data_type: str) -> List[Any]:
    out: List[Any] = []
    for value in values:
        if value is None or (isinstance(value, float) and value != value):
            out.append(None)
            continue
        if data_type == "int":
            try:
                out.append(int(round(float(value))))
            except (TypeError, ValueError):
                out.append(None)
        elif data_type == "float":
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                out.append(None)
        elif data_type == "bool":
            out.append(bool(value))
        else:
            out.append(str(value))
    return out


__all__ = [
    "FormulaService",
    "PreviewResult",
    "SYNTAX_REFERENCE",
    "VALID_DATA_TYPES",
    "VALID_NULL_POLICIES",
]
