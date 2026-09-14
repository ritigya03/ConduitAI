"""Rehearses the Day 6 'add a required field mid-project' demo: an old
confirmed mapping spec (created before kyc_status existed) keeps working
unmodified, while a *new* spec version that maps kyc_status enforces it
as required -- with zero special-case code, because
app.record_builder.apply_spec_to_row only ever processes fields present
in a given spec_json, and app.validation.validate_schema (after Day 6's
fix, see test_validation.py) only checks columns present in `values`.
"""

from sqlmodel import Session, select

from app.db import engine
from app.loader import load_batch
from app.models import Customer, MappingSpec, OnboardingBatch, Quarantine, RawRecord, Source


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


_OLD_SPEC = {
    "customer_natural_key": _spec_entry("customer_id", "trim"),
    "legal_name": _spec_entry("company_name", "trim"),
}
_NEW_SPEC_WITH_KYC = {
    **_OLD_SPEC,
    "kyc_status": _spec_entry("kyc", "map_enum"),
}


def _make_batch(session: Session, tenant_id: str, spec_json: dict, raw_json: dict) -> OnboardingBatch:
    source = Source(tenant_id=tenant_id, name="crm", kind="crm")
    session.add(source)
    session.commit()
    session.refresh(source)

    spec = MappingSpec(tenant_id=tenant_id, source_id=source.id, version=1, spec_json=spec_json, status="confirmed")
    session.add(spec)
    session.commit()

    batch = OnboardingBatch(tenant_id=tenant_id, source_id=source.id, file_hash="h", filename="t.csv", row_count=0)
    session.add(batch)
    session.commit()
    session.refresh(batch)

    session.add(
        RawRecord(
            batch_id=batch.id, source_id=source.id, tenant_id=tenant_id, row_index=0,
            raw_json=raw_json, content_hash="h",
        )
    )
    session.commit()
    return batch


def test_old_spec_without_kyc_status_still_loads_the_customer_with_the_default(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, _OLD_SPEC, {"customer_id": "C-1", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 0  # not retroactively broken by the new required field
        customer = session.exec(select(Customer).where(Customer.tenant_id == unique_tenant_id)).first()
        assert customer.kyc_status == "pending"  # the server_default, since the old spec never set it


def test_new_spec_with_kyc_status_mapped_enforces_it_as_required(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(
            session, unique_tenant_id, _NEW_SPEC_WITH_KYC,
            {"customer_id": "C-2", "company_name": "Beta", "kyc": ""},  # blank -- missing the now-required field
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 0
        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).first()
        assert "MISSING_REQUIRED" in quarantined.error_codes


def test_new_spec_with_kyc_status_provided_loads_successfully(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(
            session, unique_tenant_id, _NEW_SPEC_WITH_KYC,
            {"customer_id": "C-3", "company_name": "Gamma", "kyc": "verified"},
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        customer = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id, Customer.natural_key == "C-3")
        ).first()
        assert customer.kyc_status == "verified"
