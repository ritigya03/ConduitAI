"""Tests for GET /metrics -- tenant-scoped aggregate counts."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.loader import load_batch
from app.main import app
from app.models import MappingSpec, OnboardingBatch, RawRecord, Source

client = TestClient(app)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def test_metrics_for_unknown_tenant_are_all_zero(unique_tenant_id):
    response = client.get("/metrics", params={"tenant_id": unique_tenant_id})

    assert response.status_code == 200
    assert response.json() == {
        "batches": 0,
        "customers": 0,
        "invoices": 0,
        "support_tickets": 0,
        "accounts": 0,
        "quarantine_open": 0,
        "quarantine_fixed": 0,
        "mapping_specs_confirmed": 0,
    }


def test_metrics_reflect_loads_and_quarantine(unique_tenant_id):
    with Session(engine) as session:
        source = Source(tenant_id=unique_tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        batch = OnboardingBatch(
            tenant_id=unique_tenant_id, source_id=source.id, file_hash="h1", filename="t.csv", row_count=0
        )
        session.add(batch)
        session.commit()
        session.refresh(batch)

        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        spec = MappingSpec(tenant_id=unique_tenant_id, source_id=source.id, version=1, spec_json=spec_json, status="confirmed")
        session.add(spec)
        session.commit()

        session.add(
            RawRecord(
                batch_id=batch.id, source_id=source.id, tenant_id=unique_tenant_id, row_index=0,
                raw_json={"customer_id": "C-1", "company_name": "Acme"}, content_hash="h",
            )
        )
        session.add(
            RawRecord(
                batch_id=batch.id, source_id=source.id, tenant_id=unique_tenant_id, row_index=1,
                raw_json={"customer_id": "", "company_name": "Beta"}, content_hash="h2",
            )
        )
        session.commit()

        load_batch(session, unique_tenant_id, batch)

    response = client.get("/metrics", params={"tenant_id": unique_tenant_id})

    assert response.status_code == 200
    body = response.json()
    assert body["batches"] == 1
    assert body["customers"] == 1
    assert body["quarantine_open"] == 1
    assert body["mapping_specs_confirmed"] == 1


def test_metrics_only_counts_the_given_tenant(unique_tenant_id):
    other_tenant = f"{unique_tenant_id}-other"
    with Session(engine) as session:
        source = Source(tenant_id=other_tenant, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)
        session.add(
            OnboardingBatch(tenant_id=other_tenant, source_id=source.id, file_hash="h", filename="t.csv", row_count=0)
        )
        session.commit()

    response = client.get("/metrics", params={"tenant_id": unique_tenant_id})

    assert response.status_code == 200
    assert response.json()["batches"] == 0
