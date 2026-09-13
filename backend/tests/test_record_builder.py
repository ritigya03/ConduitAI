from app.record_builder import apply_spec_to_row


def _entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def test_apply_spec_to_row_builds_customer_record():
    spec_json = {
        "customer_natural_key": _entry("customer_id", "trim"),
        "legal_name": _entry("company_name", "trim"),
        "customer_email": _entry("email", "trim"),
    }
    raw_row = {"customer_id": "C-1001", "company_name": "Acme Corp", "email": "a@acme.com"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert len(records) == 1
    record = records[0]
    assert record.target_table == "customers"
    assert record.values == {
        "natural_key": "C-1001",
        "legal_name": "Acme Corp",
        "email": "a@acme.com",
    }
    assert record.errors == []


def test_apply_spec_to_row_flags_missing_required_field():
    spec_json = {"customer_natural_key": _entry("customer_id", "trim")}
    raw_row = {"customer_id": "   "}  # trims to empty -> None

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].values == {}
    assert len(records[0].errors) == 1
    assert records[0].errors[0].field == "customer_natural_key"
    assert records[0].errors[0].code == "MISSING_REQUIRED"
    assert records[0].errors[0].raw_value == "   "


def test_apply_spec_to_row_collects_transform_errors():
    spec_json = {"invoice_issue_date": _entry("issue_date", "parse_date")}
    raw_row = {"issue_date": "not-a-date"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].target_table == "invoices"
    assert "issue_date" not in records[0].values
    assert records[0].errors[0].code == "TYPE_COERCION_FAILED"


def test_apply_spec_to_row_routes_linking_field_to_customer_natural_key():
    spec_json = {
        "invoice_customer_natural_key": _entry("customer_id", "trim"),
        "invoice_natural_key": _entry("invoice_number", "trim"),
    }
    raw_row = {"customer_id": "1001", "invoice_number": "INV-2000"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].customer_natural_key == "1001"
    assert "customer_id" not in records[0].values
    assert records[0].values == {"natural_key": "INV-2000"}


def test_apply_spec_to_row_groups_multiple_target_tables():
    spec_json = {
        "invoice_customer_natural_key": _entry("customer_id", "trim"),
        "invoice_natural_key": _entry("invoice_number", "trim"),
        "account_number": _entry("account_number", "trim"),
    }
    raw_row = {"customer_id": "1001", "invoice_number": "INV-2000", "account_number": "ACC-1001"}

    records = apply_spec_to_row(spec_json, raw_row)
    tables = {r.target_table for r in records}

    assert tables == {"invoices", "accounts"}
    accounts_record = next(r for r in records if r.target_table == "accounts")
    assert accounts_record.values == {"account_number": "ACC-1001"}
    assert accounts_record.customer_natural_key is None  # billing rows don't map this


def test_apply_spec_to_row_uses_map_enum_with_field_enum_values():
    spec_json = {"invoice_status": _entry("status", "map_enum")}
    raw_row = {"status": "OPEN"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].values == {"status": "open"}
