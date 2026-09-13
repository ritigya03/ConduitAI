"""POST /mapping-spec — builds and saves a versioned mapping spec.

Reuses Day 2's profiling + scoring fresh (self-contained, like
POST /profile), then calls the Day 3 LLM tie-breaker only for columns
the deterministic scorer bucketed "unmapped".
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.llm_mapper import LLMDecision, tiebreak
from app.mapping_spec import build_spec_entries, save_mapping_spec
from app.models import OnboardingBatch, RawRecord, Source
from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, score_all_columns

router = APIRouter()


@router.post("/mapping-spec")
def create_mapping_spec(
    tenant_id: str = Form(...),
    batch_id: str = Form(...),
    session: Session = Depends(get_session),
):
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="batch_id must be a valid UUID")

    batch = session.get(OnboardingBatch, batch_uuid)
    if batch is None or batch.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Batch not found")

    raw_rows = session.exec(
        select(RawRecord).where(RawRecord.batch_id == batch.id)
    ).all()
    if not raw_rows:
        raise HTTPException(status_code=400, detail="Batch has no raw records")

    source = session.get(Source, batch.source_id)
    source_kind = source.kind if source else None

    df = build_dataframe([row.raw_json for row in raw_rows])
    stats_list = profile_all_columns(df)
    stats_by_column = {stats.column_name: stats for stats in stats_list}
    mappings = score_all_columns(stats_by_column, source_kind)

    pool = candidate_pool(source_kind)
    llm_decisions: dict[str, LLMDecision] = {}
    for mapping in mappings:
        if mapping.bucket != "unmapped":
            continue
        decision = tiebreak(mapping.column_name, stats_by_column[mapping.column_name], pool)
        if decision is not None:
            llm_decisions[mapping.column_name] = decision

    spec_entries = build_spec_entries(mappings, llm_decisions)
    spec = save_mapping_spec(session, tenant_id, batch.source_id, spec_entries)

    return {
        "mapping_spec_id": str(spec.id),
        "version": spec.version,
        "parent_version": spec.parent_version,
        "status": spec.status,
        "spec_json": spec.spec_json,
    }
