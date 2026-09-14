# Day 6: Idempotency, Retries, Schema Evolution, Mock API, Fuzzy Dedupe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Idempotency-Key` request handling, exponential-backoff retries on the AI/network calls, a real second data source (a separate mock-CRM service pulled via a "connect API" path), a live "add a required field" schema-evolution rehearsal, and Splink-based fuzzy customer dedupe — the six items named in the Day 6 brief, ending with a working "pivot" demo.

**Architecture:** All backend additions live in `backend/app/` alongside Days 1-5's modules, following their established pattern (pure/testable core module + thin FastAPI router, `tenant_id`-scoped everywhere, SQLModel + Alembic for schema changes). The mock-CRM is a **second, fully independent FastAPI project** at `mock-crm/` (own `pyproject.toml`, own Dockerfile) — it must be a real separate service making a real HTTP round-trip, not an in-process stub, or the "connect API" story is fake. Splink dedupe runs with **hand-specified match/non-match probabilities** (no EM training) — verified against a live prototype during planning (see Task 6) because Splink's statistical training doesn't converge reliably on datasets this small (~30-90 rows); this is still genuine Fellegi-Sunter probabilistic linkage, just with expert-specified priors instead of learned ones, which is a documented, legitimate Splink usage mode.

**Tech Stack:** Adds `tenacity` (retry/backoff — the current de-facto standard, same tier of adoption as Instructor) and `splink` (v4, DuckDB backend, already `uv add`ed — see Task 6 for the verified API surface) to the existing FastAPI/SQLModel/Alembic/Polars/rapidfuzz/fastembed/Instructor stack. The mock-CRM service is a second FastAPI app.

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` — §"Idempotency design" and §"Retries" (lines ~133-140), §"Schema evolution / new required field mid-project" (lines ~142-148), §"Mock API creation" (line ~63-64), §"Fuzzy dedupe" (line ~206), §6 Day 6 bullet (line ~258).

**Design direction:** No new frontend UI is required by the Day 6 brief (the named deliverable is "the pivot demo works" — the schema-evolution rehearsal is a live walkthrough, not a UI feature). Task 8's live walkthrough is the acceptance test for the whole day, mirroring how Day 3's benchmark script and Day 5's curl walkthrough were the real verification, not just unit tests.

## Global Constraints

- **Every new table/column is tenant-scoped** (`tenant_id` column, indexed) — matches every existing table.
- **`tenacity`** is the retry library for both the LLM provider calls and the mock-CRM HTTP client — `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4), retry=retry_if_exception_type(...))`, applied narrowly to the specific exception types that mean "transient, worth retrying" (timeouts, 429, 503) — never wrapping a whole function body in a blanket retry.
- **Idempotency-Key is an optional request header**, never required — omitting it preserves every existing endpoint's current behavior exactly (backward compatible with all of Days 1-5's tests).
- **kyc_status, not risk_tier**, is the new required field for the schema-evolution demo — `risk_tier` already exists in `app/canonical.py` (optional, Day 1). Confirmed by reading the file during planning.
- **Alembic migrations are generated with `alembic revision --autogenerate`**, then hand-edited for data backfill (autogenerate never writes `UPDATE` statements) — the same workflow that produced the existing initial migration, not hand-typed DDL.
- **Splink's `Linker`/`SettingsCreator`/`DuckDBAPI` come from the top-level `splink` package** (`from splink import Linker, DuckDBAPI, SettingsCreator, block_on`), comparisons from `splink.comparison_library`. Verified against the actually-installed `splink==4.0.17` during planning — see Task 6's exact working snippet.
- **Mock-CRM reuses the "legacy" abbreviated header convention** (`CustID`, `CompName`, `Cntry`, `DT_Create`, ...) already registered as aliases in `app/canonical.py` (confirmed present — Day 3's benchmark work exercised these same abbreviations), so a pulled-from-API batch exercises the exact same deterministic-mapping path a legacy-header file upload does.
- **Docker image rebuilds are blocked in this sandbox** (Day 5 finding: the docker daemon here can't reach Docker Hub). Write real `Dockerfile`/`docker-compose.yml` additions for deployability, but run and test everything in this session via `uv run uvicorn` on the host, on distinct ports per service (main API `:8000`, mock-CRM `:8100`).

---

## Task 0: Config additions

**Files:**
- Modify: `backend/app/config.py`

**Interfaces:**
- Produces: `settings.mock_crm_base_url: str`, `settings.mock_crm_api_token: str` — consumed by Task 5's `app/mock_crm_client.py`.

- [ ] **Step 1: Add the two new settings**

```python
# backend/app/config.py -- add to the Settings class
mock_crm_base_url: str = "http://localhost:8100"
mock_crm_api_token: str = "mock-crm-demo-token"
```

- [ ] **Step 2: Confirm `tenacity` and `splink` are installed** (already added during planning)

```bash
cd backend && uv run python -c "import tenacity, splink; print(tenacity.__version__, splink.__version__)"
```

Expected: prints both version strings without error. `pyproject.toml` should already list both under `dependencies`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py backend/pyproject.toml backend/uv.lock
git commit -m "chore: add tenacity/splink deps and mock-CRM settings"
```

---

## Task 1: Idempotency-Key handling

**Files:**
- Create: `backend/app/idempotency.py`
- Modify: `backend/app/models.py` (add `IdempotencyKey` table)
- Modify: `backend/app/routers/upload.py`, `backend/app/routers/mapping_spec.py`
- Test: `backend/tests/test_idempotency.py`

**Interfaces:**
- Produces: `class IdempotencyConflict(Exception)`; `def run_idempotent(session: Session, *, tenant_id: str, endpoint: str, key: str | None, request_fingerprint: str, handler: Callable[[], tuple[int, dict]]) -> tuple[int, dict]` — if `key` is `None`, calls `handler()` directly (no idempotency behavior at all). If `key` is set: on first use, runs `handler()`, stores `(status_code, response_json)` keyed by `(tenant_id, endpoint, key)`, returns it. On replay with the **same** `request_fingerprint`, returns the stored `(status_code, response_json)` without calling `handler()` again. On replay with a **different** `request_fingerprint` under the same key, raises `IdempotencyConflict` (caller maps this to `409`).

- [ ] **Step 1: Add the `IdempotencyKey` model**

```python
# backend/app/models.py -- add near the bottom, after Quarantine or near the other control tables
class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint", "key", name="uq_idempotency_tenant_endpoint_key"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    endpoint: str
    key: str
    request_fingerprint: str
    status_code: int
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 2: Generate and apply the migration**

```bash
cd backend
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic revision --autogenerate -m "add idempotency_keys table"
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

Open the generated file under `backend/alembic/versions/` and confirm it only adds the `idempotency_keys` table (no unrelated diffs) before running `upgrade head`.

- [ ] **Step 3: Write failing tests for `run_idempotent`**

```python
# backend/tests/test_idempotency.py
from sqlmodel import Session

from app.db import engine
from app.idempotency import IdempotencyConflict, run_idempotent


def test_run_idempotent_without_key_always_calls_handler(unique_tenant_id):
    calls = []

    def handler():
        calls.append(1)
        return 201, {"n": len(calls)}

    with Session(engine) as session:
        first = run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key=None, request_fingerprint="x", handler=handler)
        second = run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key=None, request_fingerprint="x", handler=handler)

    assert first == (201, {"n": 1})
    assert second == (201, {"n": 2})
    assert len(calls) == 2


def test_run_idempotent_replays_stored_response_for_same_fingerprint(unique_tenant_id):
    calls = []

    def handler():
        calls.append(1)
        return 201, {"n": len(calls)}

    with Session(engine) as session:
        first = run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler)
        second = run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler)

    assert first == (201, {"n": 1})
    assert second == (201, {"n": 1})  # replayed, handler not called again
    assert len(calls) == 1


def test_run_idempotent_conflicts_on_different_fingerprint_same_key(unique_tenant_id):
    def handler():
        return 201, {"ok": True}

    with Session(engine) as session:
        run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="x", handler=handler)
        try:
            run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key="abc", request_fingerprint="y", handler=handler)
            assert False, "expected IdempotencyConflict"
        except IdempotencyConflict:
            pass


def test_run_idempotent_keys_are_scoped_per_endpoint(unique_tenant_id):
    """The same key string on two different endpoints must not collide."""
    def handler():
        return 200, {"ok": True}

    with Session(engine) as session:
        run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/upload", key="shared", request_fingerprint="x", handler=handler)
        # different endpoint, same key+fingerprint -- must not raise
        result = run_idempotent(session, tenant_id=unique_tenant_id, endpoint="/mapping-spec", key="shared", request_fingerprint="x", handler=handler)
    assert result == (200, {"ok": True})
```

