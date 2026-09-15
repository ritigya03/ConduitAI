"""POST /profile — column profiling + deterministic mapping proposal.
GET /reports/{batch_id}/view — the ydata-profiling HTML report, generated
lazily on first request.

Orchestrates app.profiling and app.scoring against an already-uploaded
batch's raw_records. No LLM calls, no MappingSpec persisted here — see
app.scoring's module docstring for why. Report generation
(app.report.generate_report) deliberately does NOT run inline in
`/profile` — it pulls in ydata-profiling's full dependency chain
(pandas, matplotlib, scipy) on top of app.scoring's own embedding-model
load, and running both in the same request is exactly what was crashing
this service on a memory-constrained deploy (Day 7's Render investigation
— see docs/PROGRESS.md). Deferred to its own endpoint, called only when
a reviewer actually clicks through to the full report.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import ColumnProfile, OnboardingBatch, RawRecord, Source
from app.profiling import build_dataframe, profile_all_columns
from app.report import REPORTS_DIR, generate_report
from app.scoring import score_all_columns

router = APIRouter()


@router.post("/profile")
def profile_batch(
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

    mappings_by_column = {
        mapping.column_name: mapping
        for mapping in score_all_columns(stats_by_column, source_kind)
    }

    columns_response = []
    for stats in stats_list:
        mapping = mappings_by_column[stats.column_name]
        profile_json = {"stats": stats.model_dump(), "mapping": mapping.model_dump()}

        existing = session.exec(
            select(ColumnProfile).where(
                ColumnProfile.batch_id == batch.id,
                ColumnProfile.column_name == stats.column_name,
            )
        ).first()
        if existing is not None:
            existing.profile_json = profile_json
            session.add(existing)
        else:
            session.add(
                ColumnProfile(
                    batch_id=batch.id,
                    column_name=stats.column_name,
                    profile_json=profile_json,
                )
            )

        columns_response.append(
            {
                "column_name": stats.column_name,
                "stats": stats.model_dump(),
                "candidates": [c.model_dump() for c in mapping.candidates],
                "best_field": mapping.best_field,
                "bucket": mapping.bucket,
            }
        )

    session.commit()

    return {
        "batch_id": str(batch.id),
        "columns": columns_response,
    }


@router.get("/profile/{batch_id}/report")
def view_report(
    batch_id: str,
    tenant_id: str = Query(...),
    session: Session = Depends(get_session),
):
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="batch_id must be a valid UUID")

    batch = session.get(OnboardingBatch, batch_uuid)
    if batch is None or batch.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Batch not found")

    if not settings.enable_html_reports:
        raise HTTPException(
            status_code=503,
            detail="Full HTML report generation is disabled on this deployment "
            "(ydata-profiling needs more memory than this host provides). "
            "Run locally via Docker Compose for the full report.",
        )

    report_path = REPORTS_DIR / f"{batch_id}.html"
    if not report_path.exists():
        raw_rows = session.exec(select(RawRecord).where(RawRecord.batch_id == batch.id)).all()
        if not raw_rows:
            raise HTTPException(status_code=400, detail="Batch has no raw records")
        df = build_dataframe([row.raw_json for row in raw_rows])
        generate_report(df, str(batch.id))

    return RedirectResponse(url=f"/reports/{batch_id}.html")
