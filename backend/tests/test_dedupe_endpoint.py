from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.loader import load_batch
from app.main import app
from app.models import Customer, Invoice, MappingSpec, OnboardingBatch, RawRecord, Source

client = TestClient(app)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def _load_two_near_duplicate_customers(tenant_id: str) -> tuple[str, str, str]:
    """Returns (customer_id_1, customer_id_2, batch_id)."""
    with Session(engine) as session:
        source = Source(tenant_id=tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
            "customer_email": _spec_entry("email", "trim"),
        }
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
                raw_json={"customer_id": "C-1", "company_name": "Whitfield & Sons", "email": "contact@whitfield.example"},
                content_hash="h1",
            )
        )
        session.add(
            RawRecord(
                batch_id=batch.id, source_id=source.id, tenant_id=tenant_id, row_index=1,
                raw_json={"customer_id": "C-2", "company_name": "Whitfield and Sons Ltd", "email": "contact@whitfield.example"},
                content_hash="h2",
            )
        )
        session.commit()

        load_batch(session, tenant_id, batch)

        customers = session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all()
        assert len(customers) == 2
        return str(customers[0].id), str(customers[1].id), str(batch.id)


def test_dedupe_finds_the_near_duplicate_pair(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)

    response = client.post("/dedupe", data={"tenant_id": unique_tenant_id})

    assert response.status_code == 200
    assert response.json()["candidates_found"] >= 1
    assert response.json()["new"] >= 1

    listed = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"]
    assert len(listed) >= 1
    assert listed[0]["match_probability"] > 0.5


def test_dedupe_rerun_upserts_instead_of_duplicating(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)

    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})

    listed = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"]
    assert len(listed) == 1  # not 2 -- the second pass upserted the same pair


def test_resolve_merge_repoints_invoices_and_deletes_the_loser(unique_tenant_id):
    _, _, batch_id = _load_two_near_duplicate_customers(unique_tenant_id)
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    candidate = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"][0]

    loser_id = candidate["customer_id_b"]
    winner_id = candidate["customer_id_a"]

    with Session(engine) as session:
        session.add(
            Invoice(
                tenant_id=unique_tenant_id,
                natural_key="INV-1",
                customer_id=loser_id,
                invoice_number="INV-1",
                amount_minor_units=1000,
                currency="USD",
                issue_date="2024-01-01",
                content_hash="h",
                source_batch_id=batch_id,
            )
        )
        session.commit()

    response = client.post(
        f"/duplicates/{candidate['id']}/resolve", data={"tenant_id": unique_tenant_id, "action": "merge"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "merged"
    assert response.json()["winner_customer_id"] == winner_id

    with Session(engine) as session:
        assert session.get(Customer, loser_id) is None
        moved_invoice = session.exec(select(Invoice).where(Invoice.tenant_id == unique_tenant_id)).first()
        assert str(moved_invoice.customer_id) == winner_id


def test_resolve_dismiss_marks_status_without_deleting_anything(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    candidate = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"][0]

    response = client.post(
        f"/duplicates/{candidate['id']}/resolve", data={"tenant_id": unique_tenant_id, "action": "dismiss"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"

    with Session(engine) as session:
        assert session.get(Customer, candidate["customer_id_a"]) is not None
        assert session.get(Customer, candidate["customer_id_b"]) is not None

    open_items = client.get("/duplicates", params={"tenant_id": unique_tenant_id, "status": "open"}).json()["items"]
    assert all(item["id"] != candidate["id"] for item in open_items)


def test_resolve_unknown_candidate_returns_404(unique_tenant_id):
    response = client.post(
        "/duplicates/00000000-0000-0000-0000-000000000000/resolve",
        data={"tenant_id": unique_tenant_id, "action": "dismiss"},
    )
    assert response.status_code == 404


def test_resolve_invalid_action_returns_400(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    candidate = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"][0]

    response = client.post(
        f"/duplicates/{candidate['id']}/resolve", data={"tenant_id": unique_tenant_id, "action": "not-a-real-action"}
    )
    assert response.status_code == 400