- [ ] **Step 4: Run tests, confirm they fail** (`ModuleNotFoundError: app.idempotency`)

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_idempotency.py -v
```

- [ ] **Step 5: Implement `app/idempotency.py`**

```python
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
```

- [ ] **Step 6: Run tests, confirm they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_idempotency.py -v
```

- [ ] **Step 7: Wire `Idempotency-Key` into `POST /upload`**

```python
# backend/app/routers/upload.py -- add the import and header param, wrap the existing body
import hashlib as _hashlib  # already imported as hashlib -- reuse the existing import, this line is illustrative only
from fastapi import Header
from app.idempotency import IdempotencyConflict, run_idempotent
```

Change the route signature to accept `idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")`, and wrap the existing function body (everything currently inside `upload_file`) in a nested `def _do_upload() -> tuple[int, dict]:` that returns `(response.status_code, {...the existing return dict...})` instead of setting `response.status_code` and returning directly. The outer route becomes:

```python
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
    fingerprint = hashlib.sha256(raw_bytes).hexdigest() + f"|{source_name}|{source_kind}"

    def _do_upload() -> tuple[int, dict]:
        # ... the entire existing body of upload_file from `file_hash = ...`
        # onward, ending with `return status_code, {"batch_id": ..., "row_count": ..., "idempotent": ...}`
        # instead of `response.status_code = ...; return {...}`.
        ...

    try:
        status_code, body = run_idempotent(
            session, tenant_id=tenant_id, endpoint="/upload", key=idempotency_key,
            request_fingerprint=fingerprint, handler=_do_upload,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    response.status_code = status_code
    return body
```

Note `raw_bytes` is read **before** the idempotency check so the fingerprint can be computed from it; the rest of the original function body (source/batch lookup, CSV parsing, raw-record inserts) moves into `_do_upload` unchanged except for returning a tuple instead of mutating `response.status_code` directly.

- [ ] **Step 8: Write a failing test for upload idempotency**

```python
# backend/tests/test_upload.py -- add
def test_upload_with_idempotency_key_replays_without_reprocessing(unique_tenant_id):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()

    first = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "replay-test-key"},
    )
    second = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "replay-test-key"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()


def test_upload_same_idempotency_key_different_file_returns_409(unique_tenant_id):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        crm_content = f.read()
    with open("tests/fixtures/billing_tiny.csv", "rb") as f:
        billing_content = f.read()

    client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", crm_content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "conflict-test-key"},
    )
    conflict = client.post(
        "/upload",
        files={"file": ("billing_tiny.csv", billing_content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
        headers={"Idempotency-Key": "conflict-test-key"},
    )
    assert conflict.status_code == 409
```

Check what existing `tests/test_upload.py` imports/helpers already exist (the `client` fixture pattern matches every other router test file) before adding these.

- [ ] **Step 9: Run the new tests + the full upload test file, confirm pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_upload.py -v
```

- [ ] **Step 10: Wire `Idempotency-Key` into `POST /mapping-spec`** the same way — fingerprint = `f"{batch_id}"` (the only real input besides `tenant_id`, which is already part of the idempotency scope). This is the endpoint where a retried request has real teeth: without this, a retried `POST /mapping-spec` creates a spurious extra draft version every time. Wrap `create_mapping_spec`'s body the same way Step 7 wrapped `upload_file`'s.

- [ ] **Step 11: Write a failing-then-passing test proving no spurious version is created on replay**

```python
# backend/tests/test_mapping_spec_endpoint.py -- add
def test_mapping_spec_with_idempotency_key_does_not_create_a_second_version(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    first = client.post(
        "/mapping-spec",
        data={"tenant_id": unique_tenant_id, "batch_id": batch_id},
        headers={"Idempotency-Key": "spec-replay-key"},
    )
    second = client.post(
        "/mapping-spec",
        data={"tenant_id": unique_tenant_id, "batch_id": batch_id},
        headers={"Idempotency-Key": "spec-replay-key"},
    )
    assert first.json()["mapping_spec_id"] == second.json()["mapping_spec_id"]
    assert first.json()["version"] == second.json()["version"] == 1

    with Session(engine) as session:
        specs = session.exec(select(MappingSpec).where(MappingSpec.tenant_id == unique_tenant_id)).all()
        assert len(specs) == 1  # not 2
```

- [ ] **Step 12: Run the full backend suite twice, then commit**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/idempotency.py backend/app/models.py backend/app/routers/upload.py \
        backend/app/routers/mapping_spec.py backend/alembic/versions/ \
        backend/tests/test_idempotency.py backend/tests/test_upload.py backend/tests/test_mapping_spec_endpoint.py
git commit -m "feat: Idempotency-Key handling for /upload and /mapping-spec"
```

---

## Task 2: Exponential-backoff retries for LLM provider calls

**Files:**
- Modify: `backend/app/llm_mapper.py`
- Test: `backend/tests/test_llm_mapper.py` (extend existing)

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces: `_call_provider` (already exists) now retries transient failures internally before `tiebreak` falls through to the next provider — the public `tiebreak(...)` signature and `LLMDecision` shape are unchanged, so nothing downstream (Task 3's `mapping_spec.py`, Day 3's tests) needs to change.

- [ ] **Step 1: Read the current retry-relevant tests** in `backend/tests/test_llm_mapper.py` to see how provider failures are currently simulated (likely monkeypatching `app.llm_mapper._call_provider` or the OpenAI client) — match that pattern for the new tests rather than inventing a different mocking style.

- [ ] **Step 2: Write a failing test proving transient errors are retried with backoff, not immediately abandoned**

```python
# backend/tests/test_llm_mapper.py -- add
import time
from unittest.mock import patch

from openai import APITimeoutError

from app.llm_mapper import _call_provider_with_retry


def test_call_provider_with_retry_retries_on_timeout_then_succeeds():
    attempts = []

    def flaky(**kwargs):
        attempts.append(time.monotonic())
        if len(attempts) < 3:
            raise APITimeoutError(request=None)
        return "success"

    with patch("app.llm_mapper._call_provider", side_effect=flaky):
        result = _call_provider_with_retry(
            base_url="http://example.invalid", api_key="x", model="m",
            response_model=str, user_prompt="p",
        )

    assert result == "success"
    assert len(attempts) == 3
    # exponential backoff: each gap should be larger than the last (loosely -- timing-based, allow slack)
    gaps = [attempts[i + 1] - attempts[i] for i in range(len(attempts) - 1)]
    assert gaps[1] > gaps[0] * 0.8  # second wait is not shorter than the first


def test_call_provider_with_retry_gives_up_after_max_attempts():
    def always_fails(**kwargs):
        raise APITimeoutError(request=None)

    with patch("app.llm_mapper._call_provider", side_effect=always_fails):
        try:
            _call_provider_with_retry(
                base_url="http://example.invalid", api_key="x", model="m",
                response_model=str, user_prompt="p",
            )
            assert False, "expected the exception to propagate after retries are exhausted"
        except APITimeoutError:
            pass
```

Note `APITimeoutError(request=None)` — check the installed `openai` package's exact constructor signature (`uv run python -c "from openai import APITimeoutError; import inspect; print(inspect.signature(APITimeoutError.__init__))"`) and adjust the test's construction if it requires a real `httpx.Request` instead of `None`.

- [ ] **Step 3: Run, confirm failure** (`ImportError: cannot import name '_call_provider_with_retry'`)

- [ ] **Step 4: Implement the retry wrapper**

```python
# backend/app/llm_mapper.py -- add near the top, after the existing imports
from openai import APIConnectionError, APITimeoutError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Only retry genuinely transient failure modes -- a auth error or a
# malformed-response validation error (Instructor's own domain) should
# fall through to the next provider immediately, not eat 3 retries first.
_RETRYABLE_EXCEPTIONS = (APITimeoutError, APIConnectionError, RateLimitError)


@retry(
    retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def _call_provider_with_retry(**kwargs) -> BaseModel:
    return _call_provider(**kwargs)
```

Then change `tiebreak`'s two call sites (Groq and Ollama) from `_call_provider(...)` to `_call_provider_with_retry(...)` — same keyword arguments, no other changes. The existing broad `except Exception: pass` / `except Exception: return None` around each call site stays exactly as-is (it's what makes the Groq→Ollama→None fallback chain work) — retry-with-backoff happens *inside* `_call_provider_with_retry` before that broader catch ever sees the exception, for the specific transient cases; non-transient exceptions (auth, validation) still propagate immediately to the existing fallback logic, unretried.

