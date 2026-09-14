"""POST /mapping-spec — builds and saves a versioned mapping spec.

Reuses Day 2's profiling + scoring fresh (self-contained, like
POST /profile), then calls the Day 3 LLM tie-breaker only for columns
the deterministic scorer bucketed "unmapped".
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Header, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.idempotency import IdempotencyConflict, run_idempotent
from app.llm_mapper import LLMDecision, tiebreak
from app.mapping_spec import (
    MappingSpecNotDraft,
    MappingSpecNotFound,
    build_spec_entries,
    confirm_mapping_spec,
    save_mapping_spec,
    update_spec_json,
)
from app.models import OnboardingBatch, RawRecord, Source
from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, score_all_columns

router = APIRouter()


@router.post("/mapping-spec")
def create_mapping_spec(
    tenant_id: str = Form(...),
    batch_id: str = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
):
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="batch_id must be a valid UUID")

    batch = session.get(OnboardingBatch, batch_uuid)
    if batch is None or batch.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Batch not found")

    def _do_create() -> tuple[int, dict]:
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

        return 200, {
            "mapping_spec_id": str(spec.id),
            "version": spec.version,
            "parent_version": spec.parent_version,
            "status": spec.status,
            "spec_json": spec.spec_json,
        }

    # A retried POST /mapping-spec without an Idempotency-Key legitimately
    # creates a new draft version each time (that's the documented,
    # intentional behavior verified by test_mapping_spec_versions_
    # increment_on_repeat_calls) -- the Idempotency-Key is what turns a
    # *retry of the same request* into a replay instead of a new version.
    try:
        status_code, body = run_idempotent(
            session,
            tenant_id=tenant_id,
            endpoint="/mapping-spec",
            key=idempotency_key,
            request_fingerprint=str(batch.id),
            handler=_do_create,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return body


@router.patch("/mapping-spec/{mapping_spec_id}")
def update_mapping_spec_route(
    mapping_spec_id: str,
    tenant_id: str = Form(...),
    spec_json: str = Form(...),
    session: Session = Depends(get_session),
):
    """Day 5's review-grid override action: merges entries into a
    **draft** spec's spec_json in place (no new version — that's what
    `POST /mapping-spec` is for). `spec_json` is a JSON object string,
    `{canonical_field_name: entry_or_null}` — see `update_spec_json`."""
    try:
        spec_uuid = UUID(mapping_spec_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="mapping_spec_id must be a valid UUID")

    try:
        overrides = json.loads(spec_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="spec_json must be valid JSON")
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="spec_json must be a JSON object")

    try:
        spec = update_spec_json(session, tenant_id, spec_uuid, overrides)
    except MappingSpecNotFound:
        raise HTTPException(status_code=404, detail="Mapping spec not found")
    except MappingSpecNotDraft as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "mapping_spec_id": str(spec.id),
        "version": spec.version,
        "parent_version": spec.parent_version,
        "status": spec.status,
        "spec_json": spec.spec_json,
    }


@router.post("/mapping-spec/{mapping_spec_id}/confirm")
def confirm_mapping_spec_route(
    mapping_spec_id: str,
    tenant_id: str = Form(...),
    session: Session = Depends(get_session),
):
    """The human-review action: promotes a draft mapping spec to
    "confirmed" (superseding whatever was previously confirmed for that
    source). This is the only place in Day 1-3 a spec ever leaves
    "draft" — Day 4's transform engine should read the confirmed spec,
    not the latest draft."""
    try:
        spec_uuid = UUID(mapping_spec_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="mapping_spec_id must be a valid UUID")

    try:
        spec = confirm_mapping_spec(session, tenant_id, spec_uuid)
    except MappingSpecNotFound:
        raise HTTPException(status_code=404, detail="Mapping spec not found")
    except MappingSpecNotDraft as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "mapping_spec_id": str(spec.id),
        "version": spec.version,
        "status": spec.status,
    }
