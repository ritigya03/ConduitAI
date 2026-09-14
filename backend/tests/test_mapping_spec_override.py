import json

from fastapi.testclient import TestClient

from app.db import engine
from app.main import app
from app.models import MappingSpec
from sqlmodel import Session

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


def _create_spec(tenant_id: str, batch_id: str) -> str:
    response = client.post("/mapping-spec", data={"tenant_id": tenant_id, "batch_id": batch_id})
    assert response.status_code == 200
    return response.json()["mapping_spec_id"]


def test_patch_mapping_spec_overrides_a_field(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    spec_id = _create_spec(unique_tenant_id, batch_id)

    override = {
        "display_name": {
            "source_column": "email",
            "transform": {"function": "trim", "params": {}},
            "provenance": {"method": "human", "model": None, "confidence": None, "reasoning": None},
        }
    }
    response = client.patch(
        f"/mapping-spec/{spec_id}",
        data={"tenant_id": unique_tenant_id, "spec_json": json.dumps(override)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mapping_spec_id"] == spec_id
    assert body["status"] == "draft"
    assert body["spec_json"]["display_name"]["source_column"] == "email"
    assert body["spec_json"]["display_name"]["provenance"]["method"] == "human"
    # unrelated existing entries survive the merge
    assert body["spec_json"]["customer_email"]["source_column"] == "email"

    with Session(engine) as session:
        spec = session.get(MappingSpec, spec_id)
        assert spec.spec_json["display_name"]["source_column"] == "email"


def test_patch_mapping_spec_fills_in_transform_and_provenance_when_omitted(unique_tenant_id):
    """The review grid's override control only knows a column name and a
    chosen field name -- it shouldn't need to know the field's canonical
    type to pick the right transform function."""
    batch_id = _upload_tiny_crm(unique_tenant_id)
    spec_id = _create_spec(unique_tenant_id, batch_id)

    response = client.patch(
        f"/mapping-spec/{spec_id}",
        data={
            "tenant_id": unique_tenant_id,
            "spec_json": json.dumps({"display_name": {"source_column": "email"}}),
        },
    )

    assert response.status_code == 200
    entry = response.json()["spec_json"]["display_name"]
    assert entry["source_column"] == "email"
    assert entry["transform"] == {"function": "trim", "params": {}}
    assert entry["provenance"]["method"] == "human"


def test_patch_mapping_spec_null_entry_removes_the_field(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    spec_id = _create_spec(unique_tenant_id, batch_id)

    response = client.patch(
        f"/mapping-spec/{spec_id}",
        data={"tenant_id": unique_tenant_id, "spec_json": json.dumps({"customer_email": None})},
    )

    assert response.status_code == 200
    assert "customer_email" not in response.json()["spec_json"]


def test_patch_confirmed_mapping_spec_returns_409(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    spec_id = _create_spec(unique_tenant_id, batch_id)
    confirm = client.post(f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": unique_tenant_id})
    assert confirm.status_code == 200

    response = client.patch(
        f"/mapping-spec/{spec_id}",
        data={"tenant_id": unique_tenant_id, "spec_json": json.dumps({"display_name": None})},
    )

    assert response.status_code == 409


def test_patch_unknown_mapping_spec_returns_404(unique_tenant_id):
    response = client.patch(
        "/mapping-spec/00000000-0000-0000-0000-000000000000",
        data={"tenant_id": unique_tenant_id, "spec_json": json.dumps({"display_name": None})},
    )
    assert response.status_code == 404


def test_patch_mapping_spec_invalid_spec_json_returns_400(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    spec_id = _create_spec(unique_tenant_id, batch_id)

    response = client.patch(
        f"/mapping-spec/{spec_id}",
        data={"tenant_id": unique_tenant_id, "spec_json": "not json"},
    )

    assert response.status_code == 400
