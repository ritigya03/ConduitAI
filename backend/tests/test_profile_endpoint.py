from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import ColumnProfile
from app.report import REPORTS_DIR

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


def test_profile_returns_ranked_candidates_for_every_column(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    response = client.post(
        "/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == batch_id

    columns_by_name = {c["column_name"]: c for c in body["columns"]}
    assert set(columns_by_name) == {"customer_id", "first_name", "last_name", "email"}

    for column in body["columns"]:
        assert column["candidates"], "every column gets at least one ranked candidate"
        confidences = [c["confidence"] for c in column["candidates"]]
        assert confidences == sorted(confidences, reverse=True)

    # "email" only appears as an alias on customer_email — unambiguous.
    assert columns_by_name["email"]["best_field"] == "customer_email"

    # "customer_id" is a shared alias across customer_natural_key AND
    # account_customer_natural_key (both in the crm candidate pool) — the
    # deterministic scorer can legitimately land on either; what matters
    # is it's confident, not which one it picked.
    assert columns_by_name["customer_id"]["best_field"] in {
        "customer_natural_key",
        "account_customer_natural_key",
    }
    assert columns_by_name["customer_id"]["candidates"][0]["confidence"] >= 0.55

    report_path = Path(body["report_path"])
    assert report_path.exists()
    assert report_path.parent == REPORTS_DIR


def test_profile_persists_column_profiles(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    with Session(engine) as session:
        profiles = session.exec(
            select(ColumnProfile).where(ColumnProfile.batch_id == UUID(batch_id))
        ).all()
        assert len(profiles) == 4
        assert {p.column_name for p in profiles} == {
            "customer_id",
            "first_name",
            "last_name",
            "email",
        }
        for p in profiles:
            assert "stats" in p.profile_json
            assert "mapping" in p.profile_json


def test_profile_is_idempotent_on_column_profiles(unique_tenant_id):
    """Re-profiling the same batch updates existing rows instead of
    duplicating them."""
    batch_id = _upload_tiny_crm(unique_tenant_id)
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    with Session(engine) as session:
        profiles = session.exec(
            select(ColumnProfile).where(ColumnProfile.batch_id == UUID(batch_id))
        ).all()
        assert len(profiles) == 4


def test_profile_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/profile",
        data={
            "tenant_id": unique_tenant_id,
            "batch_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404


def test_profile_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post(
        "/profile", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"}
    )
    assert response.status_code == 400
