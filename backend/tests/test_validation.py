from datetime import date, timedelta

from app.validation import validate_business_rules, validate_schema


def test_future_issue_date_is_flagged():
    values = {"issue_date": date.today() + timedelta(days=1)}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_FUTURE_DATE" for e in errors)


def test_negative_amount_is_flagged():
    values = {"amount_minor_units": -100}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_NEGATIVE_AMOUNT" for e in errors)


def test_due_date_before_issue_date_is_flagged():
    values = {"issue_date": date(2026, 2, 1), "due_date": date(2026, 1, 1)}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_DUE_BEFORE_ISSUE" for e in errors)


def test_valid_invoice_values_produce_no_business_rule_errors():
    values = {
        "issue_date": date.today(),
        "due_date": date.today() + timedelta(days=30),
        "amount_minor_units": 5000,
    }
    assert validate_business_rules("invoices", values) == []


def test_non_invoice_table_has_no_business_rules_applied():
    assert validate_business_rules("customers", {"legal_name": None}) == []


def test_schema_accepts_valid_customer_record():
    values = {
        "natural_key": "C-1001",
        "legal_name": "Acme Corp",
        "email": "a@acme.com",
    }
    assert validate_schema("customers", values) == []


def test_schema_rejects_missing_required_column():
    values = {"legal_name": "Acme Corp"}  # natural_key required, absent entirely
    errors = validate_schema("customers", values)
    assert any(e.code == "SCHEMA_VALIDATION_FAILED" for e in errors)


def test_schema_rejects_wrong_python_type():
    values = {"natural_key": "C-1001", "legal_name": "Acme Corp", "employee_count": "not-a-number"}
    errors = validate_schema("customers", values)
    assert any(e.code == "SCHEMA_VALIDATION_FAILED" for e in errors)


def test_schema_accepts_a_real_integer_value():
    """Regression test: pandas auto-infers a concrete dtype (e.g. int64)
    from a single-value column, which used to mismatch this schema's
    declared `object` dtype for every column — a real int correctly
    transformed by app.transforms.to_int was getting rejected."""
    values = {"natural_key": "C-1001", "legal_name": "Acme Corp", "employee_count": 2171}
    assert validate_schema("customers", values) == []
