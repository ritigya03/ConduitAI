from sqlmodel import Session

from app.db import engine
from app.llm_mapper import LLMDecision
from app.mapping_spec import build_spec_entries, next_version, save_mapping_spec
from app.models import MappingSpec, Source
from app.scoring import ColumnMapping, MappingCandidate


def _candidate(field: str, confidence: float) -> MappingCandidate:
    return MappingCandidate(
        canonical_field=field,
        confidence=confidence,
        name_score=confidence,
        embedding_score=confidence,
        type_score=confidence,
    )


def test_build_spec_entries_includes_confident_deterministic_columns():
    mapping = ColumnMapping(
        column_name="email",
        candidates=[_candidate("customer_email", 0.95)],
        best_field="customer_email",
        bucket="auto_accept",
    )
    entries = build_spec_entries([mapping], llm_decisions={})

    assert "customer_email" in entries
    assert entries["customer_email"]["source_column"] == "email"
    assert entries["customer_email"]["provenance"]["method"] == "deterministic"
    assert entries["customer_email"]["transform"]["function"] == "trim"


def test_build_spec_entries_excludes_unmapped_column_with_no_llm_decision():
    mapping = ColumnMapping(
        column_name="notes", candidates=[], best_field=None, bucket="unmapped"
    )
    entries = build_spec_entries([mapping], llm_decisions={})
    assert entries == {}


def test_build_spec_entries_uses_llm_decision_for_unmapped_column():
    mapping = ColumnMapping(
        column_name="amt_usd",
        candidates=[_candidate("invoice_tax_amount_minor_units", 0.4)],
        best_field="invoice_tax_amount_minor_units",
        bucket="unmapped",
    )
    llm_decision = LLMDecision(
        canonical_field="invoice_amount_minor_units",
        confidence=0.85,
        reasoning="matches the primary charge",
        model="groq/openai/gpt-oss-120b",
    )
    entries = build_spec_entries([mapping], llm_decisions={"amt_usd": llm_decision})

    assert "invoice_amount_minor_units" in entries
    assert entries["invoice_amount_minor_units"]["source_column"] == "amt_usd"
    assert entries["invoice_amount_minor_units"]["provenance"]["method"] == "llm"
    assert (
        entries["invoice_amount_minor_units"]["provenance"]["model"]
        == "groq/openai/gpt-oss-120b"
    )


def test_build_spec_entries_skips_column_when_llm_says_unknown():
    mapping = ColumnMapping(
        column_name="notes", candidates=[], best_field=None, bucket="unmapped"
    )
    llm_decision = LLMDecision(
        canonical_field="UNKNOWN", confidence=0.2, reasoning="no match", model="groq/x"
    )
    entries = build_spec_entries([mapping], llm_decisions={"notes": llm_decision})
    assert entries == {}


def test_build_spec_entries_uses_deterministic_fallback_when_llm_unavailable():
    mapping = ColumnMapping(
        column_name="weird_col",
        candidates=[_candidate("customer_region", 0.3)],
        best_field="customer_region",
        bucket="unmapped",
    )
    entries = build_spec_entries([mapping], llm_decisions={})
    assert entries["customer_region"]["provenance"]["method"] == "deterministic_fallback"


def test_next_version_starts_at_one_and_increments(unique_tenant_id):
    with Session(engine) as session:
        source = Source(tenant_id=unique_tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        version, parent = next_version(session, unique_tenant_id, source.id)
        assert version == 1
        assert parent is None

        save_mapping_spec(session, unique_tenant_id, source.id, {"customer_email": {}})

        version, parent = next_version(session, unique_tenant_id, source.id)
        assert version == 2
        assert parent == 1


def test_save_mapping_spec_persists_draft_row(unique_tenant_id):
    with Session(engine) as session:
        source = Source(tenant_id=unique_tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        spec = save_mapping_spec(
            session,
            unique_tenant_id,
            source.id,
            {"customer_email": {"source_column": "email"}},
        )

        assert spec.version == 1
        assert spec.status == "draft"
        assert spec.parent_version is None
        assert spec.spec_json == {"customer_email": {"source_column": "email"}}

        fetched = session.get(MappingSpec, spec.id)
        assert fetched is not None
