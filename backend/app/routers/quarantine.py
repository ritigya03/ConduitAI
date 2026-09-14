"""GET /quarantine, POST /quarantine/{id}/resolve, POST /quarantine/bulk-resolve
— Day 5's exception-queue triage endpoints.

"Fixed" resolutions reuse `app.loader.process_row` (the same transform +
validate + upsert building block `load_batch` uses per row) against the
quarantined row's raw values, optionally patched with a reviewer's
corrections, so a resolved row goes through exactly the same pipeline a
first-pass load would have.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.loader import get_confirmed_spec, process_row
from app.models import OnboardingBatch, Quarantine

router = APIRouter()

_RESOLVE_ACTIONS = {"fixed", "ignored"}


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a valid UUID")


def _serialize(row: Quarantine) -> dict:
    return {
        "id": str(row.id),
        "batch_id": str(row.batch_id),
        "raw_record_json": row.raw_record_json,
        "error_codes": row.error_codes,
        "severity": row.severity,
        "explanation": row.explanation,
        "suggested_fix": row.suggested_fix,
        "status": row.status,
    }


@router.get("/quarantine")
def list_quarantine(
    tenant_id: str = Query(...),
    batch_id: str | None = Query(default=None),
    status: str | None = Query(default="open"),
    session: Session = Depends(get_session),
):
    query = select(Quarantine).where(Quarantine.tenant_id == tenant_id)
    if batch_id is not None:
        query = query.where(Quarantine.batch_id == _parse_uuid(batch_id, "batch_id"))
    if status is not None:
        query = query.where(Quarantine.status == status)

    rows = session.exec(query).all()
    return {"items": [_serialize(row) for row in rows]}


class RowResultOutcome:
    """Tiny local record — not returned over the API — so `_apply_correction`
    can report both "did it load" and "did we even get to try" (false when
    there's no confirmed spec for the batch's source) to its callers."""

    def __init__(self, loaded: bool, attempted: bool):
        self.loaded = loaded
        self.attempted = attempted


def _apply_correction(row: Quarantine, corrected_values: dict, session: Session) -> RowResultOutcome:
    """Re-runs `process_row` against `row`'s raw values (patched with
    `corrected_values`), then updates `row` in place with the outcome —
    shared by the single-row and bulk-resolve endpoints below."""
    batch = session.get(OnboardingBatch, row.batch_id)
    spec = get_confirmed_spec(session, row.tenant_id, batch.source_id) if batch else None
    if spec is None:
        return RowResultOutcome(loaded=False, attempted=False)

    raw = {**row.raw_record_json, **corrected_values}
    result = process_row(session, row.tenant_id, spec, raw, row.batch_id)

    if result.loaded:
        row.status = "resubmitted"
    else:
        row.raw_record_json = raw
        row.error_codes = sorted({e["code"] for e in result.errors})
        row.explanation = "; ".join(f"{e['field']}: {e['code']}" for e in result.errors)
        row.suggested_fix = result.suggested_fix
        row.status = "open"
    session.add(row)
    return RowResultOutcome(loaded=result.loaded, attempted=True)


@router.post("/quarantine/{quarantine_id}/resolve")
def resolve_quarantine(
    quarantine_id: str,
    tenant_id: str = Form(...),
    action: str = Form(...),
    corrected_values: str | None = Form(default=None),
    session: Session = Depends(get_session),
):
    if action not in _RESOLVE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(_RESOLVE_ACTIONS)}")

    row = session.get(Quarantine, _parse_uuid(quarantine_id, "quarantine_id"))
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Quarantine row not found")

    if action == "ignored":
        row.status = "ignored"
        session.add(row)
        session.commit()
        return {"status": "ignored", "loaded": False}

    try:
        corrections = json.loads(corrected_values) if corrected_values else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="corrected_values must be valid JSON")

    outcome = _apply_correction(row, corrections, session)
    if not outcome.attempted:
        raise HTTPException(status_code=409, detail="No confirmed mapping spec for this batch's source")
    session.commit()

    return {"status": row.status, "loaded": outcome.loaded}


@router.post("/quarantine/bulk-resolve")
def bulk_resolve_quarantine(
    tenant_id: str = Form(...),
    error_code: str = Form(...),
    action: str = Form(...),
    corrected_values: str | None = Form(default=None),
    session: Session = Depends(get_session),
):
    if action not in _RESOLVE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(_RESOLVE_ACTIONS)}")

    try:
        corrections = json.loads(corrected_values) if corrected_values else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="corrected_values must be valid JSON")

    matching = session.exec(
        select(Quarantine).where(Quarantine.tenant_id == tenant_id, Quarantine.status == "open")
    ).all()
    matching = [row for row in matching if error_code in row.error_codes]

    loaded = 0
    for row in matching:
        if action == "ignored":
            row.status = "ignored"
            session.add(row)
            continue
        outcome = _apply_correction(row, corrections, session)
        if outcome.loaded:
            loaded += 1
    session.commit()

    still_open = sum(1 for row in matching if row.status == "open")
    return {"processed": len(matching), "loaded": loaded, "still_open": still_open}
