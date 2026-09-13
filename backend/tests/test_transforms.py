from datetime import date

from app.transforms import TransformResult, map_enum, parse_date, to_int, to_minor_units, trim


def test_trim_strips_whitespace():
    assert trim("  hi  ") == TransformResult("hi", None)


def test_trim_empty_string_becomes_none():
    assert trim("   ") == TransformResult(None, None)


def test_trim_none_passes_through():
    assert trim(None) == TransformResult(None, None)


def test_parse_date_iso_format():
    assert parse_date("2026-01-15") == TransformResult(date(2026, 1, 15), None)


def test_parse_date_unambiguous_slash_format_day_first():
    # 25 can't be a month, so this must be DD/MM/YYYY
    assert parse_date("25/12/2025") == TransformResult(date(2025, 12, 25), None)


def test_parse_date_unambiguous_slash_format_month_first():
    # 25 in the second slot can't be a month, so the first slot is the month
    assert parse_date("12/25/2025") == TransformResult(date(2025, 12, 25), None)


def test_parse_date_ambiguous_flags_instead_of_guessing():
    result = parse_date("03/04/2025")
    assert result.value is None
    assert result.error_code == "DATE_AMBIGUOUS"


def test_parse_date_garbage_is_type_coercion_failed():
    result = parse_date("not-a-date")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_parse_date_none_passes_through():
    assert parse_date(None) == TransformResult(None, None)


def test_to_int_parses_plain_integer():
    assert to_int("42") == TransformResult(42, None)


def test_to_int_garbage_is_type_coercion_failed():
    result = to_int("abc")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_to_minor_units_plain_decimal():
    assert to_minor_units("100.00") == TransformResult(10000, None)


def test_to_minor_units_us_formatted_with_symbol_and_thousands_comma():
    assert to_minor_units("$1,234.56") == TransformResult(123456, None)


def test_to_minor_units_negative_amount():
    assert to_minor_units("-450.00") == TransformResult(-45000, None)


def test_to_minor_units_eu_formatted():
    assert to_minor_units("1.234,56") == TransformResult(123456, None)


def test_to_minor_units_garbage_is_type_coercion_failed():
    result = to_minor_units("not-money")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_to_minor_units_none_passes_through():
    assert to_minor_units(None) == TransformResult(None, None)


def test_map_enum_case_insensitive_match():
    assert map_enum("OPEN", ["open", "paid"]) == TransformResult("open", None)


def test_map_enum_invalid_value():
    result = map_enum("bogus", ["open", "paid"])
    assert result.value is None
    assert result.error_code == "ENUM_INVALID"


def test_map_enum_none_passes_through():
    assert map_enum(None, ["open", "paid"]) == TransformResult(None, None)
