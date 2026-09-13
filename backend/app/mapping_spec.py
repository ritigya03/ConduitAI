"""Mapping-spec builder + versioning.

Turns Day 2's per-column scoring output (app.scoring.ColumnMapping) plus
optional Day 3 LLM tie-break decisions (app.llm_mapper.LLMDecision) into
a versioned mapping-spec JSON document and persists it as a new
mapping_specs row. Never edits an existing spec row in place — each
call creates version N+1. Pure, DB-free functions (build_spec_entries)
are kept separate from the two that touch a Session, so the assembly
logic is testable without a database.
"""

from uuid import UUID

from sqlmodel import Session, select

from app.canonical import FieldType, get_field
from app.llm_mapper import LLMDecision
from app.models import MappingSpec
from app.scoring import ColumnMapping

class MappingSpecNotFound(Exception):
    """No mapping spec with that id belongs to that tenant."""


class MappingSpecNotDraft(Exception):
    """Only a "draft" spec can be confirmed — it's already confirmed or superseded."""


_TRANSFORM_BY_FIELD_TYPE: dict[FieldType, dict] = {
    FieldType.STRING: {"function": "trim", "params": {}},
    FieldType.EMAIL: {"function": "trim", "params": {}},
    FieldType.PHONE: {"function": "trim", "params": {}},
    FieldType.DATE: {"function": "parse_date", "params": {}},
    FieldType.DATETIME: {"function": "parse_date", "params": {}},
    FieldType.ENUM: {"function": "map_enum", "params": {}},
    FieldType.INTEGER: {"function": "to_int", "params": {}},
    FieldType.MONEY_MINOR_UNITS: {"function": "to_minor_units", "params": {}},
    FieldType.CURRENCY_CODE: {"function": "trim", "params": {}},
}


def build_spec_entries(
    mappings: list[ColumnMapping], llm_decisions: dict[str, LLMDecision]
) -> dict[str, dict]:
    """Returns {canonical_field_name: {source_column, transform, provenance}}.

    A column is included only if it has a real decision: a confident
    deterministic bucket, an LLM decision that isn't "UNKNOWN", or (when
    the LLM was unavailable for an unmapped column) the deterministic
    best guess, tagged "deterministic_fallback". A column with none of
    these is left out of the spec entirely.
    """
    entries: dict[str, dict] = {}
    for mapping in mappings:
        llm_decision = llm_decisions.get(mapping.column_name)

        if llm_decision is not None:
            if llm_decision.canonical_field == "UNKNOWN":
                continue
            field_name = llm_decision.canonical_field
            provenance = {
                "method": "llm",
                "model": llm_decision.model,
                "confidence": round(llm_decision.confidence, 4),
                "reasoning": llm_decision.reasoning,
            }
        elif mapping.best_field is not None and mapping.candidates:
            field_name = mapping.best_field
            method = "deterministic" if mapping.bucket != "unmapped" else "deterministic_fallback"
            provenance = {
                "method": method,
                "model": None,
                "confidence": mapping.candidates[0].confidence,
                "reasoning": None,
            }
        else:
            continue

        field = get_field(field_name)
        entries[field_name] = {
            "source_column": mapping.column_name,
            "transform": _TRANSFORM_BY_FIELD_TYPE[field.type],
            "provenance": provenance,
        }
    return entries


def next_version(
    session: Session, tenant_id: str, source_id: UUID
) -> tuple[int, int | None]:
    latest = session.exec(
        select(MappingSpec)
        .where(MappingSpec.tenant_id == tenant_id, MappingSpec.source_id == source_id)
        .order_by(MappingSpec.version.desc())
    ).first()
    if latest is None:
        return 1, None
    return latest.version + 1, latest.version


def save_mapping_spec(
    session: Session, tenant_id: str, source_id: UUID, spec_entries: dict[str, dict]
) -> MappingSpec:
    version, parent_version = next_version(session, tenant_id, source_id)
    spec = MappingSpec(
        tenant_id=tenant_id,
        source_id=source_id,
        version=version,
        spec_json=spec_entries,
        status="draft",
        parent_version=parent_version,
    )
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec


def confirm_mapping_spec(
    session: Session, tenant_id: str, mapping_spec_id: UUID
) -> MappingSpec:
    """Promotes a "draft" spec to "confirmed" — the human-review action
    Day 5's UI performs. Any other spec already "confirmed" for the same
    (tenant, source) is marked "superseded" first, so at most one
    confirmed version exists per source at a time."""
    spec = session.get(MappingSpec, mapping_spec_id)
    if spec is None or spec.tenant_id != tenant_id:
        raise MappingSpecNotFound(f"No mapping spec {mapping_spec_id} for tenant {tenant_id}")
    if spec.status != "draft":
        raise MappingSpecNotDraft(f"Mapping spec {mapping_spec_id} is {spec.status!r}, not draft")

    previously_confirmed = session.exec(
        select(MappingSpec).where(
            MappingSpec.tenant_id == tenant_id,
            MappingSpec.source_id == spec.source_id,
            MappingSpec.status == "confirmed",
        )
    ).all()
    for other in previously_confirmed:
        other.status = "superseded"
        session.add(other)

    spec.status = "confirmed"
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec
