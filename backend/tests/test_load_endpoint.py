from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import Customer

client = TestClient(app)


def _upload_tiny_crm(tenant_id: str) -> str:
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()
    response = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 201
    return response.json()["batch_id"]


def _create_and_confirm_spec(tenant_id: str, batch_id: str) -> None:
    create = client.post("/mapping-spec", data={"tenant_id": tenant_id, "batch_id": batch_id})
    spec_id = create.json()["mapping_spec_id"]
    confirm = client.post(f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": tenant_id})
    assert confirm.status_code == 200


def test_load_without_confirmed_spec_returns_409(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})
    assert response.status_code == 409


def test_load_loads_customers_after_spec_confirmed(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    _create_and_confirm_spec(unique_tenant_id, batch_id)

    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 5
    assert body["loaded"] + body["quarantined"] == 5

    with Session(engine) as session:
        customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        assert len(customers) == body["loaded"]


def test_load_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/load",
        data={"tenant_id": unique_tenant_id, "batch_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404


def test_load_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"})
    assert response.status_code == 400
