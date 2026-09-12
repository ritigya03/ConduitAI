# Day 1: Foundations & Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the ConduitAI backend skeleton — Docker Compose (Postgres + FastAPI), the full canonical SQLModel schema with an Alembic migration, a canonical-field registry, an idempotent `POST /upload` endpoint, and a deterministic seed-data generator with a ≥40-column labeled mapping benchmark — so a messy CSV can be uploaded and its raw rows persisted, end to end.

**Architecture:** FastAPI app backed by Postgres via SQLModel, migrated with Alembic. Raw uploaded rows land untouched in `raw_records` (JSONB), tagged by an idempotent `onboarding_batches` row keyed on `(tenant_id, source_id, file_hash)`. A separate `app/canonical.py` registry (not a DB table) describes the canonical fields — name, target table/column, type, required, description, aliases — that Day 2/3 mapping and Day 4 transform will consume. A standalone seed generator produces four deliberately messy CSVs plus a `ground_truth.json` (column mappings + injected defects) for later benchmarking.

**Tech Stack:** Python 3.12, FastAPI, SQLModel (SQLAlchemy 2.0 + Pydantic v2), Alembic, Postgres 16, `uv`, `charset-normalizer`, `faker`, Docker Compose.

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` (repo root) — see §1 (stack), §2 (architecture/schema), §5 (metrics), §6 Day 1 (scope). This plan also incorporates review feedback gathered during brainstorming (canonical field registry, two-part `ground_truth.json`, ≥40-column benchmark, determinism, constraints-now-not-later, upload idempotency semantics, three SQLModel/Alembic gotchas, Compose healthcheck).

## Global Constraints

- Python **3.12** pinned in the Docker image (host is 3.14.7; do not rely on the host interpreter for anything that runs in a container).
- **uv** is the only dependency manager — no bare `pip install`, no Poetry.
- Postgres **16**. Connection string uses the `postgresql+psycopg://` (psycopg v3) dialect.
- Money is **never** stored as float — always integer minor units + a currency code (applies from Day 4 onward; Day 1 just defines the columns).
- **`tenant_id` on every table**, no exceptions.
- Raw data is **immutable** — `raw_records` rows are written once and never updated.
- `customers`, `invoices`, `support_tickets` each get a `natural_key` column, a `content_hash` column, and a `UNIQUE(tenant_id, natural_key)` constraint **in the initial migration** — do not defer this to a later migration.
- Seed data generation must be **byte-for-byte deterministic**: `random.seed(42)` and `Faker.seed(42)`, called every time before generation.
- No LLM calls, no embeddings, no mapping logic in Day 1 — that's Day 2/3. `app/canonical.py` only describes fields; it does not score or map anything yet.
- Every canonical-field name used in the seed generator's `ground_truth.json` must correspond to a real entry in `app.canonical.CANONICAL_FIELDS` — validated at generation time, not just by convention.
- Use `datetime.now(timezone.utc)`, never the deprecated `datetime.utcnow()`.
- Every DB-touching test gets its own tenant via the `unique_tenant_id` fixture (`backend/tests/conftest.py`) — no hardcoded tenant strings. The suite must pass on repeated runs without truncating tables in between.
- `docker compose up` alone (from a clean volume) must bring up a fully migrated, working API — the container's entrypoint runs `alembic upgrade head`, not a human.

---

## Task 1: Docker Compose + Dockerfile + uv bootstrap + `/health`

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_health.py`
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`
- Create: `docker-compose.yml` (repo root)
- Modify: `.gitignore` (repo root — create if absent)

**Interfaces:**
- Produces: `app.main:app` (a `FastAPI` instance), `GET /health -> {"status": "ok"}`. Compose services `db` (Postgres 16, port 5432, healthcheck, named volume `conduit_pgdata`) and `api` (built from `backend/`, port 8000, `depends_on: db: condition: service_healthy`).

- [ ] **Step 1: Scaffold backend package**

```bash
mkdir -p backend/app backend/tests
touch backend/app/__init__.py backend/tests/__init__.py
```

- [ ] **Step 2: Write `backend/pyproject.toml`**

```toml
[project]
name = "conduitai-backend"
version = "0.1.0"
description = "ConduitAI backend - AI-assisted customer data onboarding pipeline"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlmodel>=0.0.22",
    "psycopg[binary]>=3.2.0",
    "alembic>=1.14.0",
    "pydantic-settings>=2.6.0",
    "python-multipart>=0.0.12",
    "charset-normalizer>=3.4.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "httpx>=0.27.0",
    "faker>=33.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app", "seed"]
```

Note: `packages = ["app", "seed"]` references a `seed/` package that doesn't exist until Task 7 — that's fine, `uv sync` doesn't build the wheel, it only needs the `[project]`/`[dependency-groups]` tables to resolve dependencies during development.

- [ ] **Step 3: Run `uv sync` to create the venv and lockfile**

```bash
cd backend && uv sync
```

Expected: creates `backend/.venv/` and `backend/uv.lock` with no errors.

- [ ] **Step 4: Write `backend/app/main.py`**

```python
from fastapi import FastAPI

app = FastAPI(title="ConduitAI")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Write the failing test**

```python
# backend/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 6: Run the test**

```bash
cd backend && uv run pytest tests/test_health.py -v
```

Expected: PASS (this one isn't test-first since there's no behavior to get wrong yet, but confirm it actually runs).

- [ ] **Step 7: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /code

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY seed ./seed

RUN uv sync

ENV PATH="/code/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

Note: `alembic.ini`, `alembic/`, and `seed/` don't exist yet (Tasks 5 and 7). Create empty placeholders now so the Dockerfile build doesn't fail: `mkdir -p backend/alembic backend/seed && touch backend/alembic.ini backend/alembic/.gitkeep backend/seed/.gitkeep`. Tasks 5 and 7 will overwrite these.

- [ ] **Step 8: Write `backend/.dockerignore`**

```
.venv
__pycache__
*.pyc
tests
.pytest_cache
seed/output
```

- [ ] **Step 9: Write `docker-compose.yml` at the repo root**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: conduit
      POSTGRES_PASSWORD: conduit
      POSTGRES_DB: conduitai
    ports:
      - "5432:5432"
    volumes:
      - conduit_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U conduit -d conduitai"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    build:
      context: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+psycopg://conduit:conduit@db:5432/conduitai
      ENVIRONMENT: development
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./backend/app:/code/app
      - ./backend/alembic:/code/alembic

volumes:
  conduit_pgdata:
