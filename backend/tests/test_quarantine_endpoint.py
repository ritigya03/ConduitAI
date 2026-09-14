"""Tests for GET /quarantine, POST /quarantine/{id}/resolve, and
POST /quarantine/bulk-resolve. Quarantine rows are seeded by calling
app.loader.load_batch directly against a hand-built spec (same pattern
as tests/test_loader.py) rather than going through the real scorer/LLM —
deterministic and fast, and this file's job is the new endpoints, not
mapping accuracy.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.loader import load_batch
from app.main import app
from app.models import Customer, MappingSpec, OnboardingBatch, Quarantine, Source

client = TestClient(app)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def _make_batch(session: Session, tenant_id: str, source_kind: str) -> OnboardingBatch:
    source = Source(tenant_id=tenant_id, name=source_kind, kind=source_kind)
    session.add(source)
    session.commit()
    session.refresh(source)

    batch = OnboardingBatch(
        tenant_id=tenant_id, source_id=source.id, file_hash=f"hash-{source_kind}", filename="test.csv", row_count=0
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def _add_raw_row(session: Session, batch: OnboardingBatch, tenant_id: str, index: int, raw_json: dict) -> None:
    from app.models import RawRecord

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
    spec = MappingSpec(tenant_id=tenant_id, source_id=source_id, version=1, spec_json=spec_json, status="confirmed")
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec


_CRM_SPEC = {
    "customer_natural_key": _spec_entry("customer_id", "trim"),
    "legal_name": _spec_entry("company_name", "trim"),
}

_BILLING_SPEC = {
    "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
    "invoice_natural_key": _spec_entry("invoice_number", "trim"),
    "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
    "invoice_currency": _spec_entry("currency", "trim"),
    "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
}


def _seed_crm_customer(session: Session, tenant_id: str) -> None:
    batch = _make_batch(session, tenant_id, "crm")
    _confirm_spec(session, tenant_id, batch.source_id, _CRM_SPEC)
    _add_raw_row(session, batch, tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
    load_batch(session, tenant_id, batch)


def _seed_missing_required_quarantine_row(tenant_id: str) -> tuple[str, str]:
    """Returns (batch_id, quarantine_row_id) for one MISSING_REQUIRED row."""
    with Session(engine) as session:
        batch = _make_batch(session, tenant_id, "crm2")
        _confirm_spec(session, tenant_id, batch.source_id, _CRM_SPEC)
        _add_raw_row(session, batch, tenant_id, 0, {"customer_id": "", "company_name": "Acme"})
        load_batch(session, tenant_id, batch)

        row = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).first()
        return str(batch.id), str(row.id)


def test_list_quarantine_returns_open_items_for_batch(unique_tenant_id):
    batch_id, _ = _seed_missing_required_quarantine_row(unique_tenant_id)

    response = client.get("/quarantine", params={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "open"
    assert "MISSING_REQUIRED" in items[0]["error_codes"]
    assert items[0]["raw_record_json"]["customer_id"] == ""


def test_resolve_fixed_with_corrected_values_loads_the_row(unique_tenant_id):
    _, quarantine_id = _seed_missing_required_quarantine_row(unique_tenant_id)

    response = client.post(
        f"/quarantine/{quarantine_id}/resolve",
        data={
            "tenant_id": unique_tenant_id,
            "action": "fixed",
            "corrected_values": '{"customer_id": "C-9001"}',
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["loaded"] is True
    assert body["status"] == "resubmitted"

    with Session(engine) as session:
        customer = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id, Customer.natural_key == "C-9001")
        ).first()
        assert customer is not None
        row = session.get(Quarantine, quarantine_id)
        assert row.status == "resubmitted"


def test_resolve_fixed_with_still_bad_values_stays_open(unique_tenant_id):
    _, quarantine_id = _seed_missing_required_quarantine_row(unique_tenant_id)

    response = client.post(
        f"/quarantine/{quarantine_id}/resolve",
        data={"tenant_id": unique_tenant_id, "action": "fixed"},  # no correction: still missing
    )

    assert response.status_code == 200
    body = response.json()
    assert body["loaded"] is False
    assert body["status"] == "open"


def test_resolve_ignored_sets_status(unique_tenant_id):
    _, quarantine_id = _seed_missing_required_quarantine_row(unique_tenant_id)

    response = client.post(
        f"/quarantine/{quarantine_id}/resolve",
        data={"tenant_id": unique_tenant_id, "action": "ignored"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "loaded": False}

    open_items = client.get(
        "/quarantine", params={"tenant_id": unique_tenant_id, "status": "open"}
    ).json()["items"]
    assert all(item["id"] != quarantine_id for item in open_items)


def test_resolve_unknown_quarantine_id_returns_404(unique_tenant_id):
    response = client.post(
        "/quarantine/00000000-0000-0000-0000-000000000000/resolve",
        data={"tenant_id": unique_tenant_id, "action": "ignored"},
    )
    assert response.status_code == 404


def test_resolve_invalid_action_returns_400(unique_tenant_id):
    _, quarantine_id = _seed_missing_required_quarantine_row(unique_tenant_id)

    response = client.post(
        f"/quarantine/{quarantine_id}/resolve",
        data={"tenant_id": unique_tenant_id, "action": "not-a-real-action"},
    )
    assert response.status_code == 400


def test_bulk_resolve_only_touches_matching_error_code(unique_tenant_id):
    with Session(engine) as session:
        _seed_crm_customer(session, unique_tenant_id)

        billing_batch = _make_batch(session, unique_tenant_id, "billing")
        _confirm_spec(session, unique_tenant_id, billing_batch.source_id, _BILLING_SPEC)
        # row A: ambiguous date (DD/MM vs MM/DD both <= 12)
        _add_raw_row(
            session,
            billing_batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "C-1001",
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "03/04/2026",
            },
        )
        # row B: unparseable amount -- a different error code entirely
        _add_raw_row(
            session,
            billing_batch,
            unique_tenant_id,
            1,
            {
                "customer_id": "C-1001",
                "invoice_number": "INV-2",
                "amount": "not-a-number",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )
        summary = load_batch(session, unique_tenant_id, billing_batch)
        assert summary.quarantined == 2

    response = client.post(
        "/quarantine/bulk-resolve",
        data={
            "tenant_id": unique_tenant_id,
            "error_code": "DATE_AMBIGUOUS",
            "action": "fixed",
            "corrected_values": '{"issue_date": "2026-04-03"}',
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 1
    assert body["loaded"] == 1
    assert body["still_open"] == 0

    open_items = client.get(
        "/quarantine", params={"tenant_id": unique_tenant_id, "status": "open"}
    ).json()["items"]
    assert len(open_items) == 1
    assert "TYPE_COERCION_FAILED" in open_items[0]["error_codes"]
    assert open_items[0]["raw_record_json"]["invoice_number"] == "INV-2"


def test_bulk_resolve_ignored_marks_every_matching_row(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm3")
        _confirm_spec(session, unique_tenant_id, batch.source_id, _CRM_SPEC)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "", "company_name": "Acme"})
        _add_raw_row(session, batch, unique_tenant_id, 1, {"customer_id": "", "company_name": "Beta"})
        summary = load_batch(session, unique_tenant_id, batch)
        assert summary.quarantined == 2

    response = client.post(
        "/quarantine/bulk-resolve",
        data={"tenant_id": unique_tenant_id, "error_code": "MISSING_REQUIRED", "action": "ignored"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 2
    assert body["loaded"] == 0
    assert body["still_open"] == 0
