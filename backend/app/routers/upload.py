import csv
import hashlib
import io
import json
from datetime import datetime, timezone

from charset_normalizer import from_bytes
from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from app.db import get_session
from app.models import OnboardingBatch, RawRecord, Source

router = APIRouter()


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _row_content_hash(row: dict) -> str:
    canonical = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/upload")
def upload_file(
    file: UploadFile,
    response: Response,
    tenant_id: str = Form(...),
    source_name: str = Form(...),
    source_kind: str = Form(...),
    session: Session = Depends(get_session),
):
    raw_bytes = file.file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    source = session.exec(
        select(Source).where(
            Source.tenant_id == tenant_id,
            Source.name == source_name,
            Source.kind == source_kind,
        )
    ).first()
    if source is None:
        source = Source(tenant_id=tenant_id, name=source_name, kind=source_kind)
        session.add(source)
        session.commit()
        session.refresh(source)

    existing_batch = session.exec(
        select(OnboardingBatch).where(
            OnboardingBatch.tenant_id == tenant_id,
            OnboardingBatch.source_id == source.id,
            OnboardingBatch.file_hash == file_hash,
        )
    ).first()
    if existing_batch is not None:
        response.status_code = 200
        return {
            "batch_id": str(existing_batch.id),
            "row_count": existing_batch.row_count,
            "idempotent": True,
        }

    detection = from_bytes(raw_bytes).best()
    encoding = detection.encoding if detection else "utf-8"
    text = raw_bytes.decode(encoding, errors="replace")
    delimiter = _detect_delimiter(text[:2048])
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    batch = OnboardingBatch(
        tenant_id=tenant_id,
        source_id=source.id,
        status="queued",
        file_hash=file_hash,
        filename=file.filename or "unknown",
        encoding=encoding,
        delimiter=delimiter,
        row_count=len(rows),
        created_at=datetime.now(timezone.utc),
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)

    for index, row in enumerate(rows):
        session.add(
            RawRecord(
                batch_id=batch.id,
                source_id=source.id,
                tenant_id=tenant_id,
                row_index=index,
                raw_json=row,
                content_hash=_row_content_hash(row),
            )
        )
    session.commit()

    response.status_code = 201
    return {"batch_id": str(batch.id), "row_count": batch.row_count, "idempotent": False}
