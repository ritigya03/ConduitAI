def test_all_expected_tables_registered():
    import app.models  # noqa: F401
    from sqlmodel import SQLModel

    table_names = set(SQLModel.metadata.tables.keys())
    expected = {
        "sources", "onboarding_batches", "raw_records", "column_profiles",
        "mapping_specs", "mapping_reviews", "quarantine",
        "customers", "accounts", "invoices", "support_tickets",
    }
    assert expected.issubset(table_names)


def _unique_constraint_column_sets(table_name: str) -> set[tuple[str, ...]]:
    import app.models  # noqa: F401
    from sqlalchemy import UniqueConstraint
    from sqlmodel import SQLModel

    table = SQLModel.metadata.tables[table_name]
    return {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_customers_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("customers")


def test_invoices_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("invoices")


def test_support_tickets_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("support_tickets")


def test_onboarding_batches_has_unique_tenant_source_filehash():
    assert ("tenant_id", "source_id", "file_hash") in _unique_constraint_column_sets(
        "onboarding_batches"
    )
