"""POST /dedupe runs a fuzzy-match pass over a tenant's customers and
upserts DuplicateCandidate rows; GET /duplicates lists them;
POST /duplicates/{id}/resolve actions one (merge repoints every
invoice/support_ticket/account FK from the loser to the winner and
deletes the loser; dismiss just marks the pair reviewed-and-not-a-match).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.dedupe import find_duplicate_candidates
from app.models import Account, Customer, CustomerDuplicateCandidate, Invoice, SupportTicket

router = APIRouter()

_LINKED_MODELS = [Invoice, SupportTicket, Account]


@router.post("/dedupe")
def run_dedupe(tenant_id: str = Form(...), session: Session = Depends(get_session)):
    customers = session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all()
    candidates = find_duplicate_candidates(customers)

    created = 0
    for candidate in candidates:
        id_a, id_b = sorted([candidate.customer_id_a, candidate.customer_id_b])
        existing = session.exec(
            select(CustomerDuplicateCandidate).where(
                CustomerDuplicateCandidate.tenant_id == tenant_id,
                CustomerDuplicateCandidate.customer_id_a == UUID(id_a),
                CustomerDuplicateCandidate.customer_id_b == UUID(id_b),
            )
        ).first()
        if existing is not None:
            existing.match_probability = candidate.match_probability
            session.add(existing)
            continue
        session.add(
            CustomerDuplicateCandidate(
                tenant_id=tenant_id,
                customer_id_a=UUID(id_a),
                customer_id_b=UUID(id_b),
                match_probability=candidate.match_probability,
                status="open",
            )
        )
        created += 1
    session.commit()
    return {"candidates_found": len(candidates), "new": created}


@router.get("/duplicates")
def list_duplicates(
    tenant_id: str = Query(...), status: str | None = Query(default="open"), session: Session = Depends(get_session)
):
    query = select(CustomerDuplicateCandidate).where(CustomerDuplicateCandidate.tenant_id == tenant_id)
    if status is not None:
        query = query.where(CustomerDuplicateCandidate.status == status)
    rows = session.exec(query).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "customer_id_a": str(r.customer_id_a),
                "customer_id_b": str(r.customer_id_b),
                "match_probability": r.match_probability,
                "status": r.status,
            }
            for r in rows
        ]
    }


@router.post("/duplicates/{candidate_id}/resolve")
def resolve_duplicate(
    candidate_id: str, tenant_id: str = Form(...), action: str = Form(...), session: Session = Depends(get_session)
):
    if action not in ("merge", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be 'merge' or 'dismiss'")

    candidate = session.get(CustomerDuplicateCandidate, UUID(candidate_id))
    if candidate is None or candidate.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Duplicate candidate not found")

    if action == "dismiss":
        candidate.status = "dismissed"
        session.add(candidate)
        session.commit()
        return {"status": "dismissed"}

    winner = session.get(Customer, candidate.customer_id_a)
    loser = session.get(Customer, candidate.customer_id_b)
    if winner is None or loser is None:
        raise HTTPException(status_code=409, detail="One of the customers in this pair no longer exists")

    for model in _LINKED_MODELS:
        rows = session.exec(select(model).where(model.customer_id == loser.id)).all()
        for row in rows:
            row.customer_id = winner.id
            session.add(row)

    session.delete(loser)
    candidate.status = "merged"
    session.add(candidate)
    session.commit()
    return {"status": "merged", "winner_customer_id": str(winner.id)}
