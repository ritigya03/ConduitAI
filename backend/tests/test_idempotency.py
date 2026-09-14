from sqlmodel import Session

from app.db import engine
from app.idempotency import IdempotencyConflict, run_idempotent


def test_run_idempotent_without_key_always_calls_handler(unique_tenant_id):
    calls = []

    def handler():
        calls.append(1)
        return 201, {"n": len(calls)}

    with Session(engine) as session:
        first = run_idempotent(
            session, tenant_id=unique_tenant_id, endpoint="/upload", key=None, request_fingerprint="x", handler=handler
        )
        second = run_idempotent(
            session, tenant_id=unique_tenant_id, endpoint="/upload", key=None, request_fingerprint="x", handler=handler
        )

    assert first == (201, {"n": 1})
    assert second == (201, {"n": 2})
    assert len(calls) == 2


def test_run_idempotent_replays_stored_response_for_same_fingerprint(unique_tenant_id):
    calls = []

    def handler():
        calls.append(1)
        return 201, {"n": len(calls)}

    with Session(engine) as session:
        first = run_idempotent(
            session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler
        )
        second = run_idempotent(
            session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler
        )

    assert first == (201, {"n": 1})
    assert second == (201, {"n": 1})  # replayed, handler not called again
    assert len(calls) == 1


def test_run_idempotent_conflicts_on_different_fingerprint_same_key(unique_tenant_id):
    def handler():
        return 201, {"ok": True}

    with Session(engine) as session:
        run_idempotent(
            session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler
        )
        try:
            run_idempotent(
                session,
                tenant_id=unique_tenant_id,
                endpoint="/upload",
                key="abc",
                request_fingerprint="y",
                handler=handler,
            )
            assert False, "expected IdempotencyConflict"
        except IdempotencyConflict:
            pass


def test_run_idempotent_keys_are_scoped_per_endpoint(unique_tenant_id):
    """The same key string on two different endpoints must not collide."""

    def handler():
        return 200, {"ok": True}

    with Session(engine) as session:
        run_idempotent(
            session,
            tenant_id=unique_tenant_id,
            endpoint="/upload",
            key="shared",
            request_fingerprint="x",
            handler=handler,
        )
        # different endpoint, same key+fingerprint -- must not raise
        result = run_idempotent(
            session,
            tenant_id=unique_tenant_id,
            endpoint="/mapping-spec",
            key="shared",
            request_fingerprint="x",
            handler=handler,
        )
    assert result == (200, {"ok": True})
