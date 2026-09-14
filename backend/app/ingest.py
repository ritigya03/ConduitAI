"""Shared "land these rows as a new (or idempotently-replayed) batch"
logic -- used by both POST /upload (rows come from a parsed file) and
POST /connect/mock-crm (rows come from a paginated API pull). Keeping
this in one place means both ingestion paths get the same source
lookup-or-create, same file_hash-based batch dedup, and same raw_records
insert.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models import OnboardingBatch, RawRecord, Source


def _row_content_hash(row: dict) -> str:
    canonical = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest_rows(
    session: Session,
    *,
    tenant_id: str,
    source_name: str,
    source_kind: str,
    rows: list[dict],
    content_hash: str,
    filename: str,
    encoding: str | None = None,
    delimiter: str | None = None,
) -> tuple[OnboardingBatch, bool]:
    """Returns (batch, is_idempotent_replay). `is_idempotent_replay` is
    True when a batch with this exact (tenant, source, content_hash)
    already exists -- the existing batch is returned unchanged, no new
    raw records are written."""
    source = session.exec(
        select(Source).where(
            Source.tenant_id == tenant_id, Source.name == source_name, Source.kind == source_kind
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
            OnboardingBatch.file_hash == content_hash,
        )
    ).first()
    if existing_batch is not None:
        return existing_batch, True

    batch = OnboardingBatch(
        tenant_id=tenant_id,
        source_id=source.id,
        status="queued",
        file_hash=content_hash,
        filename=filename,
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

    return batch, False