```

- [ ] **Step 10: Write/append repo-root `.gitignore`**

```
backend/.venv/
backend/__pycache__/
backend/**/__pycache__/
backend/.pytest_cache/
backend/seed/output/
.env
```

- [ ] **Step 11: Bring the stack up and verify**

```bash
docker compose up --build -d
sleep 3
curl -s http://localhost:8000/health
```

Expected: `docker compose ps` shows `db` as `healthy` and `api` as `running`; curl returns `{"status":"ok"}`. Leave the stack running — later tasks build on it.

- [ ] **Step 12: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app backend/tests \
        backend/Dockerfile backend/.dockerignore backend/alembic.ini \
        backend/alembic/.gitkeep backend/seed/.gitkeep \
        docker-compose.yml .gitignore
git commit -m "feat: bootstrap FastAPI backend with docker-compose and /health"
```

---

## Task 2: Settings and DB session

**Files:**
- Create: `backend/app/config.py`
- Create: `backend/app/db.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.config.Settings` (fields: `database_url: str`, `environment: str`, `default_tenant_id: str`), `app.config.settings` (module-level `Settings()` instance). `app.db.engine` (SQLAlchemy `Engine`), `app.db.get_session() -> Generator[Session, None, None]` (FastAPI dependency).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
from app.config import Settings


def test_settings_reads_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@z:5432/db")
    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://x:y@z:5432/db"


def test_settings_has_sane_defaults():
    settings = Settings()
    assert settings.default_tenant_id == "demo-tenant"
    assert settings.environment in {"development", "production", "test"}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conduit:conduit@localhost:5432/conduitai"
    environment: str = "development"
    default_tenant_id: str = "demo-tenant"


settings = Settings()
```

- [ ] **Step 4: Write `backend/app/db.py`**

```python
from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(settings.database_url, echo=False)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/db.py backend/tests/test_config.py
git commit -m "feat: add Settings and DB session"
```

---

## Task 3: Canonical field registry

**Files:**
- Create: `backend/app/canonical.py`
- Test: `backend/tests/test_canonical.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.canonical.FieldType` (str `Enum`), `app.canonical.CanonicalField` (Pydantic `BaseModel`: `name: str`, `target_table: str`, `target_column: str`, `type: FieldType`, `required: bool`, `description: str`, `aliases: list[str]`, `enum_values: list[str] | None`), `app.canonical.CANONICAL_FIELDS: list[CanonicalField]`, `app.canonical.get_field(name: str) -> CanonicalField` (raises `KeyError` if unknown), `app.canonical.fields_for_table(target_table: str) -> list[CanonicalField]`.

This is the registry Day 2/3 mapping will embed against and Day 4's transform engine will read to know which column to write. `target_column` is the literal SQLModel column name on the table named by `target_table`; for the three "linking" fields (`account_customer_natural_key`, `invoice_customer_natural_key`, `ticket_customer_natural_key`) `target_column` is `customer_id`, but writing it requires resolving the natural key to a `customers.id` first — that resolution logic belongs to Day 4, not here.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_canonical.py
import pytest

from app.canonical import CANONICAL_FIELDS, fields_for_table, get_field


def test_no_duplicate_field_names():
    names = [f.name for f in CANONICAL_FIELDS]
    assert len(names) == len(set(names))


def test_every_field_has_description_and_at_least_one_alias():
    for field in CANONICAL_FIELDS:
        assert field.description.strip() != ""
        assert len(field.aliases) >= 1


def test_get_field_returns_expected_field():
    field = get_field("customer_email")
    assert field.target_table == "customers"
    assert field.target_column == "email"
    assert "email" in field.aliases


def test_get_field_raises_for_unknown_name():
    with pytest.raises(KeyError):
        get_field("not_a_real_field")


def test_fields_for_table_filters_correctly():
    invoice_fields = fields_for_table("invoices")
    assert len(invoice_fields) > 0
    assert all(f.target_table == "invoices" for f in invoice_fields)


def test_registry_covers_all_four_canonical_tables():
    tables = {f.target_table for f in CANONICAL_FIELDS}
    assert tables == {"customers", "accounts", "invoices", "support_tickets"}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_canonical.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.canonical'`.

- [ ] **Step 3: Write `backend/app/canonical.py`**

