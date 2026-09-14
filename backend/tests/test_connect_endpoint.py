"""Integration tests for POST /connect/mock-crm -- these need a live
mock-crm service reachable at settings.mock_crm_base_url (see
mock-crm/app/main.py; run `cd mock-crm && uv run uvicorn app.main:app
--port 8100`), the same way tests/test_mdp_end_to_end.py needs a live
Postgres.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_connect_mock_crm_ingests_all_pages(unique_tenant_id):
    response = client.post("/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"})
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 25  # mock-crm/app/data.py's full dataset
    assert body["idempotent"] is False


def test_connect_mock_crm_second_pull_is_idempotent_on_content(unique_tenant_id):
    first = client.post("/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"})
    second = client.post("/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"})
    assert first.json()["batch_id"] == second.json()["batch_id"]
    assert second.json()["idempotent"] is True