- [ ] **Step 5: Run the new tests + the full `test_llm_mapper.py` file**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_llm_mapper.py -v
```

Expected: new tests pass; every pre-existing test in this file still passes (some of those tests hit the *real* Groq/Ollama APIs per Day 3's design — rerun them for real, don't just trust the diff).

- [ ] **Step 6: Run the full suite twice, then commit**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/llm_mapper.py backend/tests/test_llm_mapper.py
git commit -m "feat: exponential-backoff retry for transient LLM provider failures"
```

---

## Task 3: Shared ingest helper (refactor, no behavior change)

**Files:**
- Create: `backend/app/ingest.py`
- Modify: `backend/app/routers/upload.py`
- Test: run the existing `backend/tests/test_upload.py` unchanged — this task must not change its outcome.

**Interfaces:**
- Produces: `def ingest_rows(session: Session, *, tenant_id: str, source_name: str, source_kind: str, rows: list[dict], content_hash: str, filename: str, encoding: str | None = None, delimiter: str | None = None) -> tuple[OnboardingBatch, bool]` — returns `(batch, is_idempotent_replay)`. Consumed by both `routers/upload.py` (Task 3, this one) and `routers/connect.py` (Task 5).

- [ ] **Step 1: Extract the batch/source/raw-record logic from `upload_file`'s `_do_upload` (Task 1, Step 7) into `app/ingest.py`**, unchanged in behavior:

```python
"""Shared "land these rows as a new (or idempotently-replayed) batch"
logic -- used by both POST /upload (rows come from a parsed file) and
POST /connect/mock-crm (rows come from a paginated API pull). Keeping
this in one place means both ingestion paths get the same source
lookup-or-create, same file_hash-based batch dedup, and same raw_records
insert -- exactly the kind of duplicated "create a batch" logic Day 5's
plan explicitly flagged as a smell to avoid repeating.
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
```

- [ ] **Step 2: Rewrite `_do_upload` (from Task 1) to call `ingest_rows`** instead of duplicating the source/batch/raw-record logic:

```python
# backend/app/routers/upload.py -- _do_upload's body becomes:
def _do_upload() -> tuple[int, dict]:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    detection = from_bytes(raw_bytes).best()
    encoding = detection.encoding if detection else "utf-8"
    text = raw_bytes.decode(encoding, errors="replace")
    delimiter = _detect_delimiter(text[:2048])
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    batch, is_idempotent = ingest_rows(
        session, tenant_id=tenant_id, source_name=source_name, source_kind=source_kind,
        rows=rows, content_hash=file_hash, filename=file.filename or "unknown",
        encoding=encoding, delimiter=delimiter,
    )
    status_code = 200 if is_idempotent else 201
    return status_code, {"batch_id": str(batch.id), "row_count": batch.row_count, "idempotent": is_idempotent}
```

Remove the now-dead `_row_content_hash` from `upload.py` (it moved to `app/ingest.py`) and its now-unused imports (`Source`, `RawRecord` may still be needed elsewhere in the file — check before removing; `datetime`/`timezone` almost certainly become unused).

- [ ] **Step 3: Run the full existing `test_upload.py` (and the Task 1 idempotency tests) to prove zero behavior change**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_upload.py -v
```

Expected: every test passes exactly as before this refactor — this step has no new test cases of its own, it's a refactor verified by the existing suite.

- [ ] **Step 4: Run the full suite twice, then commit**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/ingest.py backend/app/routers/upload.py
git commit -m "refactor: extract shared ingest_rows() from upload router"
```

---

## Task 4: Mock-CRM service (separate FastAPI project)

**Files:**
- Create: `mock-crm/pyproject.toml`, `mock-crm/app/__init__.py`, `mock-crm/app/main.py`, `mock-crm/app/data.py`, `mock-crm/Dockerfile`, `mock-crm/tests/test_main.py`, `mock-crm/tests/__init__.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces (HTTP contract, consumed by Task 5's `app/mock_crm_client.py`): `GET /customers?page=1&page_size=20` with header `Authorization: Bearer <token>` → `200 {"items": [...], "page": int, "page_size": int, "total": int, "has_next": bool}` on success; `401` if the header is missing or wrong; occasionally `503 {"detail": "..."}` to simulate a flaky upstream (so retry/backoff in Task 5 has something real to exercise).

- [ ] **Step 1: Scaffold the project**

```bash
mkdir -p mock-crm/app mock-crm/tests
cd mock-crm
```

```toml
# mock-crm/pyproject.toml
[project]
name = "conduitai-mock-crm"
version = "0.1.0"
description = "Deliberately messy mock CRM API for ConduitAI's 'connect API' ingestion path"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "httpx>=0.27.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

```bash
touch app/__init__.py tests/__init__.py
uv sync
```

- [ ] **Step 2: Write the deliberately-messy static dataset**, reusing the same legacy abbreviated header convention `app/canonical.py`'s aliases already expect (`custid`, `compname`, `cntry`, `dt_create`):

```python
# mock-crm/app/data.py
"""A fixed, deterministic (no random seed drift) dataset of 25 messy
customer records, using the same abbreviated 'legacy CRM' header style
backend/app/canonical.py already has aliases for (see Day 1/3's seed
generator) -- so a batch pulled from this API exercises the identical
deterministic-mapping path a legacy-header file upload does.
"""

CUSTOMERS: list[dict] = [
    {
        "CustID": f"MCRM-{1000 + i}",
        "CompName": name,
        "Email": email,
        "Phone": phone,
        "Cntry": country,
        "Ind": industry,
        "RiskTier": risk,
        "DT_Create": created,
    }
    for i, (name, email, phone, country, industry, risk, created) in enumerate([
        ("Whitfield & Sons", "contact@whitfield.example", "+1-202-555-0101", "US", "Manufacturing", "medium", "2024-03-11"),
        ("Nordkap Logistics AB", "info@nordkap.example", "+46-8-555-0102", "SE", "Logistics", "low", "2024-01-22"),
        ("Rivera Consulting Group", "hello@riveraconsulting.example", "+1-415-555-0103", "US", "Consulting", "high", "2023-11-02"),
        ("Blackwood Textiles", "sales@blackwoodtextiles.example", "+44-20-555-0104", "GB", "Retail", "medium", "2024-06-19"),
        ("Meridian Health Partners", "admin@meridianhealth.example", "+1-312-555-0105", "US", "Healthcare", "high", "2023-09-14"),
        ("Kestrel Robotics", "team@kestrelrobotics.example", "+49-30-555-0106", "DE", "Manufacturing", "low", "2024-02-28"),
        ("Solaris Energy Corp", "contact@solarisenergy.example", "+1-713-555-0107", "US", "Energy", "medium", "2024-04-07"),
        ("Whitfield and Sons Ltd", "contact@whitfield.example", "+1-202-555-0101", "US", "Manufacturing", "medium", "2024-03-12"),
        ("Ashcombe Financial", "info@ashcombefinancial.example", "+44-161-555-0109", "GB", "Finance", "high", "2023-12-30"),
        ("Delacroix Freight", "ops@delacroixfreight.example", "+33-1-555-0110", "FR", "Logistics", "medium", "2024-05-16"),
        ("Yorkshire Dairy Co", "sales@yorkshiredairy.example", "+44-113-555-0111", "GB", "Retail", "low", "2024-01-09"),
        ("Cobalt Analytics", "hello@cobaltanalytics.example", "+1-650-555-0112", "US", "Technology", "medium", "2024-07-01"),
        ("Marbach Industrieteile", "kontakt@marbach.example", "+49-711-555-0113", "DE", "Manufacturing", "high", "2023-10-21"),
        ("Sundown Hospitality Group", "info@sundownhospitality.example", "+1-305-555-0114", "US", "Hospitality", "low", "2024-03-30"),
        ("Nordstrand Fisheries", "office@nordstrandfisheries.example", "+47-22-555-0115", "NO", "Agriculture", "medium", "2024-02-14"),
        ("Ironvale Steelworks", "sales@ironvalesteel.example", "+1-412-555-0116", "US", "Manufacturing", "high", "2023-08-19"),
        ("Cassia Wellness Clinics", "contact@cassiawellness.example", "+61-2-555-0117", "AU", "Healthcare", "medium", "2024-04-22"),
        ("Halden Maritime Services", "info@haldenmaritime.example", "+47-33-555-0118", "NO", "Logistics", "low", "2024-06-03"),
        ("Prairie Grain Traders", "trade@prairiegrain.example", "+1-306-555-0119", "CA", "Agriculture", "medium", "2023-12-05"),
        ("Vantage Point Insurance", "support@vantagepoint.example", "+1-617-555-0120", "US", "Finance", "high", "2024-01-27"),
        ("Elmsworth Timber Co", "office@elmsworthtimber.example", "+44-131-555-0121", "GB", "Manufacturing", "low", "2024-05-08"),
        ("Bright Harbor Software", "hello@brightharbor.example", "+1-206-555-0122", "US", "Technology", "medium", "2024-03-25"),
        ("Alto Verde Vineyards", "info@altoverde.example", "+56-2-555-0123", "CL", "Agriculture", "low", "2023-11-18"),
        ("Sentry Risk Advisors", "contact@sentryrisk.example", "+1-212-555-0124", "US", "Finance", "high", "2024-02-09"),
        ("Loch Fyne Renewables", "info@lochfynerenewables.example", "+44-141-555-0125", "GB", "Energy", "medium", "2024-06-27"),
    ])
]
```

