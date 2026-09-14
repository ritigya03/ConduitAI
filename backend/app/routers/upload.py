import csv
import hashlib
import io

from charset_normalizer import from_bytes
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Response, UploadFile
from sqlmodel import Session

from app.db import get_session
from app.idempotency import IdempotencyConflict, run_idempotent
from app.ingest import ingest_rows

router = APIRouter()


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


@router.post("/upload")
def upload_file(
    file: UploadFile,
    response: Response,
    tenant_id: str = Form(...),
    source_name: str = Form(...),
    source_kind: str = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
):
    raw_bytes = file.file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Fingerprint for the Idempotency-Key check (Day 6) -- distinct from
    # `file_hash` below: this is "did this exact request get replayed",
    # not "have we already stored this exact file content."
    fingerprint = hashlib.sha256(raw_bytes).hexdigest() + f"|{source_name}|{source_kind}"

    def _do_upload() -> tuple[int, dict]:
        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        detection = from_bytes(raw_bytes).best()
        encoding = detection.encoding if detection else "utf-8"
        text = raw_bytes.decode(encoding, errors="replace")
        delimiter = _detect_delimiter(text[:2048])
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

        batch, is_idempotent = ingest_rows(
            session,
            tenant_id=tenant_id,
            source_name=source_name,
            source_kind=source_kind,
            rows=rows,
            content_hash=file_hash,
            filename=file.filename or "unknown",
            encoding=encoding,
            delimiter=delimiter,
        )
        status_code = 200 if is_idempotent else 201
        return status_code, {"batch_id": str(batch.id), "row_count": batch.row_count, "idempotent": is_idempotent}

    try:
        status_code, body = run_idempotent(
            session,
            tenant_id=tenant_id,
            endpoint="/upload",
            key=idempotency_key,
            request_fingerprint=fingerprint,
            handler=_do_upload,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    response.status_code = status_code
    return body
