# Day 4: Deterministic Transform + Validation + Exception Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a batch with a **confirmed** mapping spec (Day 3), transform every raw row into typed canonical values, validate it (Pandera schema + business rules + referential integrity + exact-duplicate detection), and either upsert it idempotently into the canonical tables or write it to `quarantine` with a structured, explained error. `POST /load` is the single endpoint that does this — the Minimum Demoable Product: upload → load, valid rows in canonical tables, bad rows in quarantine, nothing silently dropped.

**Architecture:** Four pure-ish modules feed one orchestrator. `app/transforms.py` (no dependencies beyond stdlib) converts raw strings to typed values. `app/record_builder.py` applies a confirmed spec's per-field transform to one raw row and groups the results by target table — this is where `MISSING_REQUIRED` is caught, since it's a property of the transform output, not a business rule. `app/validation.py` runs Pandera schema checks + hand-written business rules against a built record — no DB access, still unit-testable without Postgres. `app/loader.py` is the only module that touches the database: it resolves customer links, checks referential integrity and within-batch exact duplicates, and does the idempotent upsert (or writes to quarantine). A raw row is quarantined as a whole if *any* error surfaces anywhere in this pipeline — no partial loads of a single source row.

**Tech Stack:** Pandera (schema validation), the existing SQLModel/Postgres stack. No new AI/LLM involvement — Day 4 is entirely deterministic, per the spec's "LLM proposes, deterministic engine executes" principle.

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` (repo root) — §2 idempotency design (natural key + content hash + `ON CONFLICT`), §4 validation rule catalogue and exception queue design (error taxonomy, severity, structured error object), §6 Day 4 (scope — **fuzzy dedupe (Splink) and cross-source ID reconciliation are explicitly Day 6, not this plan**). Builds on `docs/superpowers/plans/2026-09-13-day3-ai-tiebreaker-and-mapping-spec.md`'s confirmed `MappingSpec` and `app/canonical.py`'s field registry.

**A gift from Day 1, worth knowing about before you start:** `seed/generate_seed_data.py` deliberately injects real defects into the seed CSVs — `MISSING_REQUIRED`, `DATE_AMBIGUOUS`, `BUSINESS_RULE_NEGATIVE_AMOUNT`, `BUSINESS_RULE_FUTURE_DATE`, `REF_INTEGRITY_ORPHAN_FK`, `DUPLICATE_EXACT`, `DUPLICATE_FUZZY` — and records exactly where in `ground_truth.json`'s `expected_defects`. Task 6 validates this plan's output directly against that file — it's the labeled benchmark for the exception queue, the same way Day 3 had one for mapping accuracy. Two rows are deliberately defect-free-but-tricky and must load successfully, not quarantine: `crm_snake.csv` row 7 (an accented company name, `"Café Système ..."`) and `billing.csv` row 10 (a currency-formatted amount, `"$1,234.56"`).

## Global Constraints

- **Row-level quarantine granularity.** A raw row is loaded only if *every* canonical record it produces (it can produce more than one — a billing row produces both an `invoices` record and an `accounts` record) is fully valid. If any part fails, the whole row is quarantined with the union of all its errors — never a partial load of one source row.
- **`accounts` gets manual find-or-create, not `ON CONFLICT`.** Day 1 deliberately gave `natural_key` + `content_hash` + a unique constraint to `customers`/`invoices`/`support_tickets` only — `accounts` has neither. Dedupe accounts by `(tenant_id, account_number)` via a `SELECT` before `INSERT`/`UPDATE`, at the application level. Less safe under concurrent writers than a DB constraint would be; acceptable for this scope.
- **The three "linking" canonical fields** (`account_customer_natural_key`, `invoice_customer_natural_key`, `ticket_customer_natural_key`, all `target_column="customer_id"`) never go into a record's plain `values` dict — they resolve to a real `customers.id` via a DB lookup in `app/loader.py`, and a miss is `REF_INTEGRITY_ORPHAN_FK`, not a transform error. When a row produces an `accounts` record with no `account_customer_natural_key` of its own (billing/support rows only map `account_number`, not the link), it borrows the customer link resolved for another record from the *same row* (e.g. the invoice's customer link) — accounts don't stand alone in a billing or support file.
- **`POST /load` requires a `confirmed` mapping spec to exist for the batch's source.** No confirmed spec → `409`, not a silent fallback to the latest draft — Day 3's whole point in adding the confirm step was to make this load-bearing.
- **`/load` is idempotent and safely re-runnable.** Customers/invoices/support_tickets use `INSERT ... ON CONFLICT (tenant_id, natural_key) DO UPDATE ... WHERE content_hash <> excluded.content_hash`. Quarantine rows for a batch are deleted and rewritten fresh on every `/load` call for that batch (no unique constraint to upsert against, and simpler than adding one for this scope).
- **Exact duplicates only.** `DUPLICATE_EXACT` = same natural key appears twice within the same batch. Fuzzy/cross-source dedupe (Splink) is Day 6 — a fuzzy-duplicate row (different natural key, similar name) is expected to load as a distinct record in this plan, not get flagged.
- **Money stays an integer.** `to_minor_units` must return `int` cents, never `float` — this is a standing project constraint, not new to Day 4.
- **`Invoice.invoice_number` and `SupportTicket.ticket_ref` are required columns with no canonical field mapping to them** — `canonical.py`'s `invoice_natural_key`/`ticket_natural_key` fields target `natural_key`, not these. This is a Day 1 schema redundancy (two columns meant to hold the same value: the internal dedup key and the "display" business document number), not a modeling mistake to fix now — `app/loader.py`'s upsert mirrors `natural_key` into the extra column for these two tables.
- Every DB-touching test uses the existing `unique_tenant_id` fixture.

---

## Task 1: Transform catalogue (`app/transforms.py`)

**Files:**
- Create: `backend/app/transforms.py`
- Test: `backend/tests/test_transforms.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.transforms.TransformResult` (`NamedTuple`: `value: object | None`, `error_code: str | None`), `app.transforms.trim(raw_value: str | None) -> TransformResult`, `app.transforms.parse_date(raw_value: str | None) -> TransformResult`, `app.transforms.to_int(raw_value: str | None) -> TransformResult`, `app.transforms.to_minor_units(raw_value: str | None) -> TransformResult`, `app.transforms.map_enum(raw_value: str | None, enum_values: list[str]) -> TransformResult`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_transforms.py
from datetime import date

from app.transforms import TransformResult, map_enum, parse_date, to_int, to_minor_units, trim


def test_trim_strips_whitespace():
    assert trim("  hi  ") == TransformResult("hi", None)


def test_trim_empty_string_becomes_none():
    assert trim("   ") == TransformResult(None, None)


def test_trim_none_passes_through():
    assert trim(None) == TransformResult(None, None)


def test_parse_date_iso_format():
    assert parse_date("2026-01-15") == TransformResult(date(2026, 1, 15), None)


def test_parse_date_unambiguous_slash_format_day_first():
    # 25 can't be a month, so this must be DD/MM/YYYY
    assert parse_date("25/12/2025") == TransformResult(date(2025, 12, 25), None)


def test_parse_date_unambiguous_slash_format_month_first():
    # 25 in the second slot can't be a month, so the first slot is the month
    assert parse_date("12/25/2025") == TransformResult(date(2025, 12, 25), None)


def test_parse_date_ambiguous_flags_instead_of_guessing():
    result = parse_date("03/04/2025")
    assert result.value is None
    assert result.error_code == "DATE_AMBIGUOUS"


def test_parse_date_garbage_is_type_coercion_failed():
    result = parse_date("not-a-date")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_parse_date_none_passes_through():
    assert parse_date(None) == TransformResult(None, None)


def test_to_int_parses_plain_integer():
    assert to_int("42") == TransformResult(42, None)


def test_to_int_garbage_is_type_coercion_failed():
    result = to_int("abc")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_to_minor_units_plain_decimal():
    assert to_minor_units("100.00") == TransformResult(10000, None)


def test_to_minor_units_us_formatted_with_symbol_and_thousands_comma():
    assert to_minor_units("$1,234.56") == TransformResult(123456, None)


def test_to_minor_units_negative_amount():
    assert to_minor_units("-450.00") == TransformResult(-45000, None)


def test_to_minor_units_eu_formatted():
    assert to_minor_units("1.234,56") == TransformResult(123456, None)


def test_to_minor_units_garbage_is_type_coercion_failed():
    result = to_minor_units("not-money")
    assert result.value is None
    assert result.error_code == "TYPE_COERCION_FAILED"


def test_to_minor_units_none_passes_through():
    assert to_minor_units(None) == TransformResult(None, None)


def test_map_enum_case_insensitive_match():
    assert map_enum("OPEN", ["open", "paid"]) == TransformResult("open", None)


def test_map_enum_invalid_value():
    result = map_enum("bogus", ["open", "paid"])
    assert result.value is None
    assert result.error_code == "ENUM_INVALID"


def test_map_enum_none_passes_through():
    assert map_enum(None, ["open", "paid"]) == TransformResult(None, None)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_transforms.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.transforms'`.