```python
"""Canonical field registry.

Single source of truth for what a "canonical field" is: which table/column
it writes to, its type, whether it's required, a human-readable description
(the embedding text for Day 2/3 AI-assisted column mapping), and known
aliases (used by the deterministic fuzzy/alias matcher before any
embedding or LLM call happens). This module describes fields only — it
does not score, map, or transform anything.
"""

from enum import Enum

from pydantic import BaseModel


class FieldType(str, Enum):
    STRING = "string"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    INTEGER = "integer"
    MONEY_MINOR_UNITS = "money_minor_units"
    CURRENCY_CODE = "currency_code"


class CanonicalField(BaseModel):
    name: str
    target_table: str
    target_column: str
    type: FieldType
    required: bool
    description: str
    aliases: list[str] = []
    enum_values: list[str] | None = None


CANONICAL_FIELDS: list[CanonicalField] = [
    # --- customers ---
    CanonicalField(
        name="customer_natural_key",
        target_table="customers",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this customer, used to detect the same customer across re-uploads and across systems.",
        aliases=["customer_id", "cust_id", "custid", "client_ref", "client_id"],
    ),
    CanonicalField(
        name="legal_name",
        target_table="customers",
        target_column="legal_name",
        type=FieldType.STRING,
        required=True,
        description="The customer's full legal or registered company name.",
        aliases=["company_name", "name", "compname", "legal_name", "account_name"],
    ),
    CanonicalField(
        name="display_name",
        target_table="customers",
        target_column="display_name",
        type=FieldType.STRING,
        required=False,
        description="A shorter, human-friendly name for the customer used in UI display.",
        aliases=["display_name", "short_name", "dispname", "nickname"],
    ),
    CanonicalField(
        name="customer_email",
        target_table="customers",
        target_column="email",
        type=FieldType.EMAIL,
        required=False,
        description="Primary contact email address for the customer.",
        aliases=["email", "e-mail", "contact_email", "primary_email", "email_address"],
    ),
    CanonicalField(
        name="customer_phone",
        target_table="customers",
        target_column="phone",
        type=FieldType.PHONE,
        required=False,
        description="Primary contact phone number for the customer.",
        aliases=["phone", "phone_number", "contact_phone", "tel"],
    ),
    CanonicalField(
        name="country",
        target_table="customers",
        target_column="country",
        type=FieldType.STRING,
        required=False,
        description="The country the customer is based in, as a name or ISO code.",
        aliases=["country", "cntry", "country_code", "nation"],
    ),
    CanonicalField(
        name="industry",
        target_table="customers",
        target_column="industry",
        type=FieldType.STRING,
        required=False,
        description="The industry or business sector the customer operates in.",
        aliases=["industry", "ind_cd", "sector", "vertical"],
    ),
    CanonicalField(
        name="risk_tier",
        target_table="customers",
        target_column="risk_tier",
        type=FieldType.ENUM,
        required=False,
        description="The customer's assigned risk classification tier.",
        aliases=["risk_tier", "risk", "risk_level", "risk_class"],
        enum_values=["low", "medium", "high"],
    ),
    CanonicalField(
        name="customer_created_at",
        target_table="customers",
        target_column="created_at",
        type=FieldType.DATE,
        required=False,
        description="The date this customer record was first created in the source system.",
        aliases=["created_at", "dt_create", "date_created", "created_date", "signup_date"],
    ),
    CanonicalField(
        name="customer_vat_number",
        target_table="customers",
        target_column="vat_number",
        type=FieldType.STRING,
        required=False,
        description="The customer's VAT or tax registration number.",
        aliases=["vat_number", "vat_no", "vatno", "tax_id"],
    ),
    CanonicalField(
        name="customer_employee_count",
        target_table="customers",
        target_column="employee_count",
        type=FieldType.INTEGER,
        required=False,
        description="The approximate number of employees at the customer organization.",
        aliases=["employee_count", "num_employees", "headcount", "empct"],
    ),
    CanonicalField(
        name="customer_region",
        target_table="customers",
        target_column="region",
        type=FieldType.STRING,
        required=False,
        description="The sales or geographic region the customer is assigned to.",
        aliases=["region", "sales_region", "territory"],
    ),
    # --- accounts ---
    CanonicalField(
        name="account_customer_natural_key",
        target_table="accounts",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer this account belongs to; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="account_number",
        target_table="accounts",
        target_column="account_number",
        type=FieldType.STRING,
        required=True,
        description="The unique account number or identifier assigned to this account.",
        aliases=["account_number", "acct_no", "account_id"],
    ),
    CanonicalField(
        name="account_status",
        target_table="accounts",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current status of the account.",
        aliases=["status", "account_status", "acct_status"],
        enum_values=["active", "closed", "suspended"],
    ),
    CanonicalField(
        name="account_opened_at",
        target_table="accounts",
        target_column="opened_at",
        type=FieldType.DATE,
        required=False,
        description="The date the account was opened.",
        aliases=["opened_at", "open_date", "start_date"],
    ),
    # --- invoices ---
    CanonicalField(
        name="invoice_natural_key",
        target_table="invoices",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this invoice.",
        aliases=["invoice_number", "invoice_id", "inv_no", "invoice_ref"],
    ),
    CanonicalField(
        name="invoice_customer_natural_key",
        target_table="invoices",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer this invoice belongs to; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="invoice_amount_minor_units",
        target_table="invoices",
        target_column="amount_minor_units",
        type=FieldType.MONEY_MINOR_UNITS,
        required=True,
        description="The invoice amount, stored as an integer count of the currency's minor units (e.g. cents) to avoid floating point error.",
        aliases=["amount", "amt_usd", "amount_usd", "total", "invoice_amount"],
    ),
    CanonicalField(
        name="invoice_currency",
        target_table="invoices",
        target_column="currency",
        type=FieldType.CURRENCY_CODE,
        required=True,
        description="The ISO 4217 currency code the invoice amount is denominated in.",
        aliases=["currency", "curr", "ccy"],
    ),
    CanonicalField(
        name="invoice_issue_date",
        target_table="invoices",
        target_column="issue_date",
        type=FieldType.DATE,
        required=True,
        description="The date the invoice was issued.",
        aliases=["issue_date", "invoice_date", "date_issued"],
    ),
    CanonicalField(
        name="invoice_due_date",
        target_table="invoices",
        target_column="due_date",
        type=FieldType.DATE,
        required=False,
        description="The date payment for the invoice is due.",
        aliases=["due_date", "payment_due", "dt_due"],
    ),
    CanonicalField(
        name="invoice_status",
        target_table="invoices",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current payment status of the invoice.",
        aliases=["status", "invoice_status", "payment_status"],
        enum_values=["open", "paid", "void", "overdue"],
    ),
    CanonicalField(
        name="invoice_tax_amount_minor_units",
        target_table="invoices",
        target_column="tax_amount_minor_units",
        type=FieldType.MONEY_MINOR_UNITS,
        required=False,
        description="The tax amount charged on this invoice, stored as an integer count of the currency's minor units.",
        aliases=["tax_amount", "tax", "vat_amount"],
    ),
    CanonicalField(
        name="invoice_po_number",
        target_table="invoices",
        target_column="po_number",
        type=FieldType.STRING,
        required=False,
        description="The purchase order number the customer referenced for this invoice.",
        aliases=["po_number", "purchase_order", "po_ref"],
    ),
    # --- support tickets ---
    CanonicalField(
        name="ticket_natural_key",
        target_table="support_tickets",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this support ticket.",
        aliases=["ticket_ref", "ticket_id", "case_number"],
    ),
    CanonicalField(
        name="ticket_customer_natural_key",
        target_table="support_tickets",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer who raised this support ticket; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="ticket_subject",
        target_table="support_tickets",
        target_column="subject",
        type=FieldType.STRING,
        required=False,
        description="A short summary of what the support ticket is about.",
        aliases=["subject", "title", "summary"],
    ),
    CanonicalField(
        name="ticket_priority",
        target_table="support_tickets",
        target_column="priority",
        type=FieldType.ENUM,
        required=False,
        description="The urgency level assigned to the support ticket.",
        aliases=["priority", "severity"],
        enum_values=["low", "medium", "high", "urgent"],
    ),
    CanonicalField(
        name="ticket_status",
        target_table="support_tickets",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current status of the support ticket.",
        aliases=["status", "ticket_status"],
        enum_values=["open", "pending", "closed"],
    ),
    CanonicalField(
        name="ticket_opened_at",
        target_table="support_tickets",
        target_column="opened_at",
        type=FieldType.DATE,
        required=False,
        description="The date the support ticket was opened.",
        aliases=["opened_at", "open_date"],
    ),
    CanonicalField(
        name="ticket_closed_at",
        target_table="support_tickets",
        target_column="closed_at",
        type=FieldType.DATE,
        required=False,
        description="The date the support ticket was closed, if resolved.",
        aliases=["closed_at", "close_date", "resolved_at"],
    ),
]

_BY_NAME = {field.name: field for field in CANONICAL_FIELDS}


def get_field(name: str) -> CanonicalField:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown canonical field: {name}") from exc


def fields_for_table(target_table: str) -> list[CanonicalField]:
    return [f for f in CANONICAL_FIELDS if f.target_table == target_table]
```

