"""POST /load — transforms, validates, and idempotently loads a batch
using its source's confirmed mapping spec. See app.loader for the
actual work; this router only does request plumbing.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.loader import NoConfirmedMappingSpec, load_batch
from app.models import OnboardingBatch

router = APIRouter()


@router.post("/load")
def load_batch_route(
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

    try:
        summary = load_batch(session, tenant_id, batch)
    except NoConfirmedMappingSpec:
        raise HTTPException(status_code=409, detail="No confirmed mapping spec for this batch's source")

    return summary.model_dump()
