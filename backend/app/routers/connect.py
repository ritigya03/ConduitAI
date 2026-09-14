"""POST /connect/mock-crm -- pulls the full mock-CRM dataset (paginated,
retried) and lands it through the same app.ingest.ingest_rows path
POST /upload uses, so everything downstream (profile, mapping-spec,
load) treats an API-sourced batch identically to a file-sourced one.
"""

import hashlib
import json

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Response
from sqlmodel import Session

from app.db import get_session
from app.idempotency import IdempotencyConflict, run_idempotent
from app.ingest import ingest_rows
from app.mock_crm_client import MockCrmError, fetch_all_customers

router = APIRouter()


@router.post("/connect/mock-crm")
def connect_mock_crm(
    response: Response,
    tenant_id: str = Form(...),
    source_name: str = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
):
    try:
        rows = fetch_all_customers()
    except MockCrmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not rows:
        raise HTTPException(status_code=400, detail="Mock CRM returned no rows")

    content_hash = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    fingerprint = f"{content_hash}|{source_name}"

    def _do_connect() -> tuple[int, dict]:
        batch, is_idempotent = ingest_rows(
            session,
            tenant_id=tenant_id,
            source_name=source_name,
            source_kind="crm",
            rows=rows,
            content_hash=content_hash,
            filename="mock-crm-pull.json",
        )
        status_code = 200 if is_idempotent else 201
        return status_code, {"batch_id": str(batch.id), "row_count": batch.row_count, "idempotent": is_idempotent}

    try:
        status_code, body = run_idempotent(
            session,
            tenant_id=tenant_id,
            endpoint="/connect/mock-crm",
            key=idempotency_key,
            request_fingerprint=fingerprint,
            handler=_do_connect,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    response.status_code = status_code
    return body