Row 8 (`"Whitfield and Sons Ltd"`) is a deliberate near-duplicate of row 1 (`"Whitfield & Sons"`) — same email/phone/country, name spelled differently, one day apart in `DT_Create`. This gives Task 6's fuzzy-dedupe pass a real positive case once this batch is loaded, exactly like the file-upload seed data's own `DUPLICATE_FUZZY` row.

- [ ] **Step 3: Write the failing tests for the API contract**

```python
# mock-crm/tests/test_main.py
from fastapi.testclient import TestClient

from app.main import API_TOKEN, app

client = TestClient(app)


def test_customers_requires_auth():
    response = client.get("/customers")
    assert response.status_code == 401


def test_customers_rejects_wrong_token():
    response = client.get("/customers", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_customers_returns_first_page():
    response = client.get(
        "/customers", params={"page": 1, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 10
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["total"] == 25
    assert body["has_next"] is True
    assert "CustID" in body["items"][0]


def test_customers_last_page_has_no_next():
    response = client.get(
        "/customers", params={"page": 3, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    body = response.json()
    assert len(body["items"]) == 5  # 25 total, page 3 of size 10
    assert body["has_next"] is False


def test_customers_page_past_the_end_returns_empty_items():
    response = client.get(
        "/customers", params={"page": 99, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["has_next"] is False
```

- [ ] **Step 4: Run, confirm failure** (`ModuleNotFoundError: app.main`)

```bash
cd mock-crm && uv run pytest tests/ -v
```

- [ ] **Step 5: Implement `app/main.py`**

```python
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
```

- [ ] **Step 6: Run tests, confirm pass** (Steps 3's tests don't hit the every-4th-request failure since each test only makes 1-2 requests within a fresh counter per test-process run — if flaky due to the shared module-level counter across tests in one run, add a `conftest.py` fixture that resets `app.main._request_counter` before each test: `import app.main as main_module; main_module._request_counter = itertools.count(1)`)

```python
# mock-crm/tests/conftest.py
import itertools

import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _reset_request_counter():
    main_module._request_counter = itertools.count(1)
```

```bash
cd mock-crm && uv run pytest tests/ -v
```

- [ ] **Step 7: Write the Dockerfile**

```dockerfile
# mock-crm/Dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /code
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev
COPY app ./app
EXPOSE 8100
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
```

- [ ] **Step 8: Add the `mock-crm` service to `docker-compose.yml`**

```yaml
# docker-compose.yml -- add under services:
  mock-crm:
    build:
      context: ./mock-crm
    ports:
      - "8100:8100"
    environment:
      MOCK_CRM_API_TOKEN: mock-crm-demo-token
```

And add `MOCK_CRM_BASE_URL: http://mock-crm:8100` / `MOCK_CRM_API_TOKEN: mock-crm-demo-token` to the `api` service's `environment` block, and `mock-crm` to `api`'s `depends_on` (no healthcheck needed — a 503 from mock-crm is meant to be retried, not blocked on).