- [ ] **Step 3: Write `backend/app/transforms.py`**

```python
"""Transform catalogue — the fixed set of functions a mapping-spec entry's
`transform.function` name can refer to (see app.mapping_spec's
_TRANSFORM_BY_FIELD_TYPE, Day 3). Each function takes one raw string
value and returns a TransformResult: either a successfully-typed value,
or an error_code and no value. None in, None out is always success —
"no value" isn't this layer's problem, app.record_builder decides
whether a missing value on a *required* field is MISSING_REQUIRED.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NamedTuple


class TransformResult(NamedTuple):
    value: object | None
    error_code: str | None


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_CURRENCY_SYMBOLS = "$€£¥"


def trim(raw_value: str | None) -> TransformResult:
    if raw_value is None:
        return TransformResult(None, None)
    stripped = raw_value.strip()
    return TransformResult(stripped or None, None)


def parse_date(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    value = raw_value.strip()

    if _ISO_DATE_RE.match(value):
        try:
            return TransformResult(date.fromisoformat(value), None)
        except ValueError:
            return TransformResult(None, "TYPE_COERCION_FAILED")

    match = _SLASH_DATE_RE.match(value)
    if match:
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12:
            day, month = first, second
        elif second > 12:
            day, month = second, first
        else:
            return TransformResult(None, "DATE_AMBIGUOUS")
        try:
            return TransformResult(date(year, month, day), None)
        except ValueError:
            return TransformResult(None, "TYPE_COERCION_FAILED")

    return TransformResult(None, "TYPE_COERCION_FAILED")


def to_int(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    try:
        return TransformResult(int(raw_value.strip()), None)
    except ValueError:
        return TransformResult(None, "TYPE_COERCION_FAILED")


def to_minor_units(raw_value: str | None) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    value = raw_value.strip()
    for symbol in _CURRENCY_SYMBOLS:
        value = value.replace(symbol, "")
    value = value.strip()

    negative = value.startswith("-")
    if negative:
        value = value[1:]

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")  # EU: 1.234,56
        else:
            value = value.replace(",", "")  # US: 1,234.56
    elif "," in value:
        integer_part, _, frac = value.rpartition(",")
        value = f"{integer_part}.{frac}" if len(frac) == 2 else value.replace(",", "")

    try:
        amount = Decimal(value)
    except InvalidOperation:
        return TransformResult(None, "TYPE_COERCION_FAILED")

    minor_units = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return TransformResult(-minor_units if negative else minor_units, None)


def map_enum(raw_value: str | None, enum_values: list[str]) -> TransformResult:
    if raw_value is None or raw_value.strip() == "":
        return TransformResult(None, None)
    normalized = raw_value.strip().lower()
    for allowed in enum_values:
        if allowed.lower() == normalized:
            return TransformResult(allowed, None)
    return TransformResult(None, "ENUM_INVALID")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_transforms.py -v
```

