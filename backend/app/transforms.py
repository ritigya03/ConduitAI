"""Transform catalogue — the fixed set of functions a mapping-spec entry's
`transform.function` name can refer to (see app.mapping_spec's
_TRANSFORM_BY_FIELD_TYPE, Day 3). Each function takes one raw string
value and returns a TransformResult: either a successfully-typed value,
or an error_code and no value. None in, None out is always success —
"no value" isn't this layer's problem, app.record_builder decides
whether a missing value on a *required* field is MISSING_REQUIRED.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NamedTuple


class TransformResult(NamedTuple):
    value: object | None
    error_code: str | None


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_CURRENCY_SYMBOLS = "$€£¥"


def trim(raw_value: str | None) -> TransformResult:
    if raw_value is None:
        return TransformResult(None, None)
    stripped = raw_value.strip()
    return TransformResult(stripped or None, None)


def parse_date(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    value = raw_value.strip()

    if _ISO_DATE_RE.match(value):
        try:
            return TransformResult(date.fromisoformat(value), None)
        except ValueError:
            return TransformResult(None, "TYPE_COERCION_FAILED")

    match = _SLASH_DATE_RE.match(value)
    if match:
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12:
            day, month = first, second
        elif second > 12:
            day, month = second, first
        else:
            return TransformResult(None, "DATE_AMBIGUOUS")
        try:
            return TransformResult(date(year, month, day), None)
        except ValueError:
            return TransformResult(None, "TYPE_COERCION_FAILED")

    return TransformResult(None, "TYPE_COERCION_FAILED")


def to_int(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    try:
        return TransformResult(int(raw_value.strip()), None)
    except ValueError:
        return TransformResult(None, "TYPE_COERCION_FAILED")


def to_minor_units(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    value = raw_value.strip()
    for symbol in _CURRENCY_SYMBOLS:
        value = value.replace(symbol, "")
    value = value.strip()

    negative = value.startswith("-")
    if negative:
        value = value[1:]

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")  # EU: 1.234,56
        else:
            value = value.replace(",", "")  # US: 1,234.56
    elif "," in value:
        integer_part, _, frac = value.rpartition(",")
        value = f"{integer_part}.{frac}" if len(frac) == 2 else value.replace(",", "")

    try:
        amount = Decimal(value)
    except InvalidOperation:
        return TransformResult(None, "TYPE_COERCION_FAILED")

    minor_units = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return TransformResult(-minor_units if negative else minor_units, None)


def map_enum(raw_value: str | None, enum_values: list[str]) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    normalized = raw_value.strip().lower()
    for allowed in enum_values:
        if allowed.lower() == normalized:
            return TransformResult(allowed, None)
    return TransformResult(None, "ENUM_INVALID")
