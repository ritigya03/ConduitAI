"""GET /metrics — plain tenant-scoped COUNT(*) aggregates for Day 5's
metrics dashboard. No new tables, no time-series — just point-in-time
counts over what Days 1-4 already persist.
"""

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.db import get_session
from app.models import (
    Account,
    Customer,
    Invoice,
    MappingSpec,
    OnboardingBatch,
    Quarantine,
    SupportTicket,
)

router = APIRouter()


def _count(session: Session, model, tenant_id: str, *extra_filters) -> int:
    query = select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    for condition in extra_filters:
        query = query.where(condition)
    return session.exec(query).one()


@router.get("/metrics")
def get_metrics(tenant_id: str = Query(...), session: Session = Depends(get_session)):
    return {
        "batches": _count(session, OnboardingBatch, tenant_id),
        "customers": _count(session, Customer, tenant_id),
        "invoices": _count(session, Invoice, tenant_id),
        "support_tickets": _count(session, SupportTicket, tenant_id),
        "accounts": _count(session, Account, tenant_id),
        "quarantine_open": _count(session, Quarantine, tenant_id, Quarantine.status == "open"),
        # "resubmitted" is the status a quarantine row gets when a
        # resolve/bulk-resolve action successfully re-runs it through
        # app.loader.process_row and it loads — that's this metric's
        # "fixed". (The model's own "fixed" status value is unused by
        # any code path today; nothing ever sets it.)
        "quarantine_fixed": _count(session, Quarantine, tenant_id, Quarantine.status == "resubmitted"),
        "mapping_specs_confirmed": _count(session, MappingSpec, tenant_id, MappingSpec.status == "confirmed"),
    }