- [ ] **Step 9: Run mock-crm locally on the host** (the docker image can't be built in this sandbox — see Global Constraints)

```bash
cd mock-crm && MOCK_CRM_API_TOKEN=mock-crm-demo-token uv run uvicorn app.main:app --port 8100 --reload
```

Verify: `curl -s localhost:8100/health` returns `{"status":"ok"}`; `curl -s localhost:8100/customers -H "Authorization: Bearer mock-crm-demo-token"` returns 20 items.

- [ ] **Step 10: Commit**

```bash
git add mock-crm docker-compose.yml
git commit -m "feat: mock-CRM service -- paginated, authenticated, deliberately messy"
```

---

## Task 5: Connect-API ingestion path (with retry/backoff)

**Files:**
- Create: `backend/app/mock_crm_client.py`, `backend/app/routers/connect.py`
- Modify: `backend/app/main.py` (register the new router)
- Test: `backend/tests/test_connect_endpoint.py`

**Interfaces:**
- Consumes: `app.ingest.ingest_rows` (Task 3), `settings.mock_crm_base_url` / `settings.mock_crm_api_token` (Task 0).
- Produces: `POST /connect/mock-crm` (form: `tenant_id`, `source_name`) → same response shape as `POST /upload`: `{"batch_id": str, "row_count": int, "idempotent": bool}`. Also accepts `Idempotency-Key` (reuses Task 1's `run_idempotent`).

- [ ] **Step 1: Implement the retrying HTTP client**

```python
# backend/app/mock_crm_client.py
"""Paginated client for the mock-CRM service, with exponential-backoff
retry on transient failures (503, timeout, connection error) -- the
mock service is deliberately flaky (see mock-crm/app/main.py) so this
retry logic is exercised for real on most runs, not just in tests.
"""

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings


class MockCrmError(Exception):
    """The mock CRM couldn't be reached or returned an unexpected response."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 503)
    return False


# retry_if_exception (a predicate), not retry_if_exception_type -- a plain
# type check would also retry a 404 or other permanent 4xx that
# raise_for_status() turns into the same HTTPStatusError class, wasting
# 4 attempts on something that will never succeed.
@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=3),
    reraise=True,
)
def _get_page(client: httpx.Client, page: int, page_size: int) -> dict:
    response = client.get(
        "/customers",
        params={"page": page, "page_size": page_size},
        headers={"Authorization": f"Bearer {settings.mock_crm_api_token}"},
    )
    if response.status_code == 401:
        # Not retryable -- a bad token will never succeed on retry.
        raise MockCrmError(f"Mock CRM rejected the request: 401 {response.text}")
    response.raise_for_status()  # raises HTTPStatusError for 5xx -- retried by the decorator above
    return response.json()


def fetch_all_customers(page_size: int = 20) -> list[dict]:
    """Pages through /customers until has_next is False, returning every
    row. Raises MockCrmError (not retried further) if the service is
    unreachable or rejects auth outright."""
    rows: list[dict] = []
    try:
        with httpx.Client(base_url=settings.mock_crm_base_url, timeout=5.0) as client:
            page = 1
            while True:
                body = _get_page(client, page, page_size)
                rows.extend(body["items"])
                if not body["has_next"]:
                    break
                page += 1
    except httpx.TransportError as exc:
        raise MockCrmError(f"Could not reach mock CRM at {settings.mock_crm_base_url}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise MockCrmError(f"Mock CRM returned {exc.response.status_code} after retries: {exc}") from exc
    return rows
```

- [ ] **Step 2: Add `httpx` as an explicit dependency** (currently only a transitive dep via FastAPI's TestClient)

```bash
cd backend && uv add httpx
```

- [ ] **Step 3: Write failing tests for the client**, using `httpx.MockTransport` (no new mocking library needed — `httpx` ships this)

```python
# backend/tests/test_mock_crm_client.py
import httpx
import pytest

from app.mock_crm_client import MockCrmError, fetch_all_customers


def test_fetch_all_customers_paginates_until_exhausted(monkeypatch):
    monkeypatch.setattr("app.config.settings.mock_crm_base_url", "http://mock-crm.test")
    monkeypatch.setattr("app.config.settings.mock_crm_api_token", "test-token")

    pages = {
        1: {"items": [{"CustID": "A"}, {"CustID": "B"}], "page": 1, "page_size": 2, "total": 3, "has_next": True},
        2: {"items": [{"CustID": "C"}], "page": 2, "page_size": 2, "total": 3, "has_next": False},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json=pages[page])

    import app.mock_crm_client as client_module
    monkeypatch.setattr(
        client_module.httpx, "Client",
        lambda *a, **kw: httpx.Client(*a, transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "base_url"}, base_url=kw.get("base_url", "")),
    )

    rows = fetch_all_customers(page_size=2)
    assert [r["CustID"] for r in rows] == ["A", "B", "C"]


def test_fetch_all_customers_retries_transient_503_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.config.settings.mock_crm_base_url", "http://mock-crm.test")
    monkeypatch.setattr("app.config.settings.mock_crm_api_token", "test-token")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(503, json={"detail": "overloaded"})
        return httpx.Response(200, json={"items": [{"CustID": "A"}], "page": 1, "page_size": 20, "total": 1, "has_next": False})

    import app.mock_crm_client as client_module
    monkeypatch.setattr(
        client_module.httpx, "Client",
        lambda *a, **kw: httpx.Client(*a, transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "base_url"}, base_url=kw.get("base_url", "")),
    )

    rows = fetch_all_customers()
    assert rows == [{"CustID": "A"}]
    assert call_count["n"] == 3


def test_fetch_all_customers_401_raises_immediately_without_retry(monkeypatch):
    monkeypatch.setattr("app.config.settings.mock_crm_base_url", "http://mock-crm.test")
    monkeypatch.setattr("app.config.settings.mock_crm_api_token", "wrong-token")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(401, json={"detail": "unauthorized"})

    import app.mock_crm_client as client_module
    monkeypatch.setattr(
        client_module.httpx, "Client",
        lambda *a, **kw: httpx.Client(*a, transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "base_url"}, base_url=kw.get("base_url", "")),
    )

    with pytest.raises(MockCrmError):
        fetch_all_customers()
    assert call_count["n"] == 1  # not retried
```

If monkeypatching `httpx.Client` this way proves awkward in practice (the lambda reconstructing kwargs is fragile), the simpler fix discovered while implementing may be to add an optional `transport: httpx.BaseTransport | None = None` parameter to `fetch_all_customers`/`_get_page`, defaulted to `None` (production) and passed straight through to `httpx.Client(..., transport=transport)` — tests then pass `transport=httpx.MockTransport(handler)` directly instead of monkeypatching the `Client` constructor. Prefer this if it's cleaner; it's a better-designed seam either way (explicit dependency injection instead of monkeypatching a stdlib-adjacent class).

- [ ] **Step 4: Run, confirm failure, then implement against the tests** (implementation is already written in Step 1 — this step is: run, see failures from the test file itself (import errors or the transport-injection gap noted above), adjust `mock_crm_client.py` to accept the `transport` parameter if that's the path taken, rerun).

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mock_crm_client.py -v
```

- [ ] **Step 5: Implement `POST /connect/mock-crm`**

```python
# backend/app/routers/connect.py
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

    content_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    fingerprint = f"{content_hash}|{source_name}"

    def _do_connect() -> tuple[int, dict]:
        batch, is_idempotent = ingest_rows(
            session, tenant_id=tenant_id, source_name=source_name, source_kind="crm",
            rows=rows, content_hash=content_hash, filename="mock-crm-pull.json",
        )
        status_code = 200 if is_idempotent else 201
        return status_code, {"batch_id": str(batch.id), "row_count": batch.row_count, "idempotent": is_idempotent}

    try:
        status_code, body = run_idempotent(
            session, tenant_id=tenant_id, endpoint="/connect/mock-crm", key=idempotency_key,
            request_fingerprint=fingerprint, handler=_do_connect,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    response.status_code = status_code
    return body
```

Matches `upload.py`'s established `Response` parameter pattern exactly (Task 1, Step 7).

- [ ] **Step 6: Register the router**

```python
# backend/app/main.py -- add the import and include_router call alongside the existing ones
from app.routers.connect import router as connect_router
...
app.include_router(connect_router)
```

- [ ] **Step 7: Write the integration test** (requires the mock-crm service running on `:8100` from Task 4, Step 9 — start it first)

```python
# backend/tests/test_connect_endpoint.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_connect_mock_crm_ingests_all_pages(unique_tenant_id):
    response = client.post(
        "/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 25  # mock-crm/app/data.py's full dataset
    assert body["idempotent"] is False


def test_connect_mock_crm_second_pull_is_idempotent_on_content(unique_tenant_id):
    first = client.post("/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"})
    second = client.post("/connect/mock-crm", data={"tenant_id": unique_tenant_id, "source_name": "mock-crm"})
    assert first.json()["batch_id"] == second.json()["batch_id"]
    assert second.json()["idempotent"] is True
```

This test requires a live mock-crm at `settings.mock_crm_base_url` — note in the test file's docstring that it's an integration test, not a unit test, and needs `cd mock-crm && uv run uvicorn app.main:app --port 8100` running first (same pattern as `test_mdp_end_to_end.py` needing Postgres up).

- [ ] **Step 8: Run it for real** (mock-crm running on :8100, main backend's Postgres up)

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_connect_endpoint.py -v
```

If the mock-crm's every-4th-request 503 causes an occasional real failure here even after retries (bad luck on retry timing), that's informative, not a bug to silently work around -- investigate whether `stop_after_attempt(4)` is enough headroom against `_FAILURE_EVERY_N = 4` when paginating 2 pages (page_size 20 → 2 requests for 25 rows); if the two requests can both land on a failing count under adversarial timing, either is acceptable, but note the finding.

- [ ] **Step 9: Run the full backend suite twice** (excluding the mock-crm-dependent test if mock-crm isn't left running — note which tests need it), then commit

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/mock_crm_client.py backend/app/routers/connect.py backend/app/main.py \
        backend/pyproject.toml backend/uv.lock backend/tests/test_mock_crm_client.py backend/tests/test_connect_endpoint.py
git commit -m "feat: POST /connect/mock-crm -- paginated, retried, idempotent API ingestion"
```

---

## Task 6: Splink fuzzy dedupe

**Files:**
- Create: `backend/app/dedupe.py`, `backend/app/routers/dedupe.py`
- Modify: `backend/app/models.py` (add `CustomerDuplicateCandidate`), `backend/app/main.py`
- Test: `backend/tests/test_dedupe.py`, `backend/tests/test_dedupe_endpoint.py`

**Interfaces:**
- Produces: `class DuplicateCandidate(BaseModel): customer_id_a: str; customer_id_b: str; match_probability: float`; `def find_duplicate_candidates(customers: list[Customer], threshold: float = 0.5) -> list[DuplicateCandidate]` (pure, DB-free — takes already-loaded `Customer` rows, testable without a live linker in most tests); `GET /duplicates?tenant_id=...&status=open`, `POST /dedupe` (form: `tenant_id`) runs the pass and upserts candidates, `POST /duplicates/{id}/resolve` (form: `tenant_id`, `action`: `"merge"|"dismiss"`) — `merge` repoints every `Invoice`/`SupportTicket`/`Account` FK from the loser to the winner customer and deletes the loser row.

- [ ] **Step 1: Verify the exact Splink API this plan relies on is still correct against the installed version** (already verified once during planning against `splink==4.0.17` — re-verify if `uv.lock` shows a different resolved version by the time this task runs):

```bash
cd backend && uv run python -c "import splink; print(splink.__version__)"
uv run python -c "from splink import Linker, DuckDBAPI, SettingsCreator, block_on; import splink.comparison_library as cl; print('imports ok')"
```

- [ ] **Step 2: Add the `CustomerDuplicateCandidate` model**

```python
# backend/app/models.py -- add near Quarantine
class CustomerDuplicateCandidate(SQLModel, table=True):
    __tablename__ = "customer_duplicate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id_a", "customer_id_b", name="uq_dup_candidate_tenant_pair"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    # customer_id_a is always the lexicographically-smaller UUID string of
    # the pair -- normalizing pair order this way means re-running the
    # dedupe pass can upsert into the same row instead of creating a
    # mirror-image duplicate every time.
    customer_id_a: UUID = Field(foreign_key="customers.id")
    customer_id_b: UUID = Field(foreign_key="customers.id")
    match_probability: float
    status: str = Field(default="open")  # open | merged | dismissed
    created_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 3: Generate and apply the migration**

```bash
cd backend
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic revision --autogenerate -m "add customer_duplicate_candidates table"
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

Check the generated migration only touches `customer_duplicate_candidates` before applying.

- [ ] **Step 4: Write failing tests for the pure `find_duplicate_candidates` function**, using the exact verified Splink configuration from planning:

```python
# backend/tests/test_dedupe.py
from app.dedupe import find_duplicate_candidates
from app.models import Customer


def _customer(id_str: str, legal_name: str, email: str | None, country: str | None) -> Customer:
    from uuid import UUID as _UUID, uuid4
    return Customer(
        id=_UUID(int=int(id_str)), tenant_id="t", natural_key=id_str, legal_name=legal_name,
        email=email, country=country, content_hash="h", source_batch_id=uuid4(),
    )


def test_find_duplicate_candidates_catches_same_email_near_duplicate_name():
    customers = [
        _customer("1", "Whitfield & Sons", "contact@whitfield.example", "US"),
        _customer("2", "Whitfield and Sons Ltd", "contact@whitfield.example", "US"),
        _customer("3", "Totally Unrelated Corp", "other@unrelated.example", "FR"),
    ]

    candidates = find_duplicate_candidates(customers, threshold=0.5)

    pairs = {frozenset({c.customer_id_a, c.customer_id_b}) for c in candidates}
    assert frozenset({str(customers[0].id), str(customers[1].id)}) in pairs
    assert not any(str(customers[2].id) in pair for pair in pairs)


def test_find_duplicate_candidates_returns_nothing_for_fewer_than_two_customers():
    assert find_duplicate_candidates([], threshold=0.5) == []
    assert find_duplicate_candidates([_customer("1", "Solo Corp", None, None)], threshold=0.5) == []


def test_find_duplicate_candidates_handles_null_email_and_country_gracefully():
    """Splink comparisons must not blow up on the Customer model's
    genuinely-nullable email/country fields."""
    customers = [
        _customer("1", "Acme Corp", None, None),
        _customer("2", "Acme Corporation", None, None),
    ]
    candidates = find_duplicate_candidates(customers, threshold=0.0)
    assert isinstance(candidates, list)  # doesn't raise
```

- [ ] **Step 5: Run, confirm failure**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_dedupe.py -v
```

- [ ] **Step 6: Implement `app/dedupe.py`**, using the hand-specified-probability configuration verified during planning (this exact settings shape was run end-to-end against a live `Linker.inference.predict()` call and produced a 99.8% match probability for a same-email near-duplicate-name pair, and correctly excluded an unrelated third row):

```python
"""Splink-based fuzzy customer dedupe -- "same customer, different
spelling" (Day 6). Runs with hand-specified match/non-match
probabilities rather than Splink's statistical EM training: this
project's per-tenant customer counts (tens to low hundreds) are too
small for EM to converge reliably (verified during planning -- see this
plan's Task 6 notes), and expert-specified priors are a legitimate,
documented Splink usage mode, not a workaround. The email comparison
carries the most weight: two records sharing an exact email are strong
evidence of the same underlying customer regardless of how differently
the name is spelled, which is exactly the "same customer, different
spelling" case this is built to catch.
"""

import pandas as pd
from pydantic import BaseModel
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl

from app.models import Customer


class DuplicateCandidate(BaseModel):
    customer_id_a: str
    customer_id_b: str
    match_probability: float


def _build_settings() -> SettingsCreator:
    name_comparison = cl.JaroWinklerAtThresholds("legal_name", [0.9, 0.7]).configure(
        m_probabilities=[0.85, 0.5, 0.3, 0.02],
        u_probabilities=[0.001, 0.02, 0.05, 0.9],
    )
    email_comparison = cl.ExactMatch("email").configure(
        m_probabilities=[0.95, 0.05],
        u_probabilities=[0.001, 0.999],
    )
    country_comparison = cl.ExactMatch("country").configure(
        m_probabilities=[0.6, 0.4],
        u_probabilities=[0.3, 0.7],
    )
    return SettingsCreator(
        link_type="dedupe_only",
        probability_two_random_records_match=0.01,
        comparisons=[name_comparison, email_comparison, country_comparison],
        blocking_rules_to_generate_predictions=[
            block_on("substr(legal_name, 1, 3)"),
            block_on("email"),
        ],
        retain_intermediate_calculation_columns=False,
    )


def find_duplicate_candidates(customers: list[Customer], threshold: float = 0.5) -> list[DuplicateCandidate]:
    if len(customers) < 2:
        return []

    df = pd.DataFrame(
        [
            {
                "unique_id": str(c.id),
                "legal_name": c.legal_name,
                "email": c.email or "",
                "country": c.country or "",
            }
            for c in customers
        ]
    )

    db_api = DuckDBAPI()
    linker = Linker(df, _build_settings(), db_api, set_up_basic_logging=False)
    result = linker.inference.predict(threshold_match_probability=threshold)
    result_df = result.as_pandas_dataframe()

    return [
        DuplicateCandidate(
            customer_id_a=row["unique_id_l"],
            customer_id_b=row["unique_id_r"],
            match_probability=round(float(row["match_probability"]), 4),
        )
        for _, row in result_df.iterrows()
    ]
```

- [ ] **Step 7: Run tests, confirm pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_dedupe.py -v
```

If the email-blocking rule (`block_on("email")`) errors on empty-string emails behaving oddly with DuckDB's `=` semantics, check the actual error; empty string `""` equals `""` (not the same as SQL `NULL`), so two customers with both-blank emails would spuriously block-and-compare against each other -- confirmed acceptable for this dataset's scale (a demo-sized customer list), but worth a one-line comment in the code if observed.

- [ ] **Step 8: Write the router**

```python
# backend/app/routers/dedupe.py
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
```

- [ ] **Step 9: Register the router**

```python
# backend/app/main.py
from app.routers.dedupe import router as dedupe_router
...
app.include_router(dedupe_router)
```

- [ ] **Step 10: Write failing tests for the endpoints**, building real duplicate customers via `app.loader.load_batch` (same DB-direct pattern as `tests/test_loader.py`/`tests/test_quarantine_endpoint.py`) rather than through the real scorer:

```python
# backend/tests/test_dedupe_endpoint.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.loader import load_batch
from app.main import app
from app.models import Account, Customer, Invoice, MappingSpec, OnboardingBatch, Source

client = TestClient(app)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def _load_two_near_duplicate_customers(tenant_id: str) -> tuple[str, str]:
    with Session(engine) as session:
        source = Source(tenant_id=tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
            "customer_email": _spec_entry("email", "trim"),
        }
        spec = MappingSpec(tenant_id=tenant_id, source_id=source.id, version=1, spec_json=spec_json, status="confirmed")
        session.add(spec)
        session.commit()

        batch = OnboardingBatch(tenant_id=tenant_id, source_id=source.id, file_hash="h", filename="t.csv", row_count=0)
        session.add(batch)
        session.commit()
        session.refresh(batch)

        from app.models import RawRecord
        session.add(RawRecord(batch_id=batch.id, source_id=source.id, tenant_id=tenant_id, row_index=0, raw_json={"customer_id": "C-1", "company_name": "Whitfield & Sons", "email": "contact@whitfield.example"}, content_hash="h1"))
        session.add(RawRecord(batch_id=batch.id, source_id=source.id, tenant_id=tenant_id, row_index=1, raw_json={"customer_id": "C-2", "company_name": "Whitfield and Sons Ltd", "email": "contact@whitfield.example"}, content_hash="h2"))
        session.commit()

        load_batch(session, tenant_id, batch)

        customers = session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all()
        assert len(customers) == 2
        return str(customers[0].id), str(customers[1].id)


def test_dedupe_finds_the_near_duplicate_pair(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)

    response = client.post("/dedupe", data={"tenant_id": unique_tenant_id})

    assert response.status_code == 200
    assert response.json()["candidates_found"] >= 1
    assert response.json()["new"] >= 1

    listed = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"]
    assert len(listed) >= 1
    assert listed[0]["match_probability"] > 0.5


def test_resolve_merge_repoints_invoices_and_deletes_the_loser(unique_tenant_id):
    id_a, id_b = _load_two_near_duplicate_customers(unique_tenant_id)
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    candidate = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"][0]

    with Session(engine) as session:
        loser_id = candidate["customer_id_b"]
        winner_id = candidate["customer_id_a"]
        session.add(Invoice(
            tenant_id=unique_tenant_id, natural_key="INV-1", customer_id=loser_id, invoice_number="INV-1",
            amount_minor_units=1000, currency="USD", issue_date="2024-01-01", content_hash="h", source_batch_id=None,
        ))
        # source_batch_id/FK constraints: use an existing batch id instead of None if the DB enforces it --
        # look up a real OnboardingBatch.id for this tenant before this insert if the FK is NOT NULL + enforced.
        session.commit()

    response = client.post(f"/duplicates/{candidate['id']}/resolve", data={"tenant_id": unique_tenant_id, "action": "merge"})
    assert response.status_code == 200
    assert response.json()["status"] == "merged"

    with Session(engine) as session:
        assert session.get(Customer, loser_id) is None
        moved_invoice = session.exec(select(Invoice).where(Invoice.tenant_id == unique_tenant_id)).first()
        assert str(moved_invoice.customer_id) == winner_id


def test_resolve_dismiss_marks_status_without_deleting_anything(unique_tenant_id):
    _load_two_near_duplicate_customers(unique_tenant_id)
    client.post("/dedupe", data={"tenant_id": unique_tenant_id})
    candidate = client.get("/duplicates", params={"tenant_id": unique_tenant_id}).json()["items"][0]

    response = client.post(f"/duplicates/{candidate['id']}/resolve", data={"tenant_id": unique_tenant_id, "action": "dismiss"})
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"

    with Session(engine) as session:
        assert session.get(Customer, candidate["customer_id_a"]) is not None
        assert session.get(Customer, candidate["customer_id_b"]) is not None
```

`Invoice.source_batch_id` is a required FK in `app.models` -- before running this, check whether inserting `None` fails (it almost certainly does, per the model definition read during planning: `source_batch_id: UUID = Field(foreign_key="onboarding_batches.id")` with no default, so it's required). Fix the merge test by reusing the real `batch.id` from `_load_two_near_duplicate_customers` (return it as a third value from that helper) instead of `None`.

- [ ] **Step 11: Run, confirm failure, fix the FK issue noted above, implement until green**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_dedupe_endpoint.py -v
```

- [ ] **Step 12: Run the full suite twice, then commit**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/dedupe.py backend/app/routers/dedupe.py backend/app/models.py backend/app/main.py \
        backend/alembic/versions/ backend/tests/test_dedupe.py backend/tests/test_dedupe_endpoint.py
git commit -m "feat: Splink fuzzy dedupe -- POST /dedupe, GET /duplicates, resolve (merge/dismiss)"
```

---

## Task 7: Schema-evolution live demo -- add `kyc_status` as a required field

**Files:**
- Modify: `backend/app/models.py` (Customer gets `kyc_status`), `backend/app/canonical.py` (register the field)
- Create: two Alembic migrations (nullable-add, then backfill+NOT NULL)
- Test: `backend/tests/test_schema_evolution.py`

**Interfaces:**
- Produces: `Customer.kyc_status: str` (required after the second migration); a new `CanonicalField(name="kyc_status", ...)` entry.

**Why `kyc_status`, not `risk_tier`:** `app/canonical.py` already has `risk_tier` (Day 1, optional) -- confirmed by reading the file during planning. Reusing it would fake the "customer adds a genuinely new field" narrative. `kyc_status` (Know Your Customer verification state: `pending`/`verified`/`rejected`) is a realistic new compliance requirement for a risk-platform onboarding pipeline and doesn't exist anywhere in the schema yet.

**The key architectural finding this task proves (verify it, don't just assert it):** `app.record_builder.apply_spec_to_row` only ever processes canonical fields that are *present as keys in a given mapping spec's `spec_json`* (confirmed by reading the function during planning: `for canonical_field_name, entry in spec_json.items()`). A field added to `app/canonical.py` that isn't in an *old* confirmed spec's `spec_json` is never even looked at when that old spec is used -- so historical batches loaded under an old spec version are automatically unaffected by a new required field, with **no separate "validation rule versioning" system needed**. The spec's own hedge ("rules are versioned too, or scoped by effective_date") turns out to already be satisfied by the existing mapping-spec-per-batch versioning from Day 3 -- this task's tests exist specifically to prove that finding, not to build new infrastructure for it.

- [ ] **Step 1: Add `kyc_status` to the `Customer` model** (nullable for now -- Step 3 makes it required after backfill)

```python
# backend/app/models.py -- Customer class, add after risk_tier (or any existing column)
kyc_status: str | None = None
```

- [ ] **Step 2: Generate and apply migration 1 (add nullable column)**

```bash
cd backend
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic revision --autogenerate -m "add customers.kyc_status (nullable)"
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

Confirm the generated migration is exactly `op.add_column('customers', sa.Column('kyc_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True))` and nothing else.

- [ ] **Step 3: Hand-edit a second migration for the backfill + NOT NULL**

```bash
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic revision -m "backfill customers.kyc_status and make it required"
```

(Plain `revision`, not `--autogenerate` -- there's no model change to detect yet; Step 4 changes the model *after* this migration exists, matching the real "add nullable, backfill, model catches up" order the spec describes.) Edit the generated file:

```python
def upgrade() -> None:
    op.execute("UPDATE customers SET kyc_status = 'pending' WHERE kyc_status IS NULL")
    op.alter_column('customers', 'kyc_status', nullable=False)


def downgrade() -> None:
    op.alter_column('customers', 'kyc_status', nullable=True)
```

```bash
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

- [ ] **Step 4: Update the model to match** (now required, matching the DB)

```python
# backend/app/models.py -- Customer class
kyc_status: str
```

- [ ] **Step 5: Register the canonical field**

```python
# backend/app/canonical.py -- add to CANONICAL_FIELDS, near the other customers-table fields
CanonicalField(
    name="kyc_status",
    target_table="customers",
    target_column="kyc_status",
    type=FieldType.ENUM,
    required=True,
    description="Know-Your-Customer verification status for this customer record.",
    aliases=["kyc_status", "kyc", "kyc_state", "verification_status"],
    enum_values=["pending", "verified", "rejected"],
),
```

- [ ] **Step 6: Run the full existing suite once before writing new tests** -- this is the step that would immediately surface if `kyc_status` being required breaks any *existing* test that constructs a `Customer` directly (several test files do: `tests/test_loader.py`, `tests/test_dedupe.py` from Task 6, `tests/test_metrics_endpoint.py`, etc., via SQLModel's `Customer(...)` constructor or via `app.loader.load_batch`'s upsert path)

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
```

Two categories of failure are expected here, both real and both need fixing, not suppressing:
1. Any test file that builds a `Customer(...)` directly without `kyc_status` -- add `kyc_status="pending"` to those constructor calls.
2. `app.loader._upsert_customer` inserts whatever `record.values` gives it -- since `kyc_status` won't be in most existing test specs' `spec_json`, the built `Customer` insert will be missing the column entirely, and Postgres will reject it (`NOT NULL constraint`). Fix: give `kyc_status` a database-level default so an *old* spec that doesn't map it still loads successfully. Add a third, small migration:

```bash
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic revision -m "add server default for customers.kyc_status"
```

```python
def upgrade() -> None:
    op.alter_column('customers', 'kyc_status', server_default='pending')


def downgrade() -> None:
    op.alter_column('customers', 'kyc_status', server_default=None)
```

```bash
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

Re-run the full suite; fix any remaining direct-`Customer(...)`-construction test failures by adding `kyc_status="pending"` explicitly (the server default only covers SQL-level inserts missing the column, not SQLModel's Python-side required-field validation on direct construction, which will still demand the field or itself default -- check whether `Customer.kyc_status: str` needs `= "pending"` as a Python-side default too, or whether every direct-construction call site genuinely needs updating; prefer updating call sites explicitly if there are few of them, since a silent Python-side default would mask the same "old code doesn't know about this field" reality the server default is meant to represent at the DB layer only).

- [ ] **Step 7: Write the rehearsal test proving the core finding**

```python
# backend/tests/test_schema_evolution.py
"""Rehearses the Day 6 'add a required field mid-project' demo: an old
confirmed mapping spec (created before kyc_status existed) keeps working
unmodified, while a *new* spec version that maps kyc_status enforces it
as required -- with zero special-case code, because app.record_builder
only ever processes fields present in a given spec_json."""

from sqlmodel import Session, select

from app.db import engine
from app.loader import load_batch
from app.mapping_spec import save_mapping_spec
from app.models import Customer, MappingSpec, OnboardingBatch, Quarantine, RawRecord, Source


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


_OLD_SPEC = {
    "customer_natural_key": _spec_entry("customer_id", "trim"),
    "legal_name": _spec_entry("company_name", "trim"),
}
_NEW_SPEC_WITH_KYC = {
    **_OLD_SPEC,
    "kyc_status": _spec_entry("kyc", "map_enum"),
}


def _make_batch(session: Session, tenant_id: str, spec_json: dict, raw_json: dict) -> OnboardingBatch:
    source = Source(tenant_id=tenant_id, name="crm", kind="crm")
    session.add(source)
    session.commit()
    session.refresh(source)

    spec = MappingSpec(tenant_id=tenant_id, source_id=source.id, version=1, spec_json=spec_json, status="confirmed")
    session.add(spec)
    session.commit()

    batch = OnboardingBatch(tenant_id=tenant_id, source_id=source.id, file_hash="h", filename="t.csv", row_count=0)
    session.add(batch)
    session.commit()
    session.refresh(batch)

    session.add(RawRecord(batch_id=batch.id, source_id=source.id, tenant_id=tenant_id, row_index=0, raw_json=raw_json, content_hash="h"))
    session.commit()
    return batch


def test_old_spec_without_kyc_status_still_loads_the_customer_with_the_default(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, _OLD_SPEC, {"customer_id": "C-1", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 0  # not retroactively broken by the new required field
        customer = session.exec(select(Customer).where(Customer.tenant_id == unique_tenant_id)).first()
        assert customer.kyc_status == "pending"  # the server_default, since the old spec never set it


def test_new_spec_with_kyc_status_mapped_enforces_it_as_required(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(
            session, unique_tenant_id, _NEW_SPEC_WITH_KYC,
            {"customer_id": "C-2", "company_name": "Beta", "kyc": ""},  # blank -- missing the now-required field
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 0
        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).first()
        assert "MISSING_REQUIRED" in quarantined.error_codes


def test_new_spec_with_kyc_status_provided_loads_successfully(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(
            session, unique_tenant_id, _NEW_SPEC_WITH_KYC,
            {"customer_id": "C-3", "company_name": "Gamma", "kyc": "verified"},
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        customer = session.exec(select(Customer).where(Customer.tenant_id == unique_tenant_id, Customer.natural_key == "C-3")).first()
        assert customer.kyc_status == "verified"
```

- [ ] **Step 8: Run, confirm pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_schema_evolution.py -v
```

- [ ] **Step 9: Rehearse the full live narrative for real** against actual seed data + the actual `POST /mapping-spec` endpoint (not the hand-built specs above) -- this is the literal "watch me add a required field and re-onboard without breaking prior data" demo the spec calls for:

```bash
cd backend
uv run python -m seed.generate_seed_data  # if not already generated this session
```

```bash
TENANT="schema-evolution-demo-$(date +%s)"

# 1. Upload + load crm_snake.csv under the OLD world (spec v1, no kyc_status) -- prove it still works post-migration.
UPLOAD=$(curl -s -X POST http://localhost:8000/upload -F "tenant_id=$TENANT" -F "source_name=crm" -F "source_kind=crm" -F "file=@seed/output/crm_snake.csv;type=text/csv")
BATCH_ID=$(echo "$UPLOAD" | python3 -c "import json,sys;print(json.load(sys.stdin)['batch_id'])")
SPEC=$(curl -s -X POST http://localhost:8000/mapping-spec -F "tenant_id=$TENANT" -F "batch_id=$BATCH_ID")
SPEC_ID=$(echo "$SPEC" | python3 -c "import json,sys;print(json.load(sys.stdin)['mapping_spec_id'])")
curl -s -X POST "http://localhost:8000/mapping-spec/$SPEC_ID/confirm" -F "tenant_id=$TENANT"
curl -s -X POST http://localhost:8000/load -F "tenant_id=$TENANT" -F "batch_id=$BATCH_ID"
# Expect: loaded rows have kyc_status='pending' (the server default) -- old spec never mapped it.

# 2. Re-run POST /mapping-spec for the SAME batch -- a fresh scorer pass now sees kyc_status as a candidate
#    field too (even though this file has no matching column, proving it's now in the candidate pool).
curl -s -X POST http://localhost:8000/mapping-spec -F "tenant_id=$TENANT" -F "batch_id=$BATCH_ID" | python3 -c "import json,sys; d=json.load(sys.stdin); print('version:', d['version']); print('kyc_status in spec:', 'kyc_status' in d['spec_json'])"
```

Record the actual output of this run (paste it into this plan's Task 8 write-up, or directly into `docs/PROGRESS.md`'s Day 6 section) as the real evidence the demo works, the same way Day 3's benchmark numbers and Day 5's curl walkthrough were recorded as real evidence, not just "tests pass."

- [ ] **Step 10: Run the full suite twice, then commit**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
git add backend/app/models.py backend/app/canonical.py backend/alembic/versions/ backend/tests/test_schema_evolution.py
git commit -m "feat: schema-evolution demo -- add required customers.kyc_status field"
```

---

## Task 8: Full verification pass, docs, and the "pivot" walkthrough

- [ ] **Step 1: Run the complete backend suite twice** (all of Tasks 1-7's tests together, proving nothing regressed):

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -q
```

- [ ] **Step 2: Run the mock-crm service's own suite**

```bash
cd mock-crm && uv run pytest -v
```

- [ ] **Step 3: Live walkthrough of every Day 6 capability against the running services** (main API on `:8000`, mock-crm on `:8100`, Postgres up) -- this is the actual "pivot demo," not a formality:
  1. `POST /upload` with an `Idempotency-Key` header, replayed -- confirm identical response, no duplicate batch.
  2. `POST /connect/mock-crm` -- confirm 25 rows land as one batch, exercising the paginated+retried client against the real (occasionally-503) mock service.
  3. Profile + map + confirm + load the mock-crm batch -- confirm the legacy-header aliases (`custid`, `compname`, `cntry`, `dt_create`) auto-map at high confidence, same as a file upload would.
  4. `POST /dedupe` on a tenant with the mock-crm batch loaded (which contains the `Whitfield & Sons` / `Whitfield and Sons Ltd` near-duplicate) -- confirm `GET /duplicates` shows the pair with a high match probability; resolve it with `action=merge`; confirm the customer count drops by one and no invoices/tickets were orphaned.
  5. Re-run Task 7 Step 9's schema-evolution walkthrough fresh, recording the actual output.
  6. Kill the mock-crm process mid-way through a `POST /connect/mock-crm` call (or lower `_FAILURE_EVERY_N` temporarily) to watch the retry/backoff actually retry and either recover or fail cleanly with a `502` and a clear message -- confirm it's not a silent hang or an unhandled exception.

- [ ] **Step 4: Update `docs/PROGRESS.md`** with a `## Day 6` section following the exact style of the existing Day 2/3/5 entries -- **Built**, then an **Issues hit while building it** table/list documenting anything discovered during real execution that this plan didn't anticipate (there will be some -- Splink API quirks, the FK-constraint gotcha in Task 6's merge test, exact retry-timing behavior under the mock-crm's every-4th-request failure, etc.), then update the **Where things stand** section's test count, endpoint list, and "not done yet" list (should now read something like: production job/worker infra, deployment/VPC docs -- Day 7's territory).

- [ ] **Step 5: Do not commit yet.** Per this project's established convention (see `docs/PROGRESS.md`'s Day 5 entry and this session's prior turns), all of Day 6's work gets squashed into a single commit on `main`, written by the user, without a Claude co-author trailer -- stage everything (`git add`) and stop, the same way Day 5 ended.
