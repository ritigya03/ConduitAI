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


class CustomerDuplicateCandidate(SQLModel, table=True):
    """A probable-duplicate customer pair found by app.dedupe's Splink
    pass -- "same customer, different spelling" (Day 6). Separate from
    Quarantine: this flags two already-*loaded* customers as probably
    the same entity, not a single row that failed to load."""

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
    # dedupe pass upserts into the same row instead of creating a
    # mirror-image duplicate every time.
    #
    # Deliberately NOT a DB-enforced foreign_key: a "merged" resolution
    # deletes the loser Customer row, and this record is meant to survive
    # as a permanent audit trail of that decision -- a hard FK would make
    # that delete impossible (found by running the merge test for real).
    customer_id_a: UUID
    customer_id_b: UUID
    match_probability: float
    status: str = Field(default="open")  # open | merged | dismissed
    created_at: datetime = Field(default_factory=_utcnow)


class IdempotencyKey(SQLModel, table=True):
    """Stores a completed response per (tenant, endpoint, client-supplied
    key) so a retried request replays the original result instead of
    reprocessing -- the Stripe-style Idempotency-Key pattern (Day 6).
    Complementary to the content-hash idempotency `OnboardingBatch`
    already has: this guards against a *retried request*, not duplicate
    *content*."""

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
    kyc_status: str
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