Note: `customer_vat_number`, `customer_employee_count`, `customer_region`, `invoice_tax_amount_minor_units`, and `invoice_po_number` exist here specifically so Task 7's seed generator has enough genuinely-mapped columns to clear the 40-column benchmark floor with real headroom, not just barely. Task 4 must add matching nullable columns (`vat_number`, `employee_count`, `region` on `Customer`; `tax_amount_minor_units`, `po_number` on `Invoice`) or Task 5's autogenerate will drift from this registry.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_canonical.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/canonical.py backend/tests/test_canonical.py
git commit -m "feat: add canonical field registry"
```

---

## Task 4: Canonical SQLModel schema

**Files:**
- Create: `backend/app/models.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: SQLModel table classes registered on `SQLModel.metadata`: `Source`, `OnboardingBatch`, `RawRecord`, `ColumnProfile`, `MappingSpec`, `MappingReview`, `Quarantine`, `Customer`, `Account`, `Invoice`, `SupportTicket`. Table names: `sources`, `onboarding_batches`, `raw_records`, `column_profiles`, `mapping_specs`, `mapping_reviews`, `quarantine`, `customers`, `accounts`, `invoices`, `support_tickets`. `OnboardingBatch` has a `UNIQUE(tenant_id, source_id, file_hash)` constraint; `Customer`/`Invoice`/`SupportTicket` each have `UNIQUE(tenant_id, natural_key)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_models.py
def test_all_expected_tables_registered():
    import app.models  # noqa: F401
    from sqlmodel import SQLModel

    table_names = set(SQLModel.metadata.tables.keys())
    expected = {
        "sources", "onboarding_batches", "raw_records", "column_profiles",
        "mapping_specs", "mapping_reviews", "quarantine",
        "customers", "accounts", "invoices", "support_tickets",
    }
    assert expected.issubset(table_names)


def _unique_constraint_column_sets(table_name: str) -> set[tuple[str, ...]]:
    import app.models  # noqa: F401
    from sqlalchemy import UniqueConstraint
    from sqlmodel import SQLModel

    table = SQLModel.metadata.tables[table_name]
    return {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_customers_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("customers")


def test_invoices_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("invoices")


def test_support_tickets_has_unique_tenant_natural_key():
    assert ("tenant_id", "natural_key") in _unique_constraint_column_sets("support_tickets")


def test_onboarding_batches_has_unique_tenant_source_filehash():
    assert ("tenant_id", "source_id", "file_hash") in _unique_constraint_column_sets(
        "onboarding_batches"
    )
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`.

- [ ] **Step 3: Write `backend/app/models.py`**

```python
"""Canonical SQLModel schema.

Storage layer only — no business logic. `raw_records` is append-only and
never mutated. `customers`/`invoices`/`support_tickets` carry natural_key +
content_hash + a UNIQUE(tenant_id, natural_key) constraint from day one so
Day 4's idempotent upsert (`ON CONFLICT (tenant_id, natural_key) DO UPDATE
... WHERE content_hash <> excluded.content_hash`) has something to conflict
on without a follow-up migration.
"""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """`datetime.utcnow()` is deprecated as of Python 3.12 — this is its replacement."""
    return datetime.now(timezone.utc)


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    kind: str  # crm | billing | support


class OnboardingBatch(SQLModel, table=True):
    __tablename__ = "onboarding_batches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "file_hash", name="uq_batch_tenant_source_filehash"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    source_id: UUID = Field(foreign_key="sources.id")
    status: str = Field(default="queued")  # queued | running | succeeded | failed
    mapping_spec_version: int | None = None
    metrics_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    file_hash: str = Field(index=True)
    filename: str
    encoding: str | None = None
    delimiter: str | None = None
    row_count: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class RawRecord(SQLModel, table=True):
    __tablename__ = "raw_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    batch_id: UUID = Field(foreign_key="onboarding_batches.id", index=True)
    source_id: UUID = Field(foreign_key="sources.id")
    tenant_id: str = Field(index=True)
    row_index: int
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    content_hash: str
    created_at: datetime = Field(default_factory=_utcnow)


class ColumnProfile(SQLModel, table=True):
    __tablename__ = "column_profiles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    batch_id: UUID = Field(foreign_key="onboarding_batches.id", index=True)
    column_name: str
    profile_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))


class MappingSpec(SQLModel, table=True):
    __tablename__ = "mapping_specs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    source_id: UUID = Field(foreign_key="sources.id")
    version: int
    spec_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    status: str = Field(default="draft")  # draft | confirmed | superseded
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    parent_version: int | None = None


class MappingReview(SQLModel, table=True):
    __tablename__ = "mapping_reviews"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    mapping_spec_id: UUID = Field(foreign_key="mapping_specs.id", index=True)
    column_name: str
    proposed: str
    chosen: str
    confidence: float
    decided_by: str | None = None
    decided_at: datetime = Field(default_factory=_utcnow)


class Quarantine(SQLModel, table=True):
    __tablename__ = "quarantine"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    batch_id: UUID = Field(foreign_key="onboarding_batches.id", index=True)
    raw_record_json: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    error_codes: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(String)))
    severity: str  # error | warning | info
    explanation: str | None = None
    suggested_fix: str | None = None
    status: str = Field(default="open")  # open | fixed | ignored | resubmitted


class Customer(SQLModel, table=True):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "natural_key", name="uq_customers_tenant_naturalkey"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    natural_key: str
    legal_name: str
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    industry: str | None = None
    risk_tier: str | None = None
    vat_number: str | None = None
    employee_count: int | None = None
    region: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    content_hash: str
    source_batch_id: UUID = Field(foreign_key="onboarding_batches.id")


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    customer_id: UUID = Field(foreign_key="customers.id")
    account_number: str
    status: str | None = None
    opened_at: date | None = None


class Invoice(SQLModel, table=True):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "natural_key", name="uq_invoices_tenant_naturalkey"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    natural_key: str
    customer_id: UUID = Field(foreign_key="customers.id")
    account_id: UUID | None = Field(default=None, foreign_key="accounts.id")
    invoice_number: str
    amount_minor_units: int
    currency: str
    issue_date: date
    due_date: date | None = None
    status: str = Field(default="open")
    tax_amount_minor_units: int | None = None
    po_number: str | None = None
    content_hash: str
    source_batch_id: UUID = Field(foreign_key="onboarding_batches.id")


class SupportTicket(SQLModel, table=True):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "natural_key", name="uq_tickets_tenant_naturalkey"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(index=True)
    natural_key: str
    customer_id: UUID = Field(foreign_key="customers.id")
    ticket_ref: str
    subject: str | None = None
    priority: str | None = None
    status: str | None = None
    opened_at: date | None = None
    closed_at: date | None = None
    content_hash: str
    source_batch_id: UUID = Field(foreign_key="onboarding_batches.id")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_models.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py
git commit -m "feat: add canonical SQLModel schema"
```

---

## Task 5: Alembic wiring + initial migration

**Files:**
- Create: `backend/alembic.ini` (overwrite placeholder)
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/<generated>_initial_canonical_schema.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `app.config.settings` (Task 2), `app.models` — all table classes (Task 4).
- Produces: a live Postgres schema with all 11 tables + the 4 unique constraints, reachable at `settings.database_url`.

- [ ] **Step 1: Initialize Alembic**

```bash
cd backend && rm alembic.ini && rm -rf alembic && uv run alembic init alembic
```

This regenerates `alembic.ini` and `alembic/` (overwriting the Task 1 placeholders) with Alembic's default templates, which Steps 2–3 then edit.

