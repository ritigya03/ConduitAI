from sqlalchemy import create_engine, inspect

from app.config import settings

EXPECTED_TABLES = {
    "sources", "onboarding_batches", "raw_records", "column_profiles",
    "mapping_specs", "mapping_reviews", "quarantine",
    "customers", "accounts", "invoices", "support_tickets",
    "alembic_version",
}


def test_alembic_upgrade_creates_all_canonical_tables():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))


def test_customers_unique_constraint_exists_in_db():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    col_sets = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("customers")
    }
    assert ("tenant_id", "natural_key") in col_sets


def test_onboarding_batches_unique_constraint_exists_in_db():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    col_sets = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("onboarding_batches")
    }
    assert ("tenant_id", "source_id", "file_hash") in col_sets
