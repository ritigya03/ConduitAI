"""Loads a batch: the only module in this plan that touches the
database. Resolves the "linking" customer key, checks referential
integrity and within-batch exact duplicates, upserts valid rows
idempotently, and quarantines the rest — a whole raw row at a time.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.models import (
    Account,
    Customer,
    Invoice,
    MappingSpec,
    OnboardingBatch,
    Quarantine,
    RawRecord,
    SupportTicket,
)
from app.record_builder import apply_spec_to_row
from app.validation import validate_business_rules, validate_schema

_LINKED_TABLES = {"accounts", "invoices", "support_tickets"}
_NATURAL_KEY_MODELS = {"customers": Customer, "invoices": Invoice, "support_tickets": SupportTicket}

# Invoice.invoice_number and SupportTicket.ticket_ref are required columns
# with no canonical field targeting them (only "natural_key" is) — see
# this plan's Global Constraints. Mirror natural_key into them on upsert.
_MIRROR_NATURAL_KEY_COLUMN = {Invoice: "invoice_number", SupportTicket: "ticket_ref"}


class NoConfirmedMappingSpec(Exception):
    """No confirmed mapping spec exists for this batch's source."""


class LoadSummary(BaseModel):
    batch_id: str
    mapping_spec_version: int
    total_rows: int
    loaded: int
    quarantined: int


def _content_hash(values: dict) -> str:
    canonical = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_confirmed_spec(session: Session, tenant_id: str, source_id: UUID) -> MappingSpec | None:
    return session.exec(
        select(MappingSpec).where(
            MappingSpec.tenant_id == tenant_id,
            MappingSpec.source_id == source_id,
            MappingSpec.status == "confirmed",
        )
    ).first()


def _resolve_customer_id(session: Session, tenant_id: str, natural_key: str | None) -> UUID | None:
    """Resolves a customer link, trying an exact natural_key match first,
    then a digit-only normalized match. The normalization fallback exists
    because source systems genuinely format the same customer's ID
    differently — this seed data's CRM file uses "C-1011", its billing
    and support files use plain "1011" for the identical customer. This
    is a fixed, deterministic parsing rule, not probabilistic dedupe
    (Splink, Day 6) — without it, every cross-system row would spuriously
    orphan, which defeats this plan's own "valid rows load" deliverable."""
    if natural_key is None:
        return None
    customer = session.exec(
        select(Customer).where(Customer.tenant_id == tenant_id, Customer.natural_key == natural_key)
    ).first()
    if customer is not None:
        return customer.id

    normalized_key = re.sub(r"\D", "", natural_key)
    if not normalized_key:
        return None
    for existing in session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all():
        if re.sub(r"\D", "", existing.natural_key) == normalized_key:
            return existing.id
    return None


def _suggest_customer_fix(session: Session, tenant_id: str, attempted_key: str) -> str | None:
    """Best-effort suggestion for a genuinely orphan customer FK (one that
    didn't resolve even after _resolve_customer_id's normalization
    fallback) — a rapidfuzz match against existing natural keys."""
    customers = session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all()

    best_match, best_score = None, 0.0
    for customer in customers:
        score = fuzz.ratio(attempted_key, customer.natural_key)
        if score > best_score:
            best_match, best_score = customer.natural_key, score
    if best_match is not None and best_score >= 85:
        return f"Map to existing customer '{best_match}' ({best_score:.0f}% match)"
    return None


def _upsert_customer(session: Session, tenant_id: str, values: dict, batch_id: UUID) -> None:
    content_hash = _content_hash(values)
    insert_values = {
        **values,
        "tenant_id": tenant_id,
        "content_hash": content_hash,
        "source_batch_id": batch_id,
    }
    stmt = pg_insert(Customer).values(**insert_values)
    update_values = {k: v for k, v in insert_values.items() if k not in ("tenant_id", "natural_key")}
    update_values["updated_at"] = datetime.now(timezone.utc)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "natural_key"],
        set_=update_values,
        where=stmt.excluded.content_hash != Customer.content_hash,
    )
    session.execute(stmt)


def _upsert_linked_record(
    session: Session, model, tenant_id: str, values: dict, customer_id: UUID, batch_id: UUID
) -> None:
    mirror_column = _MIRROR_NATURAL_KEY_COLUMN.get(model)
    if mirror_column and "natural_key" in values:
        values = {**values, mirror_column: values["natural_key"]}

    content_hash = _content_hash(values)
    insert_values = {
        **values,
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "content_hash": content_hash,
        "source_batch_id": batch_id,
    }
    stmt = pg_insert(model).values(**insert_values)
    update_values = {k: v for k, v in insert_values.items() if k not in ("tenant_id", "natural_key")}
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "natural_key"],
        set_=update_values,
        where=stmt.excluded.content_hash != model.content_hash,
    )
    session.execute(stmt)