- [ ] **Step 2: Overwrite `backend/alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.config import settings
from app.models import (  # noqa: F401  (import registers tables on SQLModel.metadata)
    Account,
    ColumnProfile,
    Customer,
    Invoice,
    MappingReview,
    MappingSpec,
    OnboardingBatch,
    Quarantine,
    RawRecord,
    Source,
    SupportTicket,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

This fixes gotcha #1: `alembic/env.py` must import the models module (even though the names are unused) or `SQLModel.metadata` stays empty and autogenerate produces an empty migration.

- [ ] **Step 3: Overwrite `backend/alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

This fixes gotcha #2: adding the static `import sqlmodel` line means every future autogenerated migration compiles, since SQLModel emits `sqlmodel.sql.sqltypes.AutoString` column-type references that Alembic's automatic import-detection doesn't know to add on its own. (`${imports}` is left in place — Alembic still auto-adds `from sqlalchemy.dialects import postgresql` for the JSONB/ARRAY columns.)

- [ ] **Step 4: Generate the initial migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "initial canonical schema"
```

- [ ] **Step 5: Inspect the generated file**

Open `backend/alembic/versions/<hash>_initial_canonical_schema.py` and confirm:
- `import sqlmodel` is present at the top.
- `op.create_table(...)` appears once for each of the 11 tables.
- The `customers`, `invoices`, `support_tickets` tables each include a `sa.UniqueConstraint("tenant_id", "natural_key", ...)`.
- The `onboarding_batches` table includes `sa.UniqueConstraint("tenant_id", "source_id", "file_hash", ...)`.
- JSONB columns render as `postgresql.JSONB(astext_type=sa.Text())` and the ARRAY column on `quarantine` renders as `postgresql.ARRAY(sa.String())`.

If any of these are missing, gotcha #3 has bitten you: a plain `dict` field without `sa_column=Column(JSONB)` renders as generic `sa.JSON()`, not `postgresql.JSONB`. Go back to `app/models.py` and check every JSONB-intended column uses `sa_column=Column(JSONB)` explicitly, then delete the generated migration file and re-run Step 4.

- [ ] **Step 6: Apply the migration**

```bash
docker compose up -d db
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run alembic upgrade head
```

Expected: `Running upgrade -> <hash>, initial canonical schema` with no errors.

- [ ] **Step 7: Write the verification test**

```python
# backend/tests/test_migrations.py
from sqlalchemy import create_engine, inspect

from app.config import settings

EXPECTED_TABLES = {
    "sources", "onboarding_batches", "raw_records", "column_profiles",
    "mapping_specs", "mapping_reviews", "quarantine",
    "customers", "accounts", "invoices", "support_tickets",
    "alembic_version",
}


def test_alembic_upgrade_creates_all_canonical_tables():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))


def test_customers_unique_constraint_exists_in_db():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    col_sets = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("customers")
    }
    assert ("tenant_id", "natural_key") in col_sets


def test_onboarding_batches_unique_constraint_exists_in_db():
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    col_sets = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("onboarding_batches")
    }
    assert ("tenant_id", "source_id", "file_hash") in col_sets
```

This is an integration test that requires the `db` service reachable at `settings.database_url` (defaults to `localhost:5432`, matching the port Compose exposes to the host) — run it from the host with `docker compose up -d db` already running, not inside the `api` container.

- [ ] **Step 8: Run it to verify it passes**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_migrations.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 9: Add a Docker entrypoint so the container migrates itself on startup**

`docker compose up` alone does not run `alembic upgrade head` anywhere — Steps 1–8 ran it by hand from the host. Without this step, a genuinely clean checkout (`git clone` + `docker compose up`) gets a running API pointed at an empty database. Fix it with an entrypoint script so the container always migrates before it serves traffic.

Write `backend/docker-entrypoint.sh`:

```bash
#!/bin/sh
set -e

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
chmod +x backend/docker-entrypoint.sh
```

Modify `backend/Dockerfile` (from Task 1) to copy it in, make it executable in the image, and run it instead of `uvicorn` directly:

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /code

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY seed ./seed
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN uv sync && chmod +x docker-entrypoint.sh

ENV PATH="/code/.venv/bin:$PATH"

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
```

- [ ] **Step 10: Prove the one-command story from a truly clean state**

```bash
docker compose down -v
docker compose up --build -d
sleep 5
curl -s http://localhost:8000/health
```

`down -v` deletes the `conduit_pgdata` volume too, so this is the closest local approximation of a fresh `git clone` + `docker compose up`. Expected: `{"status":"ok"}`, and the entrypoint's `alembic upgrade head` log line appears in `docker compose logs api`. Confirm the schema actually landed without any host-side `alembic` invocation:

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_migrations.py -v
```

Expected: PASS (3 tests) — proving the tables exist purely because the container migrated itself, not because of the manual `alembic upgrade head` run in Step 6.

- [ ] **Step 11: Commit**

```bash
git add backend/alembic.ini backend/alembic backend/tests/test_migrations.py \
        backend/Dockerfile backend/docker-entrypoint.sh
git commit -m "feat: wire up Alembic, add initial canonical schema migration, and auto-migrate on container start"
```

---

## Task 6: `POST /upload` with file-hash idempotency

**Files:**
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/upload.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/fixtures/crm_tiny.csv`
- Create: `backend/tests/fixtures/billing_tiny.csv`
- Create: `backend/tests/fixtures/support_tiny.csv`
- Test: `backend/tests/test_upload.py`

**Interfaces:**
- Consumes: `app.db.get_session` (Task 2), `app.models.Source`, `app.models.OnboardingBatch`, `app.models.RawRecord` (Task 4).
- Produces: `POST /upload` (multipart form: `file`, `tenant_id`, `source_name`, `source_kind`) → `201 {"batch_id": str, "row_count": int, "idempotent": false}` on first upload, `200 {"batch_id": str, "row_count": int, "idempotent": true}` on a byte-identical re-upload for the same `(tenant_id, source_id)`, `400` on an empty file. Also produces the `unique_tenant_id` pytest fixture (`backend/tests/conftest.py`) — every later task that writes a DB-touching test should use it instead of a hardcoded tenant string, or the suite stops being re-runnable.

- [ ] **Step 1: Write `backend/tests/conftest.py`**

Without this, a hardcoded `tenant_id` like `"test-upload-basic"` makes the test pass exactly once — the second `pytest` run finds the batch from the first run already sitting in Postgres, gets `idempotent: true` back instead of a fresh `201`, and fails. A fresh UUID-suffixed tenant per test keeps every DB-touching test run repeatable without needing to truncate tables between runs.

```python
# backend/tests/conftest.py
from uuid import uuid4

import pytest


@pytest.fixture
def unique_tenant_id() -> str:
    return f"test-tenant-{uuid4()}"
```

- [ ] **Step 2: Hand-write the tiny test fixtures**

