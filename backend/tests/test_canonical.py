import pytest

from app.canonical import CANONICAL_FIELDS, fields_for_table, get_field


def test_no_duplicate_field_names():
    names = [f.name for f in CANONICAL_FIELDS]
    assert len(names) == len(set(names))


def test_every_field_has_description_and_at_least_one_alias():
    for field in CANONICAL_FIELDS:
        assert field.description.strip() != ""
        assert len(field.aliases) >= 1


def test_get_field_returns_expected_field():
    field = get_field("customer_email")
    assert field.target_table == "customers"
    assert field.target_column == "email"
    assert "email" in field.aliases


def test_get_field_raises_for_unknown_name():
    with pytest.raises(KeyError):
        get_field("not_a_real_field")


def test_fields_for_table_filters_correctly():
    invoice_fields = fields_for_table("invoices")
    assert len(invoice_fields) > 0
    assert all(f.target_table == "invoices" for f in invoice_fields)


def test_registry_covers_all_four_canonical_tables():
    tables = {f.target_table for f in CANONICAL_FIELDS}
    assert tables == {"customers", "accounts", "invoices", "support_tickets"}
