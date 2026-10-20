"""A restricted expression language for user-defined metrics.

Security model
--------------

The Formula Lab lets users write expressions such as::

    (CLOSE - PREVCLOSE) / PREVCLOSE * 100
    AVERAGE(CLOSE, 5)
    IF(DELIVERY % > 60 AND TOTTRDQTY < ROLLING_AVG(TOTTRDQTY, 25), "HIGH", "")

Those expressions are **never** handed to :func:`eval` or :func:`exec`.
Instead this module implements a hand-written lexer, recursive-descent parser
and tree-walking evaluator:

* only the operators, functions and identifiers on the allow-lists below are
  accepted — anything else is a compile-time error;
* there is no attribute access, no subscripting, no imports, no namespaces, so
  an expression cannot reach the filesystem, the network, ``os``, ``sys`` or
  any other module;
* evaluation is bounded — no loops, no recursion, no user-defined callables.

The public surface is:

``FormulaCompiler().compile(text)``  -> :class:`CompiledFormula`
``CompiledFormula.evaluate(context)`` -> scalar for one row
``evaluate_column(series, text)``    -> list of results for a whole column
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class FormulaError(Exception):
    """Raised for any invalid or unsupported expression."""

    def __init__(self, message: str, position: int | None = None):
        self.position = position
        super().__init__(message if position is None else f"{message} (at character {position})")


class FormulaSecurityError(FormulaError):
    """Raised when an expression attempts something outside the allow-list."""


# --------------------------------------------------------------------------
# Allow-lists
# --------------------------------------------------------------------------

#: Functions that may be called from a formula.
ALLOWED_FUNCTIONS: Tuple[str, ...] = (
    "AVERAGE", "SUM", "MIN", "MAX", "ABS", "ROUND", "SQRT", "POWER", "POW",
    "IF", "AND", "OR", "NOT", "COALESCE", "SAFE_DIV", "SAFEDIV",
    "ROLLING_AVG", "ROLLING_SUM", "ROLLING_MIN", "ROLLING_MAX",
    "ROLLING_STD", "DELTA", "PCT_CHANGE", "RANK_PCT",
)

#: Words that would indicate an attempt to escape the sandbox.
FORBIDDEN_TOKENS: Tuple[str, ...] = (
    "__", "import", "exec", "eval", "compile", "open", "lambda", "def",
    "class", "global", "nonlocal", "os.", "sys.", "subprocess", "socket",
    "requests", "urllib", "getattr", "setattr", "delattr", "globals",
    "locals", "vars", "input", "breakpoint", "memoryview", "pickle",
)

#: Named constants available to every formula.
CONSTANTS: Dict[str, float] = {
    "PI": math.pi,
    "E": math.e,
    "TRUE": 1.0,
    "FALSE": 0.0,
}

#: Arrow / comparison operators expressed as words.
WORD_OPERATORS = {"AND": "and", "OR": "or", "NOT": "not"}

#: Excel/workbook column headings written the way the worksheet writes them.
#: They are rewritten to canonical field names *before* tokenising.  Doing it
#: here — rather than letting the lexer swallow spaces — keeps the grammar
#: unambiguous: ``AND``/``OR`` are always operators and ``%`` is always the
#: remainder operator, never part of a name.  The substitution only ever happens
#: outside string literals.  Longest label first; see ``_SORTED_LABEL_ALIASES``.
LABEL_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("DELIVERABLE QUANTITY", "DELIVERABLE_QTY"),
    ("DELIVERABLE QTY", "DELIVERABLE_QTY"),
    ("TOTAL TRADED QUANTITY", "TOTTRDQTY"),
    ("TOTAL TRADED QTY", "TOTTRDQTY"),
    ("TOTAL TRADED VALUE", "TOTTRDVAL"),
    ("AVG PRICE (WINDOW)", "AVG_PRICE_AVG"),
    ("DELIVERY % (WINDOW)", "DELIVERY_AVG"),
    ("ACTION (WINDOW)", "ACTION_AVG"),
    ("VOL MA UNAVAILABLE", "VOL_MA_ERROR"),
    ("TRADE INDICATOR", "TRADE_INDICATOR"),
    ("PERCENT CHANGE", "PCT_CHANGE"),
    ("DELIVERY PERCENT", "DELIVERY_PCT"),
    ("AVERAGE PRICE", "AVG_PRICE"),
    ("VOL MA WINDOW", "VOL_MA_WINDOW"),
    ("TOTAL TRADES", "TOTALTRADES"),
    ("TODAY DELIVERY", "TODAY_DELIVERY"),
    ("AVG DELIVERY", "DELIVERY_AVG"),
    ("AVG VOLUME", "AVG_VOLUME"),
    ("AVG TRADES", "AVG_TRADES"),
    ("V2 BRONZE", "BADGE_BRONZE"),
    ("V2 SILVER", "BADGE_SILVER"),
    ("V2 GOLD", "BADGE_GOLD"),
    ("VOL BADGE", "VOL_BADGE"),
    ("VOL LEVEL", "VOL_LEVEL"),
    ("TRADE DATE", "TRADE_DATE"),
    ("PREV CLOSE", "PREVCLOSE"),
    ("PCT CHANGE", "PCT_CHANGE"),
    ("D IND.", "TRADE_INDICATOR"),
    ("D IND", "TRADE_INDICATOR"),
    ("AVG PRICE", "AVG_PRICE"),
    ("DELIVERY PCT", "DELIVERY_PCT"),
    ("DELIVERY %", "DELIVERY_PCT"),
    ("% CHANGE", "PCT_CHANGE"),
    ("VOL MA", "VOL_MA"),
)

#: The same table, longest label first, so ``AVG PRICE (WINDOW)`` is rewritten
#: before the shorter ``AVG PRICE`` can match part of it.
_SORTED_LABEL_ALIASES: Tuple[Tuple[str, str], ...] = tuple(
    sorted(LABEL_ALIASES, key=lambda item: -len(item[0]))
)

#: Alias table used when resolving a bare identifier, in addition to the above.
FIELD_ALIASES: Dict[str, str] = {
    "DELIVERY_PERCENT": "DELIVERY_PCT",
    "DATE": "TRADE_DATE",
    "DV1": "VOL_BADGE",
    "DV2": "VOL_LEVEL",
    "D_IND": "TRADE_INDICATOR",
    "D_IND.": "TRADE_INDICATOR",
    "QTY_PER_TRADE": "ACTION",
    "VOL_MA_UNAVAILABLE": "VOL_MA_ERROR",
    "CMP_GT_AVG_PRICE": "CMP_VERDICT",
}

_NUMBER_RE = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_STRING_SPLIT_RE = re.compile(r"(\"[^\"]*\"|'[^']*')")


def rewrite_label_aliases(text: str) -> str:
    """Replace workbook-style column headings with canonical field names.

    Only text outside string literals is touched, so a label inside a string
    (``"40% Plus"``) is never rewritten.
    """
    if not text:
        return text

    def _rewrite(segment: str) -> str:
        for label, field in _SORTED_LABEL_ALIASES:
            pattern = re.compile(
                re.escape(label).replace(r"\ ", r"\s+"), re.IGNORECASE)
            segment = pattern.sub(field, segment)
        return segment

    parts = _STRING_SPLIT_RE.split(text)
    return "".join(part if part[:1] in ("'", '"') else _rewrite(part) for part in parts)


def normalise_field_name(text: str) -> str:
    """Upper-case and clean a written field name for lookup."""
    token = str(text).strip().upper()
    token = re.sub(r"[^A-Z0-9]+", "_", token).strip("_")
    return token


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------


@dataclass
class Token:
    kind: str          # NUMBER | STRING | IDENT | OP | LPAREN | RPAREN | COMMA | EOF
    value: Any
    position: int


OPERATORS: Tuple[str, ...] = (
    ">=", "<=", "<>", "!=", "==", "=", ">", "<",
    "+", "-", "*", "/", "%", "^",
)


def tokenize(text: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", "(", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RPAREN", ")", i))
            i += 1
            continue
        if ch == ",":
            tokens.append(Token("COMMA", ",", i))
            i += 1
            continue
        if ch in ("'", '"'):
            match = _STRING_RE.match(text, i)
            if not match:
                raise FormulaError("unterminated string literal", i)
            value = match.group(1) if match.group(1) is not None else match.group(2)
            tokens.append(Token("STRING", value, i))
            i = match.end()
            continue
        number = _NUMBER_RE.match(text, i)
        if number and (ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit())):
            tokens.append(Token("NUMBER", float(number.group(0)), i))
            i = number.end()
            continue
        matched_op = None
        for op in OPERATORS:
            if text.startswith(op, i):
                matched_op = op
                break
        if matched_op:
            tokens.append(Token("OP", matched_op, i))
            i += len(matched_op)
            continue
        ident = _IDENT_RE.match(text, i)
        if ident:
            value = ident.group(0).strip()
            tokens.append(Token("IDENT", value, i))
            i = ident.end()
            continue
        raise FormulaError(f"unexpected character {ch!r}", i)
    tokens.append(Token("EOF", None, n))
    return tokens


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: Any


@dataclass(frozen=True)
class FieldRef:
    name: str


@dataclass(frozen=True)
class Unary:
    op: str
    operand: Any


@dataclass(frozen=True)
class Binary:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Call:
    name: str
    args: Tuple[Any, ...]


# --------------------------------------------------------------------------
# Parser (recursive descent, precedence climbing)
# --------------------------------------------------------------------------

_PRECEDENCE = {
    "or": 1,
    "and": 2,
    "=": 3, "==": 3, "<>": 3, "!=": 3, ">": 3, "<": 3, ">=": 3, "<=": 3,
    "+": 4, "-": 4,
    "*": 5, "/": 5, "%": 5,
    "^": 6,
}

#: ``NOT`` is a prefix operator that binds looser than a comparison (Excel order).
_NOT_PRECEDENCE = 3


class Parser:
    def __init__(self, tokens: Sequence[Token], allowed_fields: Iterable[str]):
        self.tokens = list(tokens)
        self.pos = 0
        self.allowed = {f.upper() for f in allowed_fields}
        self.window_functions = {
            "ROLLING_AVG", "ROLLING_SUM", "ROLLING_MIN", "ROLLING_MAX",
            "ROLLING_STD", "DELTA", "PCT_CHANGE",
        }

    # -- token helpers ---------------------------------------------------
    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, kind: str) -> Token:
        if self.current.kind != kind:
            raise FormulaError(f"expected {kind}, found {self.current.value!r}", self.current.position)
        return self.advance()

    # -- grammar ---------------------------------------------------------
    def parse(self) -> Any:
        node = self.parse_expression(1)
        if self.current.kind != "EOF":
            raise FormulaError(f"unexpected trailing input {self.current.value!r}", self.current.position)
        return node

    def parse_expression(self, min_precedence: int) -> Any:
        if (min_precedence <= _NOT_PRECEDENCE and self.current.kind == "IDENT"
                and self.current.value.upper() == "NOT"):
            # Excel binds NOT *looser* than the comparison operators but tighter
            # than AND/OR, so ``NOT a = b`` means ``NOT (a = b)``.
            self.advance()
            left: Any = Unary("not", self.parse_expression(_NOT_PRECEDENCE))
        else:
            left = self.parse_unary()
        while True:
            token = self.current
            op = None
            if token.kind == "OP":
                op = token.value
            elif token.kind == "IDENT" and token.value.upper() in WORD_OPERATORS:
                op = WORD_OPERATORS[token.value.upper()]
            if op is None:
                break
            precedence = _PRECEDENCE.get(op)
            if precedence is None or precedence < min_precedence:
                break
            self.advance()
            right = self.parse_expression(precedence + 1)
            left = Binary(op, left, right)
        return left

    def parse_unary(self) -> Any:
        token = self.current
        if token.kind == "OP" and token.value in ("-", "+"):
            self.advance()
            operand = self.parse_unary()
            return Unary(token.value, operand)
        return self.parse_primary()

    def parse_primary(self) -> Any:
        token = self.current
        if token.kind == "NUMBER":
            self.advance()
            return Literal(token.value)
        if token.kind == "STRING":
            self.advance()
            return Literal(token.value)
        if token.kind == "LPAREN":
            self.advance()
            node = self.parse_expression(1)
            self.expect("RPAREN")
            return node
        if token.kind == "IDENT":
            name = token.value.strip()
            upper = name.upper()
            self.advance()
            if self.current.kind == "LPAREN":
                self.advance()
                args: List[Any] = []
                if self.current.kind != "RPAREN":
                    while True:
                        args.append(self.parse_expression(1))
                        if self.current.kind == "COMMA":
                            self.advance()
                            continue
                        break
                self.expect("RPAREN")
                return self._make_call(upper, tuple(args), token.position)
            return self._make_field(name, token.position)
        raise FormulaError(f"unexpected token {token.value!r}", token.position)

    # -- validation ------------------------------------------------------
    def _guard_forbidden(self, name: str, position: int) -> None:
        """Reject names that smell like an escape attempt.

        Only applied to identifiers that did **not** resolve to a known field or
        constant, so a legitimate column called ``OPEN`` is never mistaken for
        the ``open()`` builtin.
        """
        lowered = name.lower()
        for bad in FORBIDDEN_TOKENS:
            if bad in lowered:
                raise FormulaSecurityError(f"forbidden token {bad!r} in expression", position)

    def _make_call(self, name: str, args: Tuple[Any, ...], position: int) -> Any:
        if name not in ALLOWED_FUNCTIONS:
            self._guard_forbidden(name, position)
            raise FormulaSecurityError(
                f"function {name!r} is not allowed. Allowed functions: "
                + ", ".join(sorted(ALLOWED_FUNCTIONS)),
                position,
            )
        if name in self.window_functions:
            if len(args) != 2:
                raise FormulaError(f"{name} expects (field, periods)", position)
            if not isinstance(args[0], FieldRef):
                raise FormulaError(f"{name} requires a field name as its first argument", position)
            if not isinstance(args[1], Literal) or not isinstance(args[1].value, (int, float)):
                raise FormulaError(f"{name} requires a numeric period as its second argument", position)
        return Call(name, args)

    def _make_field(self, raw: str, position: int) -> Any:
        normalised = normalise_field_name(raw)
        for candidate in (raw.strip().upper(), normalised, FIELD_ALIASES.get(normalised)):
            if candidate and candidate in CONSTANTS:
                return Literal(CONSTANTS[candidate])
            if candidate and candidate in self.allowed:
                return FieldRef(candidate)
        self._guard_forbidden(raw, position)
        raise FormulaError(
            f"unknown field {raw!r}. Available fields: " + ", ".join(sorted(self.allowed)),
            position,
        )


# --------------------------------------------------------------------------
# Evaluation context
# --------------------------------------------------------------------------


@dataclass
class RowContext:
    """Everything a formula may read for a single row.

    ``series`` holds the full column (in row order) and ``index`` points at the
    current row so that window functions can look backwards.
    """

    values: Mapping[str, Any]
    series: Mapping[str, Sequence[Any]]
    index: int
    null_policy: str = "blank"   # blank | zero | error

    def get(self, name: str) -> Any:
        value = self.values.get(name)
        return self._apply_null_policy(value)

    def _apply_null_policy(self, value: Any) -> Any:
        if _is_missing(value):
            if self.null_policy == "zero":
                return 0.0
            return None
        return value

    def window(self, name: str, periods: int) -> Optional[List[float]]:
        if periods is None or periods < 1:
            raise FormulaError("window period must be >= 1")
        column = self.series.get(name)
        if column is None:
            raise FormulaError(f"unknown field {name!r} for window function")
        start = max(0, self.index - int(periods) + 1)
        raw = column[start:self.index + 1]
        out: List[float] = []
        for item in raw:
            if _is_missing(item):
                if self.null_policy == "zero":
                    out.append(0.0)
                continue
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                out.append(float(item))
        return out


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    """``None``, a blank string and ``NaN`` all mean "there is no value here".

    A pandas column that mixes a number with a blank becomes ``float64`` and the
    blank turns into ``NaN`` — treating that as a real number would silently
    poison every formula it touches (and would let ``NaN`` slip past the null
    policies), so it is normalised here, once, for the whole evaluator.
    """
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    return isinstance(value, str) and value.strip() == ""


def _to_number(value: Any) -> float:
    if _is_missing(value):
        raise FormulaError("cannot use a blank value in a numeric operation")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError as exc:
        raise FormulaError(f"{value!r} is not numeric") from exc


def _is_blank(value: Any) -> bool:
    return _is_missing(value)


def _truthy(value: Any) -> bool:
    if _is_blank(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() not in ("", "FALSE", "0", "NO")


def _numeric_args(args: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for arg in args:
        if _is_missing(arg):
            continue
        if isinstance(arg, (list, tuple)):
            out.extend(_numeric_args(arg))
        else:
            out.append(_to_number(arg))
    return out


def _eval(node: Any, ctx: RowContext) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, FieldRef):
        return ctx.get(node.name)
    if isinstance(node, Unary):
        value = _eval(node.operand, ctx)
        if node.op == "not":
            return not _truthy(value)
        number = _to_number(value)
        return number if node.op == "+" else -number
    if isinstance(node, Binary):
        return _eval_binary(node, ctx)
    if isinstance(node, Call):
        return _eval_call(node, ctx)
    raise FormulaError(f"unsupported expression node {node!r}")


def _eval_binary(node: Binary, ctx: RowContext) -> Any:
    op = node.op
    if op == "and":
        return _truthy(_eval(node.left, ctx)) and _truthy(_eval(node.right, ctx))
    if op == "or":
        return _truthy(_eval(node.left, ctx)) or _truthy(_eval(node.right, ctx))

    left = _eval(node.left, ctx)
    right = _eval(node.right, ctx)

    if op in ("=", "=="):
        return _compare_equal(left, right)
    if op in ("<>", "!="):
        return not _compare_equal(left, right)
    if op in (">", "<", ">=", "<="):
        if _is_blank(left) or _is_blank(right):
            return False
        lnum, rnum = _to_number(left), _to_number(right)
        return {">": lnum > rnum, "<": lnum < rnum,
                ">=": lnum >= rnum, "<=": lnum <= rnum}[op]

    if op in ("+", "-", "*", "/", "%", "^") and (isinstance(left, str) or isinstance(right, str)):
        if op == "+" and isinstance(left, str) and isinstance(right, str):
            return left + right
    lnum, rnum = _to_number(left), _to_number(right)
    if op == "+":
        return lnum + rnum
    if op == "-":
        return lnum - rnum
    if op == "*":
        return lnum * rnum
    if op == "/":
        if rnum == 0:
            return None
        return lnum / rnum
    if op == "%":
        # Excel's MOD: the remainder takes the sign of the divisor.
        if rnum == 0:
            return None
        return lnum % rnum
    if op == "^":
        return lnum ** rnum
    raise FormulaError(f"unsupported operator {op!r}")


def _compare_equal(left: Any, right: Any) -> bool:
    if _is_blank(left) and _is_blank(right):
        return True
    if _is_blank(left) or _is_blank(right):
        return False
    if isinstance(left, str) or isinstance(right, str):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        try:
            return _to_number(left) == _to_number(right)
        except FormulaError:
            return str(left).strip().upper() == str(right).strip().upper()
    return float(left) == float(right)


def _eval_call(node: Call, ctx: RowContext) -> Any:
    name = node.name

    # window functions read the full column and look backwards
    if name in ("ROLLING_AVG", "ROLLING_SUM", "ROLLING_MIN", "ROLLING_MAX",
                "ROLLING_STD", "DELTA", "PCT_CHANGE"):
        field = node.args[0].name
        periods = int(_to_number(_eval(node.args[1], ctx)))
        window = ctx.window(field, periods)
        if name == "ROLLING_AVG":
            return sum(window) / len(window) if window else None
        if name == "ROLLING_SUM":
            return sum(window) if window else None
        if name == "ROLLING_MIN":
            return min(window) if window else None
        if name == "ROLLING_MAX":
            return max(window) if window else None
        if name == "ROLLING_STD":
            if len(window) < 2:
                return None
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)
            return math.sqrt(var)
        if name == "DELTA":
            if len(window) < 2:
                return None
            return window[-1] - window[0]
        if name == "PCT_CHANGE":
            if len(window) < 2 or window[0] == 0:
                return None
            return (window[-1] - window[0]) / window[0]
        raise FormulaError(f"unsupported window function {name}")

    values = [_eval(arg, ctx) for arg in node.args]

    if name == "IF":
        if len(values) not in (2, 3):
            raise FormulaError("IF expects (condition, true_value[, false_value])")
        if _truthy(values[0]):
            return values[1]
        return values[2] if len(values) == 3 else None
    if name == "AND":
        return all(_truthy(v) for v in values)
    if name == "OR":
        return any(_truthy(v) for v in values)
    if name == "NOT":
        return not _truthy(values[0])
    if name == "COALESCE":
        for value in values:
            if not _is_blank(value):
                return value
        return None

    if name == "AVERAGE":
        # AVERAGE(field, n) is the documented trailing N-session mean.
        if (len(node.args) == 2 and isinstance(node.args[0], FieldRef)
                and isinstance(node.args[1], Literal)
                and isinstance(node.args[1].value, (int, float))
                and float(node.args[1].value).is_integer()
                and float(node.args[1].value) >= 1):
            window = ctx.window(node.args[0].name, int(node.args[1].value))
            return sum(window) / len(window) if window else None
        numbers = _numeric_args(values)
        return sum(numbers) / len(numbers) if numbers else None
    if name == "SUM":
        numbers = _numeric_args(values)
        return sum(numbers) if numbers else None
    if name == "MIN":
        numbers = _numeric_args(values)
        return min(numbers) if numbers else None
    if name == "MAX":
        numbers = _numeric_args(values)
        return max(numbers) if numbers else None
    if name == "ABS":
        return abs(_to_number(values[0]))
    if name == "ROUND":
        digits = int(_to_number(values[1])) if len(values) > 1 else 0
        value = _to_number(values[0])
        if value is None:
            return None
        factor = 10 ** digits
        scaled = value * factor
        rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
        return rounded / factor
    if name == "SQRT":
        value = _to_number(values[0])
        return math.sqrt(value) if value is not None and value >= 0 else None
    if name in ("POWER", "POW"):
        return _to_number(values[0]) ** _to_number(values[1])
    if name in ("SAFE_DIV", "SAFEDIV"):
        denominator = _to_number(values[1])
        if denominator == 0:
            return values[2] if len(values) > 2 else None
        return _to_number(values[0]) / denominator
    if name == "RANK_PCT":
        # share of the values strictly below the first argument
        target = _to_number(values[0])
        population = _numeric_args(values[1:])
        if not population:
            return None
        return sum(1 for v in population if v < target) / len(population)
    raise FormulaSecurityError(f"function {name!r} is not implemented")


# --------------------------------------------------------------------------
# Compiled formula
# --------------------------------------------------------------------------


@dataclass
class CompiledFormula:
    text: str
    ast: Any
    fields: List[str]

    def evaluate(self, context: RowContext) -> Any:
        return _eval(self.ast, context)

    def evaluate_row(
        self,
        values: Mapping[str, Any],
        series: Optional[Mapping[str, Sequence[Any]]] = None,
        index: int = 0,
        null_policy: str = "blank",
    ) -> Any:
        series = series if series is not None else {k: [v] for k, v in values.items()}
        return _eval(self.ast, RowContext(values, series, index, null_policy))


class FormulaCompiler:
    """Compiles restricted expressions. One instance may be reused."""

    def __init__(self, extra_fields: Iterable[str] = ()):
        self._extra_fields = {f.upper() for f in extra_fields}

    def compile(self, text: str, allowed_fields: Iterable[str]) -> CompiledFormula:
        if text is None:
            raise FormulaError("expression is empty")
        stripped = text.strip()
        if not stripped:
            raise FormulaError("expression is empty")
        if stripped.startswith("="):
            stripped = stripped[1:].strip()
        stripped = rewrite_label_aliases(stripped)
        tokens = tokenize(stripped)
        allowed = {f.upper() for f in allowed_fields} | self._extra_fields
        parser = Parser(tokens, allowed)
        ast = parser.parse()
        return CompiledFormula(text=text, ast=ast, fields=sorted(set(collect_fields(ast))))

    def validate(self, text: str, allowed_fields: Iterable[str]) -> Tuple[bool, str]:
        """Return ``(ok, message)`` without raising."""
        try:
            self.compile(text, allowed_fields)
            return True, "valid"
        except FormulaError as exc:
            return False, str(exc)


def collect_fields(node: Any) -> List[str]:
    found: List[str] = []
    if isinstance(node, FieldRef):
        found.append(node.name)
    elif isinstance(node, Unary):
        found.extend(collect_fields(node.operand))
    elif isinstance(node, Binary):
        found.extend(collect_fields(node.left))
        found.extend(collect_fields(node.right))
    elif isinstance(node, Call):
        for arg in node.args:
            found.extend(collect_fields(arg))
    return found


def evaluate_column(
    expression: str,
    series: Mapping[str, Sequence[Any]],
    allowed_fields: Iterable[str],
    null_policy: str = "blank",
) -> Tuple[List[Any], Optional[str]]:
    """Evaluate ``expression`` for every row of ``series``.

    Returns ``(results, error)``; ``error`` is ``None`` on success and every
    remaining value is ``None`` when the expression fails to compile.

    ``null_policy`` only changes how a *missing* input is treated:

    ``blank``
        the row's result is unavailable (``None``) — the default.
    ``zero``
        a missing input counts as ``0``.
    ``error``
        like ``blank``, but the failures are counted and reported in the
        returned message so the formula's author is told the data is incomplete
        instead of quietly getting a blank column.
    """
    length = max((len(v) for v in series.values()), default=0)
    try:
        compiled = FormulaCompiler().compile(expression, allowed_fields)
    except FormulaError as exc:
        # Nothing could be evaluated at all — hand back no values so callers can
        # tell "the expression is broken" from "some rows were unusable".
        return [], str(exc)

    results: List[Any] = []
    failures = 0
    for index in range(length):
        values = {name: (col[index] if index < len(col) else None) for name, col in series.items()}
        try:
            value = compiled.evaluate_row(values, series, index, null_policy)
        except FormulaError:
            results.append(None)
            failures += 1
            continue
        results.append(None if (isinstance(value, float) and value != value) else value)
    if null_policy == "error" and failures:
        return results, (
            f"{failures} of {length} row(s) could not be evaluated because a "
            f"required value is missing"
        )
    return results, None


__all__ = [
    "FormulaError",
    "FormulaSecurityError",
    "FormulaCompiler",
    "CompiledFormula",
    "RowContext",
    "evaluate_column",
    "collect_fields",
    "ALLOWED_FUNCTIONS",
    "CONSTANTS",
    "tokenize",
]