```csv
# backend/tests/fixtures/crm_tiny.csv
customer_id,first_name,last_name,email
C-1001,Ada,Lovelace,ada@example.com
C-1002,Grace,Hopper,grace@example.com
C-1003,Alan,Turing,alan@example.com
C-1004,Katherine,Johnson,katherine@example.com
C-1005,Margaret,Hamilton,margaret@example.com
```

```csv
# backend/tests/fixtures/billing_tiny.csv
customer_id,invoice_number,amount,currency,issue_date
1001,INV-1,100.00,USD,2026-01-01
1002,INV-2,200.00,USD,2026-01-02
1003,INV-3,300.00,USD,2026-01-03
1004,INV-4,400.00,USD,2026-01-04
1005,INV-5,500.00,USD,2026-01-05
```

```csv
# backend/tests/fixtures/support_tiny.csv
customer_id,ticket_ref,subject,status
1001,TCK-1,Login issue,open
1002,TCK-2,Billing question,closed
1003,TCK-3,Feature request,open
1004,TCK-4,Password reset,closed
1005,TCK-5,API access,open
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_upload.py
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import OnboardingBatch, RawRecord

client = TestClient(app)


def _upload_tiny_crm(tenant_id: str):
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()
    return client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )


def test_upload_stores_raw_records_and_creates_batch(unique_tenant_id):
    response = _upload_tiny_crm(unique_tenant_id)
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 5
    assert body["idempotent"] is False

    with Session(engine) as session:
        batch = session.get(OnboardingBatch, UUID(body["batch_id"]))
        assert batch is not None
        assert batch.filename == "crm_tiny.csv"
        raw_rows = session.exec(
            select(RawRecord).where(RawRecord.batch_id == batch.id)
        ).all()
        assert len(raw_rows) == 5
        assert raw_rows[0].raw_json["customer_id"] == "C-1001"


def test_uploading_same_file_twice_is_idempotent(unique_tenant_id):
    first = _upload_tiny_crm(unique_tenant_id)
    second = _upload_tiny_crm(unique_tenant_id)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    assert first.json()["batch_id"] == second.json()["batch_id"]

    with Session(engine) as session:
        batches = session.exec(
            select(OnboardingBatch).where(OnboardingBatch.tenant_id == unique_tenant_id)
        ).all()
        assert len(batches) == 1


def test_uploading_empty_file_returns_400(unique_tenant_id):
    response = client.post(
        "/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 400
```

Every test above takes `unique_tenant_id` as a parameter — pytest injects it automatically from the `conftest.py` fixture in Step 1, no import needed. Run the whole file twice in a row once it's green (Step 7) to confirm this actually fixes re-runnability: two consecutive full-suite runs must both pass.

- [ ] **Step 4: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_upload.py -v
```

Expected: FAIL — `404` on `/upload` (route doesn't exist yet).

- [ ] **Step 5: Create `backend/app/routers/__init__.py`** (empty file)

- [ ] **Step 6: Write `backend/app/routers/upload.py`**

```python
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
```

Note the `response: Response` parameter: FastAPI defaults a route's success status to `200` once no single `status_code` is fixed at the decorator level, so both branches set `response.status_code` explicitly — `200` for the idempotent replay, `201` for a genuinely new batch.

- [ ] **Step 7: Wire the router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 8: Run the tests to verify they pass, twice in a row**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_upload.py -v
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_upload.py -v
```

Expected: PASS (3 tests) on **both** runs — this is the actual regression test for re-runnability, not just a single green run.

- [ ] **Step 9: Run the full test suite so far**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: all tests across Tasks 1–6 PASS.

- [ ] **Step 10: Rebuild and manually verify via curl**

```bash
docker compose up --build -d
curl -s -F "file=@backend/tests/fixtures/crm_tiny.csv;type=text/csv" \
     -F "tenant_id=demo" -F "source_name=crm" -F "source_kind=crm" \
     http://localhost:8000/upload
```

Expected: `{"batch_id": "...", "row_count": 5, "idempotent": false}`. Running the same curl again returns `"idempotent": true` with the same `batch_id`.

- [ ] **Step 11: Commit**

```bash
git add backend/app/routers backend/app/main.py backend/tests/conftest.py \
        backend/tests/fixtures backend/tests/test_upload.py
git commit -m "feat: add idempotent POST /upload endpoint"
```

---

## Task 7: Deterministic seed-data generator + `ground_truth.json`

**Files:**
- Create: `backend/seed/__init__.py`
- Create: `backend/seed/generate_seed_data.py`
- Test: `backend/tests/test_seed_generator.py`

**Interfaces:**
- Consumes: `app.canonical.CANONICAL_FIELDS` (Task 3) — every non-null value in the generator's column maps must be a real `CanonicalField.name`.
- Produces: `seed.generate_seed_data.generate() -> None` (writes to `seed.generate_seed_data.OUTPUT_DIR`), files `crm_snake.csv`, `crm_legacy.csv`, `billing.csv`, `support.csv`, `ground_truth.json` in `backend/seed/output/`. `ground_truth.json` shape: `{"column_mappings": {filename: {source_column: canonical_name_or_null}}, "expected_defects": {filename: {row_index_str: [error_code, ...]}}}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_seed_generator.py
import csv
import json
import random

from faker import Faker

from seed.generate_seed_data import OUTPUT_DIR, generate

EXPECTED_FILES = {"crm_snake.csv", "crm_legacy.csv", "billing.csv", "support.csv"}


def test_generation_is_deterministic_across_runs():
    generate()
    first_ground_truth = (OUTPUT_DIR / "ground_truth.json").read_text()
    first_crm = (OUTPUT_DIR / "crm_snake.csv").read_text()

    random.seed(42)
    Faker.seed(42)
    generate()

    assert (OUTPUT_DIR / "ground_truth.json").read_text() == first_ground_truth
    assert (OUTPUT_DIR / "crm_snake.csv").read_text() == first_crm


def test_all_expected_files_are_written():
    generate()
    for filename in EXPECTED_FILES:
        assert (OUTPUT_DIR / filename).exists()
    assert (OUTPUT_DIR / "ground_truth.json").exists()


def test_ground_truth_has_both_sections():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    assert set(ground_truth["column_mappings"].keys()) == EXPECTED_FILES
    assert set(ground_truth["expected_defects"].keys()) == {
        "crm_snake.csv", "billing.csv", "support.csv",
    }


def test_benchmark_has_at_least_40_mapped_columns():
    # Total column count is a weaker number than it looks — a chunk of every
    # file is deliberately unmapped (None) to test that the mapper abstains
    # correctly. What Day 3's accuracy@1 is actually computed over is the
    # *mapped* columns, so that's what must clear the 40-column floor.
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    mapped_columns = sum(
        1
        for mapping in ground_truth["column_mappings"].values()
        for canonical_name in mapping.values()
        if canonical_name is not None
    )
    assert mapped_columns >= 40


def test_all_mapped_canonical_names_are_real_fields():
    from app.canonical import CANONICAL_FIELDS

    valid_names = {f.name for f in CANONICAL_FIELDS}
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    for mapping in ground_truth["column_mappings"].values():
        for canonical_name in mapping.values():
            if canonical_name is not None:
                assert canonical_name in valid_names


def test_crm_snake_and_crm_legacy_share_row_values_under_different_headers():
    generate()
    with (OUTPUT_DIR / "crm_snake.csv").open() as f:
        snake_rows = list(csv.DictReader(f))
    with (OUTPUT_DIR / "crm_legacy.csv").open() as f:
        legacy_rows = list(csv.DictReader(f))

    assert len(snake_rows) == len(legacy_rows)
    assert snake_rows[0]["company_name"] == legacy_rows[0]["COMPNAME"]


def test_expected_defects_reference_valid_row_indices():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    for filename, defects in ground_truth["expected_defects"].items():
        with (OUTPUT_DIR / filename).open() as f:
            row_count = sum(1 for _ in csv.DictReader(f))
        for row_index_str in defects:
            assert int(row_index_str) < row_count
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_seed_generator.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seed'`.

