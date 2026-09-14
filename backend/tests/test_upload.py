from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import OnboardingBatch, RawRecord

client = TestClient(app)


def _upload_tiny_crm(tenant_id: str):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()
    return client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )


def test_upload_stores_raw_records_and_creates_batch(unique_tenant_id):
    response = _upload_tiny_crm(unique_tenant_id)
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 5
    assert body["idempotent"] is False

    with Session(engine) as session:
        batch = session.get(OnboardingBatch, UUID(body["batch_id"]))
        assert batch is not None
        assert batch.filename == "crm_tiny.csv"
        raw_rows = session.exec(
            select(RawRecord).where(RawRecord.batch_id == batch.id)
        ).all()
        assert len(raw_rows) == 5
        assert raw_rows[0].raw_json["customer_id"] == "C-1001"


def test_uploading_same_file_twice_is_idempotent(unique_tenant_id):
    first = _upload_tiny_crm(unique_tenant_id)
    second = _upload_tiny_crm(unique_tenant_id)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    assert first.json()["batch_id"] == second.json()["batch_id"]

    with Session(engine) as session:
        batches = session.exec(
            select(OnboardingBatch).where(OnboardingBatch.tenant_id == unique_tenant_id)
        ).all()
        assert len(batches) == 1


def test_uploading_empty_file_returns_400(unique_tenant_id):
    response = client.post(
        "/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 400


def test_upload_with_idempotency_key_replays_without_reprocessing(unique_tenant_id):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()

    first = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "replay-test-key"},
    )
    second = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "replay-test-key"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()

    with Session(engine) as session:
        batches = session.exec(
            select(OnboardingBatch).where(OnboardingBatch.tenant_id == unique_tenant_id)
        ).all()
        assert len(batches) == 1  # replay never re-ran _do_upload


def test_upload_same_idempotency_key_different_file_returns_409(unique_tenant_id):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        crm_content = f.read()
    with open("tests/fixtures/billing_tiny.csv", "rb") as f:
        billing_content = f.read()

    client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", crm_content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "conflict-test-key"},
    )
    conflict = client.post(
        "/upload",
        files={"file": ("billing_tiny.csv", billing_content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "conflict-test-key"},
    )
    assert conflict.status_code == 409
