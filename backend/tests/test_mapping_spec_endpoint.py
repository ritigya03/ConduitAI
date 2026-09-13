from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import MappingSpec

client = TestClient(app)


def _upload(tenant_id: str, fixture_path: str, filename: str) -> str:
    with open(fixture_path, "rb") as f:
        content = f.read()
    response = client.post(
        "/upload",
        files={"file": (filename, content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 201
    return response.json()["batch_id"]


def _upload_tiny_crm(tenant_id: str) -> str:
    return _upload(tenant_id, "tests/fixtures/crm_tiny.csv", "crm_tiny.csv")


def test_mapping_spec_maps_unambiguous_columns_deterministically(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["parent_version"] is None
    assert body["status"] == "draft"

    spec_json = body["spec_json"]
    assert spec_json["customer_email"]["source_column"] == "email"
    assert spec_json["customer_email"]["provenance"]["method"] == "deterministic"

    # No entry should ever claim more confidence than the deterministic
    # scorer's own auto_accept ceiling of 1.0, and a "draft" spec's
    # tentative (human_confirm-bucket) entries are expected and fine —
    # they're exactly what Day 5's human review UI exists to confirm.
    for entry in spec_json.values():
        assert 0.0 <= entry["provenance"]["confidence"] <= 1.0


def test_mapping_spec_llm_tiebreak_excludes_genuinely_unmappable_column(unique_tenant_id):
    """The one real, end-to-end test of the Groq -> Ollama chain: a column
    with no plausible canonical match, name- or meaning-wise, must land in
    Day 2's "unmapped" bucket and then get a real LLM call.

    This deliberately does NOT assert on the LLM's specific answer — real
    LLM output varies call to call (confirmed: this exact case flipped
    between runs during development). What must hold regardless of the
    LLM's judgment is the *mechanism*: an unmapped-bucket column's final
    provenance is never plain "deterministic" (that would mean the LLM
    path was skipped entirely) — it's either "llm" (real answer,
    including a correct "UNKNOWN" that excludes it) or
    "deterministic_fallback" (every provider failed). The exact-match
    "UNKNOWN" case is already covered deterministically, with no network
    dependency, by test_mapping_spec.py's
    test_build_spec_entries_skips_column_when_llm_says_unknown."""
    batch_id = _upload(
        unique_tenant_id,
        "tests/fixtures/crm_with_unmappable_column.csv",
        "crm_with_unmappable_column.csv",
    )

    response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert response.status_code == 200
    spec_json = response.json()["spec_json"]

    for entry in spec_json.values():
        if entry["source_column"] == "zzz_widget_code":
            assert entry["provenance"]["method"] in {"llm", "deterministic_fallback"}

    # the two genuinely-mappable columns in this fixture must still land
    assert spec_json["customer_email"]["source_column"] == "email"


def test_mapping_spec_versions_increment_on_repeat_calls(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    first = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )
    second = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert first.json()["version"] == 1
    assert second.json()["version"] == 2
    assert second.json()["parent_version"] == 1

    with Session(engine) as session:
        specs = session.exec(
            select(MappingSpec).where(MappingSpec.tenant_id == unique_tenant_id)
        ).all()
        assert len(specs) == 2


def test_mapping_spec_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/mapping-spec",
        data={
            "tenant_id": unique_tenant_id,
            "batch_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404


def test_mapping_spec_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"}
    )
    assert response.status_code == 400


def test_confirm_mapping_spec_marks_it_confirmed(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    create_response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )
    spec_id = create_response.json()["mapping_spec_id"]

    response = client.post(
        f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": unique_tenant_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_confirm_mapping_spec_twice_returns_409(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    create_response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )
    spec_id = create_response.json()["mapping_spec_id"]
    client.post(f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": unique_tenant_id})

    response = client.post(
        f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": unique_tenant_id}
    )
    assert response.status_code == 409


def test_confirm_unknown_mapping_spec_returns_404(unique_tenant_id):
    response = client.post(
        "/mapping-spec/00000000-0000-0000-0000-000000000000/confirm",
        data={"tenant_id": unique_tenant_id},
    )
    assert response.status_code == 404
