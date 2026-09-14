"""Mock CRM service: a deliberately separate FastAPI app (not a stub
inside the main backend) so ConduitAI's 'connect API' ingestion path
(backend/app/routers/connect.py) makes a real HTTP round-trip against a
real second service -- paginated, authenticated, and occasionally flaky
on purpose, so retry/backoff (Day 6) has something real to demonstrate.
"""

import itertools
import os

from fastapi import FastAPI, Header, HTTPException, Query

from app.data import CUSTOMERS

API_TOKEN = os.environ.get("MOCK_CRM_API_TOKEN", "mock-crm-demo-token")
# Fails roughly every Nth request (not the first, so a demo run's very
# first call reliably succeeds) -- purely to give the retry/backoff
# story something to visibly do. A module-level counter is fine: this
# service is single-process, in-memory, throwaway state by design.
_FAILURE_EVERY_N = 4
_request_counter = itertools.count(1)

app = FastAPI(title="Mock CRM")


@app.get("/customers")
def list_customers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    count = next(_request_counter)
    if count % _FAILURE_EVERY_N == 0:
        raise HTTPException(status_code=503, detail="Mock CRM is temporarily overloaded, try again")

    start = (page - 1) * page_size
    end = start + page_size
    items = CUSTOMERS[start:end]
    total = len(CUSTOMERS)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": end < total,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