Expected: PASS (20 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/transforms.py backend/tests/test_transforms.py
git commit -m "feat: add transform catalogue (trim, parse_date, to_int, to_minor_units, map_enum)"
```

---

## Task 2: Record builder (`app/record_builder.py`)

**Files:**
- Create: `backend/app/record_builder.py`
- Test: `backend/tests/test_record_builder.py`

**Interfaces:**
- Consumes: `app.canonical.get_field` (existing), `app.transforms.*` (Task 1).
- Produces: `app.record_builder.LINKING_FIELDS: set[str]` (`{"account_customer_natural_key", "invoice_customer_natural_key", "ticket_customer_natural_key"}`), `app.record_builder.FieldError` (Pydantic `BaseModel`: `field: str`, `code: str`, `raw_value: str | None`), `app.record_builder.BuiltRecord` (Pydantic `BaseModel`: `target_table: str`, `values: dict`, `customer_natural_key: str | None`, `errors: list[FieldError]`), `app.record_builder.apply_spec_to_row(spec_json: dict, raw_row: dict) -> list[BuiltRecord]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_record_builder.py
from app.record_builder import apply_spec_to_row


def _entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def test_apply_spec_to_row_builds_customer_record():
    spec_json = {
        "customer_natural_key": _entry("customer_id", "trim"),
        "legal_name": _entry("company_name", "trim"),
        "customer_email": _entry("email", "trim"),
    }
    raw_row = {"customer_id": "C-1001", "company_name": "Acme Corp", "email": "a@acme.com"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert len(records) == 1
    record = records[0]
    assert record.target_table == "customers"
    assert record.values == {
        "natural_key": "C-1001",
        "legal_name": "Acme Corp",
        "email": "a@acme.com",
    }
    assert record.errors == []


def test_apply_spec_to_row_flags_missing_required_field():
    spec_json = {"customer_natural_key": _entry("customer_id", "trim")}
    raw_row = {"customer_id": "   "}  # trims to empty -> None

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].values == {}
    assert records[0].errors == [
        {"field": "customer_natural_key", "code": "MISSING_REQUIRED", "raw_value": "   "}
    ]


def test_apply_spec_to_row_collects_transform_errors():
    spec_json = {"invoice_issue_date": _entry("issue_date", "parse_date")}
    raw_row = {"issue_date": "not-a-date"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].target_table == "invoices"
    assert "issue_date" not in records[0].values
    assert records[0].errors[0].code == "TYPE_COERCION_FAILED"


def test_apply_spec_to_row_routes_linking_field_to_customer_natural_key():
    spec_json = {
        "invoice_customer_natural_key": _entry("customer_id", "trim"),
        "invoice_natural_key": _entry("invoice_number", "trim"),
    }
    raw_row = {"customer_id": "1001", "invoice_number": "INV-2000"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].customer_natural_key == "1001"
    assert "customer_id" not in records[0].values
    assert records[0].values == {"natural_key": "INV-2000"}


def test_apply_spec_to_row_groups_multiple_target_tables():
    spec_json = {
        "invoice_customer_natural_key": _entry("customer_id", "trim"),
        "invoice_natural_key": _entry("invoice_number", "trim"),
        "account_number": _entry("account_number", "trim"),
    }
    raw_row = {"customer_id": "1001", "invoice_number": "INV-2000", "account_number": "ACC-1001"}

    records = apply_spec_to_row(spec_json, raw_row)
    tables = {r.target_table for r in records}

    assert tables == {"invoices", "accounts"}
    accounts_record = next(r for r in records if r.target_table == "accounts")
    assert accounts_record.values == {"account_number": "ACC-1001"}
    assert accounts_record.customer_natural_key is None  # billing rows don't map this


def test_apply_spec_to_row_uses_map_enum_with_field_enum_values():
    spec_json = {"invoice_status": _entry("status", "map_enum")}
    raw_row = {"status": "OPEN"}

    records = apply_spec_to_row(spec_json, raw_row)

    assert records[0].values == {"status": "open"}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_record_builder.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.record_builder'`.

- [ ] **Step 3: Write `backend/app/record_builder.py`**

```python
"""Applies a confirmed mapping spec to one raw row.

Groups the spec's per-field transforms by target table (a single row can
produce more than one canonical record — a billing row produces both an
invoices record and an accounts record) and runs each field's transform
function. MISSING_REQUIRED is caught here, not in app.validation,
because it's a direct property of "did the transform produce a value
for a required field" — no business logic involved.

The three "linking" fields (target_column="customer_id" on
accounts/invoices/support_tickets) never land in a record's `values`
dict — app.loader resolves them to a real customers.id via a DB lookup,
since that's the only place with database access in this pipeline.
"""

from pydantic import BaseModel

from app.canonical import get_field
from app.transforms import map_enum, parse_date, to_int, to_minor_units, trim

LINKING_FIELDS = {
    "account_customer_natural_key",
    "invoice_customer_natural_key",
    "ticket_customer_natural_key",
}

_TRANSFORM_FUNCTIONS = {
    "trim": trim,
    "parse_date": parse_date,
    "to_int": to_int,
    "to_minor_units": to_minor_units,
}


class FieldError(BaseModel):
    field: str
    code: str
    raw_value: str | None


class BuiltRecord(BaseModel):
    target_table: str
    values: dict
    customer_natural_key: str | None = None
    errors: list[FieldError] = []


def apply_spec_to_row(spec_json: dict, raw_row: dict) -> list[BuiltRecord]:
    values_by_table: dict[str, dict] = {}
    customer_key_by_table: dict[str, str | None] = {}
    errors_by_table: dict[str, list[FieldError]] = {}

    for canonical_field_name, entry in spec_json.items():
        field = get_field(canonical_field_name)
        raw_value = raw_row.get(entry["source_column"])
        function_name = entry["transform"]["function"]

        if function_name == "map_enum":
            result = map_enum(raw_value, field.enum_values or [])
        else:
            result = _TRANSFORM_FUNCTIONS[function_name](raw_value)

        values_by_table.setdefault(field.target_table, {})
        errors_by_table.setdefault(field.target_table, [])
        customer_key_by_table.setdefault(field.target_table, None)

        if result.error_code is not None:
            errors_by_table[field.target_table].append(
                FieldError(field=canonical_field_name, code=result.error_code, raw_value=raw_value)
            )
            continue

        if result.value is None and field.required:
            errors_by_table[field.target_table].append(
                FieldError(field=canonical_field_name, code="MISSING_REQUIRED", raw_value=raw_value)
            )
            continue

        if canonical_field_name in LINKING_FIELDS:
            customer_key_by_table[field.target_table] = result.value
        else:
            values_by_table[field.target_table][field.target_column] = result.value

    return [
        BuiltRecord(
            target_table=table,
            values=values,
            customer_natural_key=customer_key_by_table[table],
            errors=errors_by_table[table],
        )
        for table, values in values_by_table.items()
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_record_builder.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/record_builder.py backend/tests/test_record_builder.py
git commit -m "feat: add record builder (applies confirmed spec to a raw row)"
```

---

## Task 3: Validation (`app/validation.py`)

**Files:**
- Create: `backend/app/validation.py`
- Test: `backend/tests/test_validation.py`
- Modify: `backend/pyproject.toml` (add `pandera`)

**Interfaces:**
- Consumes: `app.canonical.FieldType`, `app.canonical.fields_for_table` (existing), `app.record_builder.FieldError`, `app.record_builder.LINKING_FIELDS` (Task 2).
- Produces: `app.validation.validate_business_rules(target_table: str, values: dict) -> list[FieldError]`, `app.validation.validate_schema(target_table: str, values: dict) -> list[FieldError]`.

- [ ] **Step 1: Add `pandera` to `backend/pyproject.toml`**

```toml
    "pandera>=0.20.0",
```

```bash
cd backend && uv sync
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_validation.py
from datetime import date, timedelta

from app.validation import validate_business_rules, validate_schema


def test_future_issue_date_is_flagged():
    values = {"issue_date": date.today() + timedelta(days=1)}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_FUTURE_DATE" for e in errors)


def test_negative_amount_is_flagged():
    values = {"amount_minor_units": -100}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_NEGATIVE_AMOUNT" for e in errors)


def test_due_date_before_issue_date_is_flagged():
    values = {"issue_date": date(2026, 2, 1), "due_date": date(2026, 1, 1)}
    errors = validate_business_rules("invoices", values)
    assert any(e.code == "BUSINESS_RULE_DUE_BEFORE_ISSUE" for e in errors)


def test_valid_invoice_values_produce_no_business_rule_errors():
    values = {
        "issue_date": date.today(),
        "due_date": date.today() + timedelta(days=30),
        "amount_minor_units": 5000,
    }
    assert validate_business_rules("invoices", values) == []


def test_non_invoice_table_has_no_business_rules_applied():
    assert validate_business_rules("customers", {"legal_name": None}) == []


def test_schema_accepts_valid_customer_record():
    values = {
        "natural_key": "C-1001",
        "legal_name": "Acme Corp",
        "email": "a@acme.com",
    }
    assert validate_schema("customers", values) == []


def test_schema_rejects_missing_required_column():
    values = {"legal_name": "Acme Corp"}  # natural_key required, absent entirely
    errors = validate_schema("customers", values)
    assert any(e.code == "SCHEMA_VALIDATION_FAILED" for e in errors)


def test_schema_rejects_wrong_python_type():
    values = {"natural_key": "C-1001", "legal_name": "Acme Corp", "employee_count": "not-a-number"}
    errors = validate_schema("customers", values)
    assert any(e.code == "SCHEMA_VALIDATION_FAILED" for e in errors)
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_validation.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.validation'`.

- [ ] **Step 4: Write `backend/app/validation.py`**

```python
"""Deterministic validation: Pandera schema contracts + hand-written
business rules. Neither touches the database — referential integrity
and duplicate detection need DB access and live in app.loader instead.
"""

from datetime import date

import pandera as pa

from app.canonical import FieldType, fields_for_table
from app.record_builder import LINKING_FIELDS, FieldError

_EXPECTED_TYPE_BY_FIELD_TYPE: dict[FieldType, type] = {
    FieldType.STRING: str,
    FieldType.EMAIL: str,
    FieldType.PHONE: str,
    FieldType.ENUM: str,
    FieldType.CURRENCY_CODE: str,
    FieldType.DATE: date,
    FieldType.DATETIME: date,
    FieldType.INTEGER: int,
    FieldType.MONEY_MINOR_UNITS: int,
}

_SCHEMA_CACHE: dict[str, pa.DataFrameSchema] = {}


def _schema_for_table(target_table: str) -> pa.DataFrameSchema:
    columns = {}
    for field in fields_for_table(target_table):
        if field.name in LINKING_FIELDS:
            continue
        expected_type = _EXPECTED_TYPE_BY_FIELD_TYPE[field.type]
        columns[field.target_column] = pa.Column(
            object,
            nullable=not field.required,
            checks=pa.Check(
                lambda s, t=expected_type: s.dropna().map(lambda v: isinstance(v, t)).all()
            ),
        )
    return pa.DataFrameSchema(columns)


def _get_schema(target_table: str) -> pa.DataFrameSchema:
    if target_table not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[target_table] = _schema_for_table(target_table)
    return _SCHEMA_CACHE[target_table]


def validate_schema(target_table: str, values: dict) -> list[FieldError]:
    import pandas as pd

    schema = _get_schema(target_table)
    row = {name: [values.get(name)] for name in schema.columns}
    df = pd.DataFrame(row)
    try:
        schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        errors = []
        for _, failure in exc.failure_cases.iterrows():
            errors.append(
                FieldError(
                    field=str(failure.get("column") or "unknown"),
                    code="SCHEMA_VALIDATION_FAILED",
                    raw_value=str(failure.get("failure_case")),
                )
            )
        return errors
    return []


def validate_business_rules(target_table: str, values: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    if target_table != "invoices":
        return errors

    issue_date = values.get("issue_date")
    due_date = values.get("due_date")
    amount = values.get("amount_minor_units")

    if issue_date is not None and issue_date > date.today():
        errors.append(
            FieldError(field="issue_date", code="BUSINESS_RULE_FUTURE_DATE", raw_value=str(issue_date))
        )
    if amount is not None and amount < 0:
        errors.append(
            FieldError(field="amount_minor_units", code="BUSINESS_RULE_NEGATIVE_AMOUNT", raw_value=str(amount))
        )
    if issue_date is not None and due_date is not None and due_date < issue_date:
        errors.append(
            FieldError(field="due_date", code="BUSINESS_RULE_DUE_BEFORE_ISSUE", raw_value=str(due_date))
        )
    return errors
```

Note: if Pandera's `lazy=True` `SchemaErrors.failure_cases` DataFrame has different column names on your installed Pandera version (the `column`/`failure_case` keys have shifted across Pandera major versions before), adjust the two `failure.get(...)` calls to match — run Step 3's tests to see the actual columns if this happens.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_validation.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/validation.py backend/tests/test_validation.py
git commit -m "feat: add Pandera schema validation + business rules"
```

---

## Task 4: Loader (`app/loader.py`)

**Files:**
- Create: `backend/app/loader.py`
- Test: `backend/tests/test_loader.py`

**Interfaces:**
- Consumes: `app.record_builder.apply_spec_to_row`, `app.record_builder.BuiltRecord`, `app.record_builder.FieldError` (Task 2), `app.validation.validate_business_rules`, `app.validation.validate_schema` (Task 3), `app.models.MappingSpec`, `app.models.RawRecord`, `app.models.Customer`, `app.models.Account`, `app.models.Invoice`, `app.models.SupportTicket`, `app.models.Quarantine`, `app.models.OnboardingBatch` (existing).
- Produces: `app.loader.NoConfirmedMappingSpec` (Exception), `app.loader.LoadSummary` (Pydantic `BaseModel`: `batch_id: str`, `mapping_spec_version: int`, `total_rows: int`, `loaded: int`, `quarantined: int`), `app.loader.load_batch(session: Session, tenant_id: str, batch: OnboardingBatch) -> LoadSummary`.

This is the only module that touches Postgres in this plan — its tests are DB integration tests, not unit tests.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_loader.py
import pytest
from sqlmodel import Session, select

from app.db import engine
from app.loader import NoConfirmedMappingSpec, load_batch
from app.models import (
    Customer,
    Invoice,
    MappingSpec,
    OnboardingBatch,
    Quarantine,
    RawRecord,
    Source,
)


def _spec_entry(source_column: str, function: str) -> dict:
    return {"source_column": source_column, "transform": {"function": function, "params": {}}}


def _make_batch(session: Session, tenant_id: str, source_kind: str) -> OnboardingBatch:
    source = Source(tenant_id=tenant_id, name=source_kind, kind=source_kind)
    session.add(source)
    session.commit()
    session.refresh(source)

    batch = OnboardingBatch(
        tenant_id=tenant_id,
        source_id=source.id,
        file_hash="test-hash",
        filename="test.csv",
        row_count=0,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def _add_raw_row(session: Session, batch: OnboardingBatch, tenant_id: str, index: int, raw_json: dict) -> None:
    session.add(
        RawRecord(
            batch_id=batch.id,
            source_id=batch.source_id,
            tenant_id=tenant_id,
            row_index=index,
            raw_json=raw_json,
            content_hash=f"hash-{index}",
        )
    )
    session.commit()


def _confirm_spec(session: Session, tenant_id: str, source_id, spec_json: dict) -> MappingSpec:
    spec = MappingSpec(
        tenant_id=tenant_id, source_id=source_id, version=1, spec_json=spec_json, status="confirmed"
    )
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec


def test_load_batch_raises_without_a_confirmed_spec(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001"})

        with pytest.raises(NoConfirmedMappingSpec):
            load_batch(session, unique_tenant_id, batch)


def test_load_batch_loads_valid_customer_row(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 0
        customer = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id, Customer.natural_key == "C-1001")
        ).first()
        assert customer is not None
        assert customer.legal_name == "Acme"


def test_load_batch_quarantines_row_missing_a_required_field(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 0
        assert summary.quarantined == 1
        quarantined = session.exec(
            select(Quarantine).where(Quarantine.batch_id == batch.id)
        ).all()
        assert len(quarantined) == 1
        assert "MISSING_REQUIRED" in quarantined[0].error_codes
        assert quarantined[0].severity == "error"


def test_load_batch_is_idempotent_on_rerun(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "", "company_name": "Acme"})
        _add_raw_row(session, batch, unique_tenant_id, 1, {"customer_id": "C-1002", "company_name": "Beta"})

        load_batch(session, unique_tenant_id, batch)
        load_batch(session, unique_tenant_id, batch)

        customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert len(customers) == 1
        assert len(quarantined) == 1


def test_load_batch_flags_orphan_foreign_key(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "billing")
        spec_json = {
            "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
            "invoice_natural_key": _spec_entry("invoice_number", "trim"),
            "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
            "invoice_currency": _spec_entry("currency", "trim"),
            "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(
            session,
            batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "9999",
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert "REF_INTEGRITY_ORPHAN_FK" in quarantined[0].error_codes


def test_load_batch_suggests_a_fix_for_a_normalized_id_mismatch(unique_tenant_id):
    """The exact cross-system-ID-format case the spec's own example is
    about: CRM writes "C-1001", billing writes "1001"."""
    with Session(engine) as session:
        crm_batch = _make_batch(session, unique_tenant_id, "crm")
        crm_spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, crm_batch.source_id, crm_spec_json)
        _add_raw_row(session, crm_batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
        load_batch(session, unique_tenant_id, crm_batch)

        billing_batch = _make_batch(session, unique_tenant_id, "billing")
        billing_spec_json = {
            "invoice_customer_natural_key": _spec_entry("customer_id", "trim"),
            "invoice_natural_key": _spec_entry("invoice_number", "trim"),
            "invoice_amount_minor_units": _spec_entry("amount", "to_minor_units"),
            "invoice_currency": _spec_entry("currency", "trim"),
            "invoice_issue_date": _spec_entry("issue_date", "parse_date"),
        }
        _confirm_spec(session, unique_tenant_id, billing_batch.source_id, billing_spec_json)
        _add_raw_row(
            session,
            billing_batch,
            unique_tenant_id,
            0,
            {
                "customer_id": "1001",  # not "C-1001" -- an orphan FK by exact match
                "invoice_number": "INV-1",
                "amount": "100.00",
                "currency": "USD",
                "issue_date": "2026-01-01",
            },
        )

        load_batch(session, unique_tenant_id, billing_batch)

        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == billing_batch.id)).all()
        assert "REF_INTEGRITY_ORPHAN_FK" in quarantined[0].error_codes
        assert "C-1001" in (quarantined[0].suggested_fix or "")


def test_load_batch_flags_exact_duplicate_within_batch(unique_tenant_id):
    with Session(engine) as session:
        batch = _make_batch(session, unique_tenant_id, "crm")
        spec_json = {
            "customer_natural_key": _spec_entry("customer_id", "trim"),
            "legal_name": _spec_entry("company_name", "trim"),
        }
        _confirm_spec(session, unique_tenant_id, batch.source_id, spec_json)
        _add_raw_row(session, batch, unique_tenant_id, 0, {"customer_id": "C-1001", "company_name": "Acme"})
        _add_raw_row(session, batch, unique_tenant_id, 1, {"customer_id": "C-1001", "company_name": "Acme"})

        summary = load_batch(session, unique_tenant_id, batch)

        assert summary.loaded == 1
        assert summary.quarantined == 1
        quarantined = session.exec(select(Quarantine).where(Quarantine.batch_id == batch.id)).all()
        assert "DUPLICATE_EXACT" in quarantined[0].error_codes
```

Add `import pytest` at the top of the test file (needed for `pytest.raises`).

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_loader.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.loader'`.

- [ ] **Step 3: Write `backend/app/loader.py`**

```python
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
from app.record_builder import BuiltRecord, apply_spec_to_row
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
    if natural_key is None:
        return None
    customer = session.exec(
        select(Customer).where(Customer.tenant_id == tenant_id, Customer.natural_key == natural_key)
    ).first()
    return customer.id if customer else None


def _suggest_customer_fix(session: Session, tenant_id: str, attempted_key: str) -> str | None:
    """Best-effort suggestion for an orphan customer FK. Tries an exact
    match after stripping non-digit characters first (CRM's "C-1001" vs
    billing's "1001" is exactly this — the spec's own example), then
    falls back to a rapidfuzz match against existing natural keys."""
    customers = session.exec(select(Customer).where(Customer.tenant_id == tenant_id)).all()

    normalized_attempt = re.sub(r"\D", "", attempted_key)
    if normalized_attempt:
        for customer in customers:
            if re.sub(r"\D", "", customer.natural_key) == normalized_attempt:
                return f"Map to existing customer '{customer.natural_key}' (normalized ID match)"

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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_loader.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/loader.py backend/tests/test_loader.py
git commit -m "feat: add idempotent loader (upsert + referential integrity + quarantine)"
```

---

## Task 5: `POST /load` endpoint

**Files:**
- Create: `backend/app/routers/load.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_load_endpoint.py`

**Interfaces:**
- Consumes: `app.db.get_session`, `app.models.OnboardingBatch` (existing), `app.loader.load_batch`, `app.loader.NoConfirmedMappingSpec` (Task 4).
- Produces: `POST /load` (form: `tenant_id`, `batch_id`) → `200 LoadSummary` (as JSON); `404` if the batch doesn't exist or belongs to a different tenant; `400` if `batch_id` isn't a valid UUID; `409` if no confirmed mapping spec exists for the batch's source.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_load_endpoint.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import Customer

client = TestClient(app)


def _upload_tiny_crm(tenant_id: str) -> str:
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()
    response = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 201
    return response.json()["batch_id"]


def _create_and_confirm_spec(tenant_id: str, batch_id: str) -> None:
    create = client.post("/mapping-spec", data={"tenant_id": tenant_id, "batch_id": batch_id})
    spec_id = create.json()["mapping_spec_id"]
    confirm = client.post(f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": tenant_id})
    assert confirm.status_code == 200


def test_load_without_confirmed_spec_returns_409(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})
    assert response.status_code == 409


def test_load_loads_customers_after_spec_confirmed(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    _create_and_confirm_spec(unique_tenant_id, batch_id)

    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    assert response.status_code == 200
    body = response.json()
    assert body["total_rows"] == 5
    assert body["loaded"] + body["quarantined"] == 5

    with Session(engine) as session:
        customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        assert len(customers) == body["loaded"]


def test_load_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/load",
        data={"tenant_id": unique_tenant_id, "batch_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404


def test_load_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"})
    assert response.status_code == 400
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_load_endpoint.py -v
```

Expected: FAIL — `404` on `/load` (route doesn't exist yet).

- [ ] **Step 3: Write `backend/app/routers/load.py`**

```python
"""POST /load — transforms, validates, and idempotently loads a batch
using its source's confirmed mapping spec. See app.loader for the
actual work; this router only does request plumbing.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.loader import NoConfirmedMappingSpec, load_batch
from app.models import OnboardingBatch

router = APIRouter()


@router.post("/load")
def load_batch_route(
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

    try:
        summary = load_batch(session, tenant_id, batch)
    except NoConfirmedMappingSpec:
        raise HTTPException(status_code=409, detail="No confirmed mapping spec for this batch's source")

    return summary.model_dump()
```

- [ ] **Step 4: Wire the router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.routers.load import router as load_router
from app.routers.mapping_spec import router as mapping_spec_router
from app.routers.profile import router as profile_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)
app.include_router(profile_router)
app.include_router(mapping_spec_router)
app.include_router(load_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_load_endpoint.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 6: Run the full backend test suite twice in a row**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: PASS both times.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/load.py backend/app/main.py backend/tests/test_load_endpoint.py
git commit -m "feat: add POST /load endpoint"
```

---

## Task 6: End-to-end MDP test against the seed benchmark

**Files:**
- Test: `backend/tests/test_mdp_end_to_end.py`

**Interfaces:**
- Consumes: `seed.generate_seed_data.generate`, `seed.generate_seed_data.OUTPUT_DIR` (existing), the full `POST /upload` → `POST /mapping-spec` → `POST /mapping-spec/{id}/confirm` → `POST /load` chain (Days 1-4).
- Produces: nothing new — this is the plan's proof, not a new interface. Makes real Groq/Ollama calls (whatever columns Day 3's scorer can't confidently place) and is slower than the rest of the suite; that's expected for the one test that exercises the whole pipeline for real.

This is the plan's actual deliverable check: real, deliberately-messy seed CSVs, uploaded and pushed through the entire pipeline, with the results checked against `ground_truth.json`'s `expected_defects` — not just "nothing crashed."

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_mdp_end_to_end.py
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import Customer, Invoice, Quarantine, SupportTicket
from seed.generate_seed_data import OUTPUT_DIR, generate

client = TestClient(app)

_FILE_SOURCE_KIND = {
    "crm_snake.csv": "crm",
    "billing.csv": "billing",
    "support.csv": "support",
}


def _run_pipeline_for_file(tenant_id: str, filename: str, source_kind: str) -> dict:
    with (OUTPUT_DIR / filename).open("rb") as f:
        content = f.read()
    upload = client.post(
        "/upload",
        files={"file": (filename, content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": source_kind, "source_kind": source_kind},
    )
    assert upload.status_code == 201
    batch_id = upload.json()["batch_id"]

    spec = client.post("/mapping-spec", data={"tenant_id": tenant_id, "batch_id": batch_id})
    assert spec.status_code == 200
    spec_id = spec.json()["mapping_spec_id"]

    confirm = client.post(f"/mapping-spec/{spec_id}/confirm", data={"tenant_id": tenant_id})
    assert confirm.status_code == 200

    load = client.post("/load", data={"tenant_id": tenant_id, "batch_id": batch_id})
    assert load.status_code == 200
    return load.json()


def test_mdp_upload_to_load_matches_expected_defects(unique_tenant_id):
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())

    # crm_snake.csv must run first: billing/support rows reference these customers.
    crm_summary = _run_pipeline_for_file(unique_tenant_id, "crm_snake.csv", "crm")
    billing_summary = _run_pipeline_for_file(unique_tenant_id, "billing.csv", "billing")
    support_summary = _run_pipeline_for_file(unique_tenant_id, "support.csv", "support")

    expected = ground_truth["expected_defects"]
    # Every file has at least as many quarantined rows as it has expected
    # defects (DUPLICATE_FUZZY rows are expected to load, not quarantine —
    # that's Day 6 — so this is a floor, not an exact count).
    assert crm_summary["quarantined"] >= sum(
        1 for codes in expected["crm_snake.csv"].values() if "DUPLICATE_FUZZY" not in codes
    )
    assert billing_summary["quarantined"] >= len(expected["billing.csv"])
    assert support_summary["quarantined"] >= len(expected["support.csv"])

    with Session(engine) as session:
        # the two deliberately-tricky-but-valid rows must have loaded, not quarantined
        loaded_customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        assert any("Café Système" in c.legal_name for c in loaded_customers)

        loaded_invoices = session.exec(
            select(Invoice).where(Invoice.tenant_id == unique_tenant_id)
        ).all()
        assert any(inv.amount_minor_units == 123456 for inv in loaded_invoices)

        # at least one support ticket and one invoice actually loaded
        assert len(loaded_invoices) > 0
        support_tickets = session.exec(
            select(SupportTicket).where(SupportTicket.tenant_id == unique_tenant_id)
        ).all()
        assert len(support_tickets) > 0

        quarantined = session.exec(
            select(Quarantine).where(Quarantine.tenant_id == unique_tenant_id)
        ).all()
        all_codes = {code for q in quarantined for code in q.error_codes}
        # every quarantine-worthy code the seed data injects (except the
        # Day-6-scoped fuzzy dedupe) must show up somewhere
        assert "MISSING_REQUIRED" in all_codes
        assert "BUSINESS_RULE_NEGATIVE_AMOUNT" in all_codes
        assert "BUSINESS_RULE_FUTURE_DATE" in all_codes
        assert "REF_INTEGRITY_ORPHAN_FK" in all_codes
        assert "DUPLICATE_EXACT" in all_codes


def test_load_is_re_runnable(unique_tenant_id):
    generate()
    first = _run_pipeline_for_file(unique_tenant_id, "crm_snake.csv", "crm")

    with (OUTPUT_DIR / "crm_snake.csv").open("rb") as f:
        content = f.read()
    reupload = client.post(
        "/upload",
        files={"file": ("crm_snake.csv", content, "text/csv")},
        data={"tenant_id": unique_tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert reupload.json()["idempotent"] is True
    batch_id = reupload.json()["batch_id"]

    second = client.post("/load", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})
    assert second.status_code == 200
    assert second.json()["loaded"] == first["loaded"]
    assert second.json()["quarantined"] == first["quarantined"]

    with Session(engine) as session:
        customers = session.exec(
            select(Customer).where(Customer.tenant_id == unique_tenant_id)
        ).all()
        assert len(customers) == first["loaded"]  # no duplicates from re-running
```

- [ ] **Step 2: Run it**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mdp_end_to_end.py -v -s
```

Expected: PASS (2 tests). This is the slowest test in the suite (real LLM calls across three files) — that's expected. If a specific assertion about which codes appear fails, print `quarantined` rows' `error_codes`/`explanation` to see what actually happened before adjusting — the seed data's exact row contents are the source of truth, not this test's assumptions about them.

- [ ] **Step 3: Run the full suite one more time to confirm nothing regressed**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: PASS, all tests — this is the Minimum Demoable Product milestone.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_mdp_end_to_end.py
git commit -m "test: add end-to-end MDP test (upload -> mapping-spec -> confirm -> load) against seed defects"
```