def _find_or_create_account(
    session: Session, tenant_id: str, values: dict, customer_id: UUID | None
) -> None:
    account_number = values.get("account_number")
    if account_number is None:
        return
    existing = session.exec(
        select(Account).where(Account.tenant_id == tenant_id, Account.account_number == account_number)
    ).first()
    if existing is not None:
        for key, value in values.items():
            setattr(existing, key, value)
        if customer_id is not None:
            existing.customer_id = customer_id
        session.add(existing)
    elif customer_id is not None:
        session.add(Account(tenant_id=tenant_id, customer_id=customer_id, **values))


def load_batch(session: Session, tenant_id: str, batch: OnboardingBatch) -> LoadSummary:
    spec = _get_confirmed_spec(session, tenant_id, batch.source_id)
    if spec is None:
        raise NoConfirmedMappingSpec(
            f"No confirmed mapping spec for source {batch.source_id} (tenant {tenant_id})"
        )

    raw_rows = session.exec(select(RawRecord).where(RawRecord.batch_id == batch.id)).all()

    # idempotent re-run: this batch's quarantine entries are recomputed fresh every time
    for existing in session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all():
        session.delete(existing)
    session.commit()

    seen_natural_keys: dict[str, set[str]] = {}
    loaded = 0
    quarantined = 0

    for row in raw_rows:
        built_records = apply_spec_to_row(spec.spec_json, row.raw_json)
        row_errors: list[dict] = [e.model_dump() for r in built_records for e in r.errors]

        row_customer_key = next(
            (r.customer_natural_key for r in built_records if r.customer_natural_key), None
        )

        for record in built_records:
            row_errors.extend(e.model_dump() for e in validate_business_rules(record.target_table, record.values))
            row_errors.extend(e.model_dump() for e in validate_schema(record.target_table, record.values))

        for record in built_records:
            if record.target_table not in _NATURAL_KEY_MODELS:
                continue
            natural_key = record.values.get("natural_key")
            if natural_key is None:
                continue
            seen = seen_natural_keys.setdefault(record.target_table, set())
            if natural_key in seen:
                row_errors.append(
                    {"field": "natural_key", "code": "DUPLICATE_EXACT", "raw_value": natural_key}
                )
            else:
                seen.add(natural_key)

        resolved_customer_id: UUID | None = None
        suggested_fix: str | None = None
        needs_customer = any(r.target_table in _LINKED_TABLES for r in built_records)
        if needs_customer:
            effective_key = next(
                (r.customer_natural_key for r in built_records if r.target_table != "accounts" and r.customer_natural_key),
                row_customer_key,
            )
            if effective_key is not None:
                resolved_customer_id = _resolve_customer_id(session, tenant_id, effective_key)
                if resolved_customer_id is None:
                    row_errors.append(
                        {"field": "customer_id", "code": "REF_INTEGRITY_ORPHAN_FK", "raw_value": effective_key}
                    )
                    suggested_fix = _suggest_customer_fix(session, tenant_id, effective_key)
            # effective_key is None only when the required linking field
            # itself failed (already recorded as MISSING_REQUIRED above) —
            # no need for a second, redundant orphan-FK error on top of it.

        if row_errors:
            error_codes = sorted({e["code"] for e in row_errors})
            session.add(
                Quarantine(
                    tenant_id=tenant_id,
                    batch_id=batch.id,
                    raw_record_json=row.raw_json,
                    error_codes=error_codes,
                    severity="error",
                    explanation="; ".join(f"{e['field']}: {e['code']}" for e in row_errors),
                    suggested_fix=suggested_fix,
                    status="open",
                )
            )
            quarantined += 1
            continue

        for record in built_records:
            if record.target_table == "customers":
                _upsert_customer(session, tenant_id, record.values, batch.id)
            elif record.target_table == "invoices":
                _upsert_linked_record(session, Invoice, tenant_id, record.values, resolved_customer_id, batch.id)
            elif record.target_table == "support_tickets":
                _upsert_linked_record(session, SupportTicket, tenant_id, record.values, resolved_customer_id, batch.id)
            elif record.target_table == "accounts":
                _find_or_create_account(session, tenant_id, record.values, resolved_customer_id)
        loaded += 1

    batch.mapping_spec_version = spec.version
    session.add(batch)
    session.commit()

    return LoadSummary(
        batch_id=str(batch.id),
        mapping_spec_version=spec.version,
        total_rows=len(raw_rows),
        loaded=loaded,
        quarantined=quarantined,
    )