- [ ] **Step 3: Create `backend/seed/__init__.py`** (empty file)

- [ ] **Step 4: Write `backend/seed/generate_seed_data.py`**

```python
"""Deterministic seed-data generator for ConduitAI.

Produces four deliberately messy CSV exports (crm_snake, crm_legacy,
billing, support) plus ground_truth.json documenting (a) the true
source-column -> canonical-field mapping for every column across all
four files (the Day 3 mapping-accuracy benchmark), and (b) the row-level
defects deliberately injected (the Day 4/5 validation-coverage and
correction-rate benchmark).

Deterministic: random.seed(SEED) and Faker.seed(SEED) are set before every
call to generate(), so re-running produces byte-identical output and
accuracy/coverage numbers are comparable run over run.
"""

import csv
import json
import random
from pathlib import Path

from faker import Faker

from app.canonical import CANONICAL_FIELDS

SEED = 42
NUM_CUSTOMERS = 30
OUTPUT_DIR = Path(__file__).parent / "output"

COUNTRIES = ["United States", "United Kingdom", "Germany", "India", "Canada"]
INDUSTRIES = ["Finance", "Healthcare", "Retail", "Manufacturing", "Technology"]
RISK_TIERS = ["low", "medium", "high"]
REGIONS = ["NA", "EMEA", "APAC"]
INVOICE_STATUSES = ["open", "paid", "void", "overdue"]
TICKET_PRIORITIES = ["low", "medium", "high", "urgent"]
TICKET_STATUSES = ["open", "pending", "closed"]

# source_column -> canonical field name, or None if deliberately unmapped
CRM_COLUMN_MAP = {
    "customer_id": "customer_natural_key",
    "company_name": "legal_name",
    "display_name": "display_name",
    "email": "customer_email",
    "phone": "customer_phone",
    "country": "country",
    "industry": "industry",
    "risk_tier": "risk_tier",
    "created_at": "customer_created_at",
    "notes": None,
    "vat_number": "customer_vat_number",
    "employee_count": "customer_employee_count",
    "region": "customer_region",
}
CRM_LEGACY_HEADER_RENAME = {
    "customer_id": "CustID",
    "company_name": "COMPNAME",
    "display_name": "DISPNAME",
    "email": "EMAIL",
    "phone": "PHONE",
    "country": "CNTRY",
    "industry": "IND_CD",
    "risk_tier": "RISK",
    "created_at": "DT_CREATE",
    "notes": "NOTES",
    "vat_number": "VATNO",
    "employee_count": "EMPCT",
    "region": "REGION",
}
BILLING_COLUMN_MAP = {
    "customer_id": "invoice_customer_natural_key",
    "invoice_number": "invoice_natural_key",
    "amount": "invoice_amount_minor_units",
    "currency": "invoice_currency",
    "issue_date": "invoice_issue_date",
    "due_date": "invoice_due_date",
    "status": "invoice_status",
    "account_number": "account_number",
    "payment_method": None,
    "memo": None,
    "tax_amount": "invoice_tax_amount_minor_units",
    "po_number": "invoice_po_number",
}
SUPPORT_COLUMN_MAP = {
    "customer_id": "ticket_customer_natural_key",
    "ticket_ref": "ticket_natural_key",
    "subject": "ticket_subject",
    "priority": "ticket_priority",
    "status": "ticket_status",
    "opened_at": "ticket_opened_at",
    "closed_at": "ticket_closed_at",
    "channel": None,
    "assignee": None,
    "account_number": "account_number",
}


def _validate_column_map(column_map: dict) -> None:
    valid_names = {f.name for f in CANONICAL_FIELDS}
    for source_col, canonical_name in column_map.items():
        if canonical_name is not None and canonical_name not in valid_names:
            raise ValueError(
                f"Column '{source_col}' maps to unknown canonical field '{canonical_name}'"
            )


def _build_customers(fake: Faker) -> list[dict]:
    customers = []
    for i in range(NUM_CUSTOMERS):
        internal_id = 1001 + i
        customers.append(
            {
                "crm_id": f"C-{internal_id}",
                "billing_id": str(internal_id),
                "company_name": fake.company(),
                "display_name": fake.company_suffix(),
                "email": fake.company_email(),
                "phone": fake.phone_number(),
                "country": random.choice(COUNTRIES),
                "industry": random.choice(INDUSTRIES),
                "risk_tier": random.choice(RISK_TIERS),
                "created_at": fake.date_between(start_date="-3y", end_date="-1y"),
                "account_number": f"ACC-{internal_id}",
                "vat_number": f"VAT{random.randint(100000, 999999)}",
                "employee_count": random.randint(5, 5000),
                "region": random.choice(REGIONS),
            }
        )
    return customers


def _write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_crm_rows(customers: list[dict]) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["crm_id"],
            "company_name": c["company_name"],
            "display_name": c["display_name"],
            "email": c["email"],
            "phone": c["phone"],
            "country": c["country"],
            "industry": c["industry"],
            "risk_tier": c["risk_tier"],
            "created_at": c["created_at"].isoformat(),
            "notes": "",
            "vat_number": c["vat_number"],
            "employee_count": str(c["employee_count"]),
            "region": c["region"],
        }
        for c in customers
    ]
    defects: dict[str, list[str]] = {}

    rows[2]["email"] = ""
    defects["2"] = ["MISSING_REQUIRED"]

    dup = dict(rows[0])
    dup["customer_id"] = "C-9001"
    dup["company_name"] = rows[0]["company_name"] + " Ltd"
    rows.append(dup)
    defects[str(len(rows) - 1)] = ["DUPLICATE_FUZZY"]

    rows[5]["created_at"] = "03/04/2025"
    defects["5"] = ["DATE_AMBIGUOUS"]

    rows[7]["company_name"] = "Café Système " + rows[7]["company_name"]

    return rows, defects


def _build_billing_rows(customers: list[dict], fake: Faker) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["billing_id"],
            "invoice_number": f"INV-{2000 + idx}",
            "amount": f"{random.uniform(100, 5000):.2f}",
            "currency": "USD",
            "issue_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "due_date": fake.date_between(start_date="today", end_date="+30d").isoformat(),
            "status": random.choice(INVOICE_STATUSES),
            "account_number": c["account_number"],
            "payment_method": random.choice(["wire", "card", "ach"]),
            "memo": "",
            "tax_amount": f"{random.uniform(5, 400):.2f}",
            "po_number": f"PO-{4000 + idx}",
        }
        for idx, c in enumerate(customers)
    ]
    defects: dict[str, list[str]] = {}

    rows[3]["amount"] = "-450.00"
    defects["3"] = ["BUSINESS_RULE_NEGATIVE_AMOUNT"]

    rows[6]["issue_date"] = "2099-01-01"
    defects["6"] = ["BUSINESS_RULE_FUTURE_DATE"]

    rows[8]["issue_date"] = "03/04/2025"
    defects["8"] = ["DATE_AMBIGUOUS"]

    rows[10]["amount"] = "$1,234.56"

    rows.append(
        {
            "customer_id": "9999",
            "invoice_number": "INV-9999",
            "amount": "775.00",
            "currency": "USD",
            "issue_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "due_date": fake.date_between(start_date="today", end_date="+30d").isoformat(),
            "status": "open",
            "account_number": "ACC-9999",
            "payment_method": "wire",
            "memo": "",
            "tax_amount": "62.00",
            "po_number": "PO-9999",
        }
    )
    defects[str(len(rows) - 1)] = ["REF_INTEGRITY_ORPHAN_FK"]

    return rows, defects


def _build_support_rows(customers: list[dict], fake: Faker) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["billing_id"],
            "ticket_ref": f"TCK-{3000 + idx}",
            "subject": fake.sentence(nb_words=6),
            "priority": random.choice(TICKET_PRIORITIES),
            "status": random.choice(TICKET_STATUSES),
            "opened_at": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "closed_at": "",
            "channel": random.choice(["email", "phone", "chat"]),
            "assignee": fake.first_name(),
            "account_number": c["account_number"],
        }
        for idx, c in enumerate(customers)
    ]
    defects: dict[str, list[str]] = {}

    rows[4]["customer_id"] = ""
    defects["4"] = ["MISSING_REQUIRED"]

    rows.append(dict(rows[1]))
    defects[str(len(rows) - 1)] = ["DUPLICATE_EXACT"]

    return rows, defects


def generate() -> None:
    for column_map in (CRM_COLUMN_MAP, BILLING_COLUMN_MAP, SUPPORT_COLUMN_MAP):
        _validate_column_map(column_map)

    random.seed(SEED)
    Faker.seed(SEED)
    fake = Faker()

    customers = _build_customers(fake)

    crm_rows, crm_defects = _build_crm_rows(customers)
    crm_headers = list(CRM_COLUMN_MAP.keys())
    _write_csv(OUTPUT_DIR / "crm_snake.csv", crm_headers, crm_rows)

    legacy_headers = [CRM_LEGACY_HEADER_RENAME[h] for h in crm_headers]
    legacy_rows = [
        {CRM_LEGACY_HEADER_RENAME[k]: v for k, v in row.items()} for row in crm_rows
    ]
    _write_csv(OUTPUT_DIR / "crm_legacy.csv", legacy_headers, legacy_rows)

    billing_rows, billing_defects = _build_billing_rows(customers, fake)
    _write_csv(OUTPUT_DIR / "billing.csv", list(BILLING_COLUMN_MAP.keys()), billing_rows)

    support_rows, support_defects = _build_support_rows(customers, fake)
    _write_csv(OUTPUT_DIR / "support.csv", list(SUPPORT_COLUMN_MAP.keys()), support_rows)

    ground_truth = {
        "column_mappings": {
            "crm_snake.csv": CRM_COLUMN_MAP,
            "crm_legacy.csv": {
                CRM_LEGACY_HEADER_RENAME[k]: v for k, v in CRM_COLUMN_MAP.items()
            },
            "billing.csv": BILLING_COLUMN_MAP,
            "support.csv": SUPPORT_COLUMN_MAP,
        },
        "expected_defects": {
            "crm_snake.csv": crm_defects,
            "billing.csv": billing_defects,
            "support.csv": support_defects,
        },
    }
    (OUTPUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))

    total_columns = sum(len(m) for m in ground_truth["column_mappings"].values())
    print(
        f"Wrote {len(crm_rows)} CRM rows, {len(billing_rows)} billing rows, "
        f"{len(support_rows)} support rows to {OUTPUT_DIR}"
    )
    print(
        f"Labeled benchmark size: {total_columns} columns across "
        f"{len(ground_truth['column_mappings'])} files"
    )


if __name__ == "__main__":
    generate()
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_seed_generator.py -v
```

