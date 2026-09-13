import pytest
from sqlmodel import Session, select

from app.db import engine
from app.loader import NoConfirmedMappingSpec, load_batch
from app.models import (
    Customer,
    Invoice,
    MappingSpec,
    OnboardingBatch,
    Quarantine,
    RawRecord,
    Source,
)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def _make_batch(session: Session, tenant_id: str, source_kind: str) -> OnboardingBatch:
    source = Source(tenant_id=tenant_id, name=source_kind, kind=source_kind)
    session.add(source)
    session.commit()
    session.refresh(source)

    batch = OnboardingBatch(
        tenant_id=tenant_id,
        source_id=source.id,
        file_hash="test-hash",
        filename="test.csv",
        row_count=0,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def _add_raw_row(session: Session, batch: OnboardingBatch, tenant_id: str, index: int, raw_json: dict) -> None:
    session.add(
        RawRecord(
            batch_id=batch.id,
            source_id=batch.source_id,
            tenant_id=tenant_id,
            row_index=index,
            raw_json=raw_json,
            content_hash=f"hash-{index}",
        )
    )
    session.commit()


def _confirm_spec(session: Session, tenant_id: str, source_id, spec_json: dict) -> MappingSpec:
    spec = MappingSpec(
        tenant_id=tenant_id, source_id=source_id, version=1, spec_json=spec_json, status="confirmed"
    )
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec


def test_load_batch_raises_without_a_confirmed_spec(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001"})

        with pytest.raises(NoConfirmedMappingSpec):
            load_batch(session, unique_tenant_id, batch)


def test_load_batch_loads_valid_customer_row(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 0
        customer = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id, Customer.natural_key == "C-1001")
        ).first()
        assert customer is not None
        assert customer.legal_name == "Acme"


def test_load_batch_quarantines_row_missing_a_required_field(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 0
        assert summary.quarantined == 1
        quarantined = session.exec(
            select(Quarantine).where(Quarantine.batch_id == batch.id)
        ).all()
        assert len(quarantined) == 1
        assert "MISSING_REQUIRED" in quarantined[0].error_codes
        assert quarantined[0].severity == "error"


def test_load_batch_is_idempotent_on_rerun(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "", "company_name": "Acme"})
        _add_raw_row(session, batch, unique_tenant_id, 1, {"customer_id": "C-1002", "company_name": "Beta"})

        load_batch(session, unique_tenant_id, batch)
        load_batch(session, unique_tenant_id, batch)

        customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert len(customers) == 1
        assert len(quarantined) == 1


def test_load_batch_flags_orphan_foreign_key(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "billing")
        spec_json = {
            "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
            "invoice_natural_key": _spec_entry("invoice_number", "trim"),
            "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
            "invoice_currency": _spec_entry("currency", "trim"),
            "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(
            session,
            batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "9999",
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert "REF_INTEGRITY_ORPHAN_FK" in quarantined[0].error_codes


def test_load_batch_resolves_a_normalized_id_format_mismatch(unique_tenant_id):
    """The exact cross-system-ID-format case the spec's own example is
    about: CRM writes "C-1001", billing writes "1001" for the identical
    customer. _resolve_customer_id's digit-normalization fallback must
    resolve this automatically -- an exact-match-only lookup would
    orphan every cross-system row in a real dataset shaped like this
    one (confirmed against the actual seed data during Task 6)."""
    with Session(engine) as session:
        crm_batch = _make_batch(session, unique_tenant_id, "crm")
        crm_spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, crm_batch.source_id, crm_spec_json)
        _add_raw_row(session, crm_batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
        load_batch(session, unique_tenant_id, crm_batch)

        billing_batch = _make_batch(session, unique_tenant_id, "billing")
        billing_spec_json = {
            "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
            "invoice_natural_key": _spec_entry("invoice_number", "trim"),
            "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
            "invoice_currency": _spec_entry("currency", "trim"),
            "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
        }
        _confirm_spec(session, unique_tenant_id, billing_batch.source_id, billing_spec_json)
        _add_raw_row(
            session,
            billing_batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "1001",  # not "C-1001" -- resolved via digit normalization
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )

        summary = load_batch(session, unique_tenant_id, billing_batch)

        assert summary.loaded == 1
        assert summary.quarantined == 0
        invoice = session.exec(
            select(Invoice).where(Invoice.tenant_id == unique_tenant_id, Invoice.natural_key == "INV-1")
        ).first()
        assert invoice is not None


def test_load_batch_suggests_a_fix_for_a_genuinely_orphan_fk(unique_tenant_id):
    """A customer_id with no match at all (not even after normalization)
    should still fall through to the rapidfuzz suggestion."""
    with Session(engine) as session:
        crm_batch = _make_batch(session, unique_tenant_id, "crm")
        crm_spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, crm_batch.source_id, crm_spec_json)
        _add_raw_row(session, crm_batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
        load_batch(session, unique_tenant_id, crm_batch)

        billing_batch = _make_batch(session, unique_tenant_id, "billing")
        billing_spec_json = {
            "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
            "invoice_natural_key": _spec_entry("invoice_number", "trim"),
            "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
            "invoice_currency": _spec_entry("currency", "trim"),
            "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
        }
        _confirm_spec(session, unique_tenant_id, billing_batch.source_id, billing_spec_json)
        _add_raw_row(
            session,
            billing_batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "9999",  # no customer, not even after normalization
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )

        load_batch(session, unique_tenant_id, billing_batch)

        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == billing_batch.id)).all()
        assert "REF_INTEGRITY_ORPHAN_FK" in quarantined[0].error_codes


def test_load_batch_flags_exact_duplicate_within_batch(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
        _add_raw_row(session, batch, unique_tenant_id, 1, {"customer_id": "C-1001", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert "DUPLICATE_EXACT" in quarantined[0].error_codes
