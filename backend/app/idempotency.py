"""Idempotency-Key request handling -- the Stripe-style pattern named in
this project's spec. Complementary to (not a replacement for) the
content-hash-based idempotency `POST /upload` and `app.loader` already
have: this protects against a *retried request* (e.g. a client that
times out waiting for a response and retries, not knowing whether the
first attempt succeeded), where the request's content might even be
byte-identical -- the guarantee here is "replaying the same key returns
the same stored response," not "detects duplicate content."
"""

from collections.abc import Callable
from typing import Any

from sqlmodel import Session, select

from app.models import IdempotencyKey


class IdempotencyConflict(Exception):
    """The same Idempotency-Key was reused with a different request."""


def run_idempotent(
    session: Session,
    *,
    tenant_id: str,
    endpoint: str,
    key: str | None,
    request_fingerprint: str,
    handler: Callable[[], tuple[int, dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    if key is None:
        return handler()

    existing = session.exec(
        select(IdempotencyKey).where(
            IdempotencyKey.tenant_id == tenant_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.key == key,
        )
    ).first()
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise IdempotencyConflict(
                f"Idempotency-Key {key!r} was already used with a different request on {endpoint}"
            )
        return existing.status_code, existing.response_json

    status_code, response_json = handler()
    session.add(
        IdempotencyKey(
            tenant_id=tenant_id,
            endpoint=endpoint,
            key=key,
            request_fingerprint=request_fingerprint,
            status_code=status_code,
            response_json=response_json,
        )
    )
    session.commit()
    return status_code, response_json