Expected: PASS (7 tests). This should print ~48 total columns / ~42 mapped — comfortably above the 40-mapped-column floor the benchmark test enforces, not sitting exactly on it. Confirm the printed benchmark size in a manual run:

```bash
cd backend && uv run python -m seed.generate_seed_data
```

- [ ] **Step 6: Run the entire Day 1 test suite one final time**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: every test from Tasks 1–7 passes.

- [ ] **Step 7: Commit**

```bash
git add backend/seed backend/tests/test_seed_generator.py
git commit -m "feat: add deterministic seed-data generator with labeled mapping benchmark"
```

---

## Day 1 Definition of Done

- `docker compose up --build` from a **truly clean checkout** (`down -v` first, or a fresh clone) brings up `db` (healthy) and `api` (running), with the container's own entrypoint running `alembic upgrade head` before `uvicorn` starts — no host-side `alembic` command required.
- `GET /health` → `{"status": "ok"}`.
- The initial migration creates all 11 canonical tables with the required unique constraints.
- `POST /upload` with `tests/fixtures/crm_tiny.csv` persists 5 `raw_records` rows and one `onboarding_batches` row; repeating the identical upload returns `idempotent: true` and creates no new batch.
- `python -m seed.generate_seed_data` deterministically writes 4 CSVs + `ground_truth.json` with ≥ 40 **mapped** columns (not just ≥ 40 total columns — a meaningful fraction are deliberately unmapped) and both a `column_mappings` and an `expected_defects` section, every mapped name resolving against `app.canonical.CANONICAL_FIELDS`.
- Full `pytest` suite (config, canonical, models, migrations, upload, seed generator) passes — and passes again on a **second consecutive run** with no manual cleanup in between, because every DB-touching test uses a fresh `unique_tenant_id` per test.
- No `DeprecationWarning` from `datetime.utcnow()` anywhere in the test output.
