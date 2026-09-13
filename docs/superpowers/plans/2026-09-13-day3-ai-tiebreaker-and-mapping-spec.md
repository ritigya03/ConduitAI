# Day 3: AI Tie-Breaker + Mapping Spec + Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For columns Day 2's deterministic scorer buckets `unmapped`, ask an LLM (Instructor + Groq, falling back to local Ollama, falling back to the deterministic guess) which canonical field they map to. Assemble every column's final decision — deterministic or LLM — into a versioned `mapping_specs` row with provenance. Prove the AI layer earns its keep with a benchmark script comparing fuzzy-only baseline vs. deterministic hybrid vs. hybrid+LLM accuracy on the labeled seed data.

**Architecture:** `POST /mapping-spec` reuses Day 2's profiling + scoring fresh (self-contained, same pattern as `POST /profile`), then calls the LLM tie-breaker only for `unmapped`-bucket columns, then hands everything to a pure, DB-free builder that assembles the spec JSON and a thin versioning helper that persists it. The LLM client, the spec builder, and the endpoint are three separate modules so each is testable without a live LLM call or a running server.

**Tech Stack:** `instructor` (schema-enforced structured LLM output via Pydantic), `openai` SDK (both Groq and Ollama expose OpenAI-compatible endpoints, so one client shape covers both), Groq (`llama-3.3-70b-versatile`, primary), local Ollama (`llama3`, already pulled — fallback).

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` (repo root) — §2 "Mapping-spec versioning" (spec JSON shape, versioning semantics), §3 "Hybrid mapping scorer" step 4 and "Reliable structured output from free models" and "Prompt design" (LLM tie-breaker mechanics), §3 "Evaluation of the AI component" (benchmark requirements), §6 Day 3 (scope), §9 "zero-budget AI stack" (provider fallback chain). Builds directly on `docs/superpowers/plans/2026-09-13-day2-profiling-and-mapping.md`'s `app/profiling.py` and `app/scoring.py`.

**Important — this plan also fixes two bugs found in Day 2's shipped `app/scoring.py` while validating the benchmark** (see Task 1): `_normalize_column_name`'s camelCase-split regex fragments ALL-CAPS column names into single letters, and `_SOURCE_KIND_TO_TABLES` never included the `accounts` table for `billing`/`support` sources even though `account_number` (an `accounts`-table field) is a real, expected mapping target for both. Both bugs make some ground-truth columns impossible to map correctly regardless of signal quality, which would make the Day 3 benchmark measure the wrong thing if left unfixed.

## Global Constraints

- **The LLM is called only for columns the Day 2 deterministic scorer bucketed `unmapped`** — never for `auto_accept` or `human_confirm` columns. This is what keeps LLM usage to a handful of calls per file (well under Groq's free-tier 30 RPM / 6,000 TPM).
- **Fallback chain: Groq → local Ollama (`llama3`) → deterministic fallback.** If Groq errors (missing key, rate limit, any exception) it falls through to Ollama; if Ollama also fails, the column keeps its Day 2 deterministic best guess, tagged `provenance.method = "deterministic_fallback"`. The pipeline must never raise or block on an LLM failure.
- **Instructor constrains `canonical_field` to a dynamically-built `Literal` of exactly that column's candidate pool + `"UNKNOWN"`** — the LLM cannot invent a field name; a hallucinated value fails Pydantic validation and Instructor retries automatically (`max_retries=2`).
- **A `MappingSpec` row is never edited in place.** Every `POST /mapping-spec` call creates `version = max_existing_version_for_(tenant, source) + 1` with `parent_version` pointing at the previous max. `status` is always `"draft"` — promoting a spec to `"confirmed"` is a human action that belongs to Day 5's review UI, not this plan.
- **`mapping_reviews` is out of scope for this plan.** It exists for Day 5's human-in-the-loop review flow (a person accepting/overriding a proposal); Day 3 only ever writes to `mapping_specs`.
- **The mapping-spec JSON is keyed by canonical field name, not source column** — `{"customer_email": {"source_column": "email", "transform": {...}, "provenance": {...}}}`. A column whose final decision is `"UNKNOWN"` (from the LLM) or has no decision at all is simply absent from the spec — never written as a null/placeholder entry.
- **`transform` is a static `FieldType → {function, params}` lookup, decided here, not by the LLM.** This plan only *names* the transform function a field should use (e.g. `parse_date`, `to_minor_units`); writing the transform *engine* that executes these is Day 4's job.
- **Benchmark eval is split in two, mirroring Day 2's fastembed-in-endpoint-only pattern:** a fast, deterministic pytest test (`tests/test_mapping_benchmark.py`, no LLM, no network, runs on every `pytest` invocation) proves the deterministic hybrid beats the fuzzy baseline; a separate on-demand CLI script (`seed/eval_mapping.py`) adds the LLM pass and prints the full three-way comparison, making real Groq calls — it is deliberately not part of the default test suite.
- `GROQ_API_KEY` lives in `backend/.env` (already gitignored) and is read via `pydantic-settings`, same mechanism as `DATABASE_URL`.
- Every DB-touching test uses the existing `unique_tenant_id` fixture (`backend/tests/conftest.py`).

---

## Task 1: Fix Day 2 scoring bugs (camelCase regex + candidate pool) and promote `candidate_pool` to public

**Files:**
- Modify: `backend/app/scoring.py`
- Modify: `backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.scoring.candidate_pool(source_kind: str | None) -> list[CanonicalField]` (renamed from `_candidate_pool` — Task 4's router needs it too, so it's no longer scoring.py-internal). All other Day 2 interfaces (`name_similarity`, `type_format_score`, `bucket_for`, `score_column`, `score_all_columns`, `MappingCandidate`, `ColumnMapping`) are unchanged in shape, only corrected in behavior.

- [ ] **Step 1: Write the failing regression tests**

Append to `backend/tests/test_scoring.py`:

```python
def test_name_similarity_handles_all_caps_column_name():
    """Regression test: the old camelCase-split regex (?<!^)(?=[A-Z]) split
    every uppercase letter, so "PHONE" became "p h o n e" (5 single-letter
    tokens) instead of staying one word — badly hurting the fuzzy match
    against legacy all-caps headers like crm_legacy.csv's PHONE, DISPNAME,
    DT_CREATE."""
    field = get_field("customer_phone")
    assert name_similarity("PHONE", field) == 1.0


def test_candidate_pool_includes_accounts_for_billing_and_support():
    """Regression test: account_number is an accounts-table field, but the
    billing/support candidate pools excluded the accounts table entirely —
    meaning billing.csv's and support.csv's account_number columns could
    never be mapped correctly by any signal, since the right field wasn't
    even a candidate."""
    from app.scoring import candidate_pool

    billing_names = {f.name for f in candidate_pool("billing")}
    support_names = {f.name for f in candidate_pool("support")}
    assert "account_number" in billing_names
    assert "account_number" in support_names
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_scoring.py -k "all_caps or includes_accounts" -v
```

Expected: FAIL — `test_name_similarity_handles_all_caps_column_name` fails because `"PHONE"` currently scores well below `1.0`; `test_candidate_pool_includes_accounts_for_billing_and_support` fails with `AttributeError` (no `candidate_pool`, only `_candidate_pool`) or, if you `import _candidate_pool` to check first, an assertion failure.

- [ ] **Step 3: Fix `backend/app/scoring.py`**

Change `_normalize_column_name`'s regex — only split at an actual lowercase/digit → uppercase transition, not before every uppercase letter:

```python
def _normalize_column_name(name: str) -> str:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return split_camel.replace("_", " ").replace("-", " ").lower().strip()
```

Add `"accounts"` to the billing and support pools:

```python
_SOURCE_KIND_TO_TABLES: dict[str, list[str]] = {
    "crm": ["customers", "accounts"],
    "billing": ["invoices", "accounts"],
    "support": ["support_tickets", "accounts"],
}
```

Rename `_candidate_pool` to `candidate_pool` (drop the leading underscore) — update both the `def` and its one call site inside `score_column`:

```python
def candidate_pool(source_kind: str | None) -> list[CanonicalField]:
    tables = _SOURCE_KIND_TO_TABLES.get(source_kind) if source_kind else None
    if tables is None:
        return CANONICAL_FIELDS
    return [field for field in CANONICAL_FIELDS if field.target_table in tables]
```

```python
def score_column(
    column_name: str, stats: ColumnStats, source_kind: str | None
) -> ColumnMapping:
    pool = candidate_pool(source_kind)
    ...
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_scoring.py -k "all_caps or includes_accounts" -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Run the full scoring + profile-endpoint suites to confirm nothing regressed**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_scoring.py tests/test_profile_endpoint.py -v
```

Expected: PASS, all tests (the existing `"CustID"` camelCase test should still pass — verify its score is now `>= 0.7` as before, possibly higher).

- [ ] **Step 6: Commit**

```bash
git add backend/app/scoring.py backend/tests/test_scoring.py
git commit -m "fix: correct camelCase-split regex and add accounts to billing/support candidate pools"
```

---

## Task 2: LLM tie-breaker (Instructor + Groq → Ollama fallback)

**Files:**
- Create: `backend/app/llm_mapper.py`
- Test: `backend/tests/test_llm_mapper.py`
- Modify: `backend/app/config.py` (add `groq_api_key`, `ollama_base_url`)
- Modify: `backend/tests/test_config.py`
- Modify: `backend/pyproject.toml` (add `instructor`, `openai`)

**Interfaces:**
- Consumes: `app.canonical.CanonicalField` (existing), `app.profiling.ColumnStats` (Day 2), `app.config.settings` (existing, extended here).
- Produces: `app.llm_mapper.LLMDecision` (Pydantic `BaseModel`: `canonical_field: str`, `confidence: float`, `reasoning: str`, `model: str`), `app.llm_mapper.tiebreak(column_name: str, stats: ColumnStats, candidates: list[CanonicalField]) -> LLMDecision | None`.

- [ ] **Step 1: Add dependencies to `backend/pyproject.toml`**

```toml
    "instructor>=1.6.0",
    "openai>=1.50.0",
```

```bash
cd backend && uv sync
```

- [ ] **Step 2: Add settings and their failing tests**

Append to `backend/tests/test_config.py`:

```python
def test_settings_reads_groq_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    settings = Settings()
    assert settings.groq_api_key == "test-key"


def test_settings_ollama_base_url_default():
    settings = Settings()
    assert settings.ollama_base_url == "http://localhost:11434/v1"
```

Run to verify it fails:

```bash
cd backend && uv run pytest tests/test_config.py -v
```

Expected: FAIL — `AttributeError`, `Settings` has no `groq_api_key`/`ollama_base_url` field yet.

Modify `backend/app/config.py` — add two fields:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://conduit:conduit@localhost:5432/conduitai"
    environment: str = "development"
    default_tenant_id: str = "demo-tenant"
    groq_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
```

Run to verify it passes:

```bash
cd backend && uv run pytest tests/test_config.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 3: Write the failing tests for `llm_mapper`**

```python
# backend/tests/test_llm_mapper.py
import pytest
from pydantic import ValidationError

from app.canonical import get_field
from app.llm_mapper import _build_decision_model, _build_user_prompt, tiebreak
from app.profiling import ColumnStats


def _stats(**overrides) -> ColumnStats:
    defaults = dict(
        column_name="col",
        row_count=5,
        null_count=0,
        null_fraction=0.0,
        distinct_count=5,
        distinct_fraction=1.0,
        sample_values=["a", "b", "c"],
        date_parse_fraction=0.0,
        int_parse_fraction=0.0,
        float_parse_fraction=0.0,
        email_match_fraction=0.0,
        phone_match_fraction=0.0,
        currency_code_match_fraction=0.0,
    )
    defaults.update(overrides)
    return ColumnStats(**defaults)


def test_build_decision_model_rejects_field_outside_candidate_list():
    model_cls = _build_decision_model(["customer_email", "customer_phone"])
    model_cls(canonical_field="customer_email", confidence=0.9, reasoning="matches")
    with pytest.raises(ValidationError):
        model_cls(canonical_field="not_a_real_field", confidence=0.9, reasoning="x")


def test_build_decision_model_allows_unknown():
    model_cls = _build_decision_model(["customer_email"])
    model_cls(canonical_field="UNKNOWN", confidence=0.1, reasoning="no match")


def test_build_user_prompt_includes_column_and_samples():
    stats = _stats(sample_values=["a@example.com", "b@example.com"])
    field = get_field("customer_email")
    prompt = _build_user_prompt("contact", stats, [field])
    assert "contact" in prompt
    assert "a@example.com" in prompt
    assert "customer_email" in prompt


def test_tiebreak_returns_none_with_no_candidates():
    assert tiebreak("col", _stats(), []) is None


def test_tiebreak_falls_back_to_ollama_when_groq_fails(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    calls = []

    def fake_call_provider(*, base_url, api_key, model, response_model, user_prompt):
        calls.append(base_url)
        if "groq" in base_url:
            raise RuntimeError("rate limited")
        return response_model(canonical_field="customer_email", confidence=0.8, reasoning="ok")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", "fake-key")

    field = get_field("customer_email")
    decision = tiebreak("contact", _stats(), [field])

    assert decision is not None
    assert decision.canonical_field == "customer_email"
    assert decision.model == f"ollama/{llm_mapper_module._OLLAMA_MODEL}"
    assert len(calls) == 2


def test_tiebreak_returns_none_when_all_providers_fail(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    def fake_call_provider(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", "fake-key")

    field = get_field("customer_email")
    assert tiebreak("contact", _stats(), [field]) is None


def test_tiebreak_skips_groq_when_no_api_key(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    calls = []

    def fake_call_provider(*, base_url, api_key, model, response_model, user_prompt):
        calls.append(base_url)
        return response_model(canonical_field="UNKNOWN", confidence=0.1, reasoning="none")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", None)

    field = get_field("customer_email")
    tiebreak("contact", _stats(), [field])

    assert len(calls) == 1
    assert "11434" in calls[0]
```

- [ ] **Step 4: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_llm_mapper.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm_mapper'`.

- [ ] **Step 5: Write `backend/app/llm_mapper.py`**

```python
"""LLM tie-breaker for low-confidence column mappings.

Called ONLY for columns the Day 2 deterministic scorer bucketed
"unmapped" — this keeps LLM usage to a handful of calls per file, well
under free-tier rate limits (Groq: 30 RPM / 6,000 TPM). Falls back
Groq -> local Ollama -> None (caller then keeps the deterministic best
guess) so a missing key, a rate limit, or no internet never blocks the
pipeline. Catching broad `Exception` around each provider call is
deliberate here, not sloppy — any failure mode (auth, rate limit,
timeout, malformed response) must fall through to the next provider.
"""

from typing import Literal

from instructor import from_openai
from openai import OpenAI
from pydantic import BaseModel, create_model

from app.canonical import CanonicalField
from app.config import settings
from app.profiling import ColumnStats

_GROQ_MODEL = "llama-3.3-70b-versatile"
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_OLLAMA_MODEL = "llama3"

_SYSTEM_PROMPT = """You map messy source-data column names to a canonical field schema. You will be given a source column's name, a few sample values, and a list of candidate canonical fields with descriptions. Pick the single best-matching canonical field, or "UNKNOWN" if none genuinely fit. Never invent a field name that isn't in the candidate list.

Example:
Column: "amt_usd". Samples: 100.00, 250.50, 99.99
Candidates:
- invoice_amount_minor_units: The invoice amount, stored as an integer count of the currency's minor units.
- invoice_tax_amount_minor_units: The tax amount charged on this invoice.
Answer: canonical_field="invoice_amount_minor_units", confidence=0.9, reasoning="amt_usd reads as the primary charge, not the tax line."
"""


class LLMDecision(BaseModel):
    canonical_field: str
    confidence: float
    reasoning: str
    model: str


def _build_decision_model(candidate_names: list[str]) -> type[BaseModel]:
    allowed = tuple(candidate_names) + ("UNKNOWN",)
    return create_model(
        "MappingDecision",
        canonical_field=(Literal[allowed], ...),
        confidence=(float, ...),
        reasoning=(str, ...),
    )


def _build_user_prompt(
    column_name: str, stats: ColumnStats, candidates: list[CanonicalField]
) -> str:
    samples = ", ".join(stats.sample_values[:5]) or "(no non-null samples)"
    candidate_lines = "\n".join(
        f"- {field.name}: {field.description}" for field in candidates
    )
    return (
        f'Column: "{column_name}". Samples: {samples}\n'
        f"Candidates:\n{candidate_lines}\n"
        "Which canonical field does this column map to?"
    )


def _call_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    response_model: type[BaseModel],
    user_prompt: str,
) -> BaseModel:
    client = from_openai(OpenAI(base_url=base_url, api_key=api_key))
    return client.chat.completions.create(
        model=model,
        response_model=response_model,
        max_retries=2,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )


def tiebreak(
    column_name: str, stats: ColumnStats, candidates: list[CanonicalField]
) -> LLMDecision | None:
    if not candidates:
        return None

    response_model = _build_decision_model([f.name for f in candidates])
    user_prompt = _build_user_prompt(column_name, stats, candidates)

    if settings.groq_api_key:
        try:
            result = _call_provider(
                base_url=_GROQ_BASE_URL,
                api_key=settings.groq_api_key,
                model=_GROQ_MODEL,
                response_model=response_model,
                user_prompt=user_prompt,
            )
            return LLMDecision(
                canonical_field=result.canonical_field,
                confidence=result.confidence,
                reasoning=result.reasoning,
                model=f"groq/{_GROQ_MODEL}",
            )
        except Exception:
            pass  # fall through to Ollama

    try:
        result = _call_provider(
            base_url=settings.ollama_base_url,
            api_key="ollama",  # Ollama ignores the key but the OpenAI client requires one
            model=_OLLAMA_MODEL,
            response_model=response_model,
            user_prompt=user_prompt,
        )
        return LLMDecision(
            canonical_field=result.canonical_field,
            confidence=result.confidence,
            reasoning=result.reasoning,
            model=f"ollama/{_OLLAMA_MODEL}",
        )
    except Exception:
        return None
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_llm_mapper.py -v
```

Expected: PASS (7 tests). None of these touch the network — `_call_provider` is monkeypatched in every test that exercises `tiebreak`.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/config.py backend/app/llm_mapper.py \
        backend/tests/test_config.py backend/tests/test_llm_mapper.py
git commit -m "feat: add LLM tie-breaker (Instructor + Groq -> Ollama fallback)"
```

---

## Task 3: Mapping-spec builder + versioning

**Files:**
- Create: `backend/app/mapping_spec.py`
- Test: `backend/tests/test_mapping_spec.py`

**Interfaces:**
- Consumes: `app.canonical.FieldType`, `app.canonical.get_field` (existing), `app.scoring.ColumnMapping`, `app.scoring.MappingCandidate` (Day 2), `app.llm_mapper.LLMDecision` (Task 2), `app.models.MappingSpec` (existing, no migration needed).
- Produces: `app.mapping_spec.build_spec_entries(mappings: list[ColumnMapping], llm_decisions: dict[str, LLMDecision]) -> dict[str, dict]`, `app.mapping_spec.next_version(session: Session, tenant_id: str, source_id: UUID) -> tuple[int, int | None]`, `app.mapping_spec.save_mapping_spec(session: Session, tenant_id: str, source_id: UUID, spec_entries: dict[str, dict]) -> MappingSpec`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_mapping_spec.py
from sqlmodel import Session

from app.db import engine
from app.llm_mapper import LLMDecision
from app.mapping_spec import build_spec_entries, next_version, save_mapping_spec
from app.models import MappingSpec, Source
from app.scoring import ColumnMapping, MappingCandidate


def _candidate(field: str, confidence: float) -> MappingCandidate:
    return MappingCandidate(
        canonical_field=field,
        confidence=confidence,
        name_score=confidence,
        embedding_score=confidence,
        type_score=confidence,
    )


def test_build_spec_entries_includes_confident_deterministic_columns():
    mapping = ColumnMapping(
        column_name="email",
        candidates=[_candidate("customer_email", 0.95)],
        best_field="customer_email",
        bucket="auto_accept",
    )
    entries = build_spec_entries([mapping], llm_decisions={})

    assert "customer_email" in entries
    assert entries["customer_email"]["source_column"] == "email"
    assert entries["customer_email"]["provenance"]["method"] == "deterministic"
    assert entries["customer_email"]["transform"]["function"] == "trim"


def test_build_spec_entries_excludes_unmapped_column_with_no_llm_decision():
    mapping = ColumnMapping(
        column_name="notes", candidates=[], best_field=None, bucket="unmapped"
    )
    entries = build_spec_entries([mapping], llm_decisions={})
    assert entries == {}


def test_build_spec_entries_uses_llm_decision_for_unmapped_column():
    mapping = ColumnMapping(
        column_name="amt_usd",
        candidates=[_candidate("invoice_tax_amount_minor_units", 0.4)],
        best_field="invoice_tax_amount_minor_units",
        bucket="unmapped",
    )
    llm_decision = LLMDecision(
        canonical_field="invoice_amount_minor_units",
        confidence=0.85,
        reasoning="matches the primary charge",
        model="groq/llama-3.3-70b-versatile",
    )
    entries = build_spec_entries([mapping], llm_decisions={"amt_usd": llm_decision})

    assert "invoice_amount_minor_units" in entries
    assert entries["invoice_amount_minor_units"]["source_column"] == "amt_usd"
    assert entries["invoice_amount_minor_units"]["provenance"]["method"] == "llm"
    assert (
        entries["invoice_amount_minor_units"]["provenance"]["model"]
        == "groq/llama-3.3-70b-versatile"
    )


def test_build_spec_entries_skips_column_when_llm_says_unknown():
    mapping = ColumnMapping(
        column_name="notes", candidates=[], best_field=None, bucket="unmapped"
    )
    llm_decision = LLMDecision(
        canonical_field="UNKNOWN", confidence=0.2, reasoning="no match", model="groq/x"
    )
    entries = build_spec_entries([mapping], llm_decisions={"notes": llm_decision})
    assert entries == {}


def test_build_spec_entries_uses_deterministic_fallback_when_llm_unavailable():
    mapping = ColumnMapping(
        column_name="weird_col",
        candidates=[_candidate("customer_region", 0.3)],
        best_field="customer_region",
        bucket="unmapped",
    )
    entries = build_spec_entries([mapping], llm_decisions={})
    assert entries["customer_region"]["provenance"]["method"] == "deterministic_fallback"


def test_next_version_starts_at_one_and_increments(unique_tenant_id):
    with Session(engine) as session:
        source = Source(tenant_id=unique_tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        version, parent = next_version(session, unique_tenant_id, source.id)
        assert version == 1
        assert parent is None

        save_mapping_spec(session, unique_tenant_id, source.id, {"customer_email": {}})

        version, parent = next_version(session, unique_tenant_id, source.id)
        assert version == 2
        assert parent == 1


def test_save_mapping_spec_persists_draft_row(unique_tenant_id):
    with Session(engine) as session:
        source = Source(tenant_id=unique_tenant_id, name="crm", kind="crm")
        session.add(source)
        session.commit()
        session.refresh(source)

        spec = save_mapping_spec(
            session,
            unique_tenant_id,
            source.id,
            {"customer_email": {"source_column": "email"}},
        )

        assert spec.version == 1
        assert spec.status == "draft"
        assert spec.parent_version is None
        assert spec.spec_json == {"customer_email": {"source_column": "email"}}

        fetched = session.get(MappingSpec, spec.id)
        assert fetched is not None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mapping_spec.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.mapping_spec'`.

- [ ] **Step 3: Write `backend/app/mapping_spec.py`**

```python
"""Mapping-spec builder + versioning.

Turns Day 2's per-column scoring output (app.scoring.ColumnMapping) plus
optional Day 3 LLM tie-break decisions (app.llm_mapper.LLMDecision) into
a versioned mapping-spec JSON document and persists it as a new
mapping_specs row. Never edits an existing spec row in place — each
call creates version N+1. Pure, DB-free functions (build_spec_entries)
are kept separate from the two that touch a Session, so the assembly
logic is testable without a database.
"""

from uuid import UUID

from sqlmodel import Session, select

from app.canonical import FieldType, get_field
from app.llm_mapper import LLMDecision
from app.models import MappingSpec
from app.scoring import ColumnMapping

_TRANSFORM_BY_FIELD_TYPE: dict[FieldType, dict] = {
    FieldType.STRING: {"function": "trim", "params": {}},
    FieldType.EMAIL: {"function": "trim", "params": {}},
    FieldType.PHONE: {"function": "trim", "params": {}},
    FieldType.DATE: {"function": "parse_date", "params": {}},
    FieldType.DATETIME: {"function": "parse_date", "params": {}},
    FieldType.ENUM: {"function": "map_enum", "params": {}},
    FieldType.INTEGER: {"function": "to_int", "params": {}},
    FieldType.MONEY_MINOR_UNITS: {"function": "to_minor_units", "params": {}},
    FieldType.CURRENCY_CODE: {"function": "trim", "params": {}},
}


def build_spec_entries(
    mappings: list[ColumnMapping], llm_decisions: dict[str, LLMDecision]
) -> dict[str, dict]:
    """Returns {canonical_field_name: {source_column, transform, provenance}}.

    A column is included only if it has a real decision: a confident
    deterministic bucket, an LLM decision that isn't "UNKNOWN", or (when
    the LLM was unavailable for an unmapped column) the deterministic
    best guess, tagged "deterministic_fallback". A column with none of
    these is left out of the spec entirely.
    """
    entries: dict[str, dict] = {}
    for mapping in mappings:
        llm_decision = llm_decisions.get(mapping.column_name)

        if llm_decision is not None:
            if llm_decision.canonical_field == "UNKNOWN":
                continue
            field_name = llm_decision.canonical_field
            provenance = {
                "method": "llm",
                "model": llm_decision.model,
                "confidence": round(llm_decision.confidence, 4),
                "reasoning": llm_decision.reasoning,
            }
        elif mapping.best_field is not None and mapping.candidates:
            field_name = mapping.best_field
            method = "deterministic" if mapping.bucket != "unmapped" else "deterministic_fallback"
            provenance = {
                "method": method,
                "model": None,
                "confidence": mapping.candidates[0].confidence,
                "reasoning": None,
            }
        else:
            continue

        field = get_field(field_name)
        entries[field_name] = {
            "source_column": mapping.column_name,
            "transform": _TRANSFORM_BY_FIELD_TYPE[field.type],
            "provenance": provenance,
        }
    return entries


def next_version(
    session: Session, tenant_id: str, source_id: UUID
) -> tuple[int, int | None]:
    latest = session.exec(
        select(MappingSpec)
        .where(MappingSpec.tenant_id == tenant_id, MappingSpec.source_id == source_id)
        .order_by(MappingSpec.version.desc())
    ).first()
    if latest is None:
        return 1, None
    return latest.version + 1, latest.version


def save_mapping_spec(
    session: Session, tenant_id: str, source_id: UUID, spec_entries: dict[str, dict]
) -> MappingSpec:
    version, parent_version = next_version(session, tenant_id, source_id)
    spec = MappingSpec(
        tenant_id=tenant_id,
        source_id=source_id,
        version=version,
        spec_json=spec_entries,
        status="draft",
        parent_version=parent_version,
    )
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return spec
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mapping_spec.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/mapping_spec.py backend/tests/test_mapping_spec.py
git commit -m "feat: add mapping-spec builder and versioning"
```

---

## Task 4: `POST /mapping-spec` endpoint

**Files:**
- Create: `backend/app/routers/mapping_spec.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_mapping_spec_endpoint.py`

**Interfaces:**
- Consumes: `app.db.get_session`, `app.models.OnboardingBatch`, `app.models.RawRecord`, `app.models.Source` (existing), `app.profiling.build_dataframe`, `app.profiling.profile_all_columns` (Day 2), `app.scoring.candidate_pool`, `app.scoring.score_all_columns` (Day 2, Task 1), `app.llm_mapper.tiebreak` (Task 2), `app.mapping_spec.build_spec_entries`, `app.mapping_spec.save_mapping_spec` (Task 3).
- Produces: `POST /mapping-spec` (form: `tenant_id`, `batch_id`) → `200 {"mapping_spec_id": str, "version": int, "parent_version": int | None, "status": str, "spec_json": dict}`; `404` if the batch doesn't exist or belongs to a different tenant; `400` if `batch_id` isn't a valid UUID or the batch has no raw records.

This test makes real Groq calls for the batch's genuinely-unmappable columns (`first_name`, `last_name` in `crm_tiny.csv` have no canonical equivalent) — the one place in the suite, besides Task 2's monkeypatched unit tests, that exercises the live LLM chain, mirroring how Day 2's `test_profile_endpoint.py` was the one real-fastembed test.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mapping_spec_endpoint.py
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import MappingSpec

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


def test_mapping_spec_maps_confident_columns_deterministically(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["parent_version"] is None
    assert body["status"] == "draft"

    spec_json = body["spec_json"]
    assert spec_json["customer_email"]["source_column"] == "email"
    assert spec_json["customer_email"]["provenance"]["method"] == "deterministic"

    # first_name/last_name have no canonical equivalent — the LLM should
    # say UNKNOWN for both, so neither appears anywhere in the spec.
    mapped_source_columns = {entry["source_column"] for entry in spec_json.values()}
    assert "first_name" not in mapped_source_columns
    assert "last_name" not in mapped_source_columns


def test_mapping_spec_versions_increment_on_repeat_calls(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    first = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )
    second = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert first.json()["version"] == 1
    assert second.json()["version"] == 2
    assert second.json()["parent_version"] == 1

    with Session(engine) as session:
        specs = session.exec(
            select(MappingSpec).where(MappingSpec.tenant_id == unique_tenant_id)
        ).all()
        assert len(specs) == 2


def test_mapping_spec_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/mapping-spec",
        data={
            "tenant_id": unique_tenant_id,
            "batch_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404


def test_mapping_spec_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post(
        "/mapping-spec", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"}
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mapping_spec_endpoint.py -v
```

Expected: FAIL — `404` on `/mapping-spec` (route doesn't exist yet).

- [ ] **Step 3: Write `backend/app/routers/mapping_spec.py`**

```python
"""POST /mapping-spec — builds and saves a versioned mapping spec.

Reuses Day 2's profiling + scoring fresh (self-contained, like
POST /profile), then calls the Day 3 LLM tie-breaker only for columns
the deterministic scorer bucketed "unmapped".
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.llm_mapper import LLMDecision, tiebreak
from app.mapping_spec import build_spec_entries, save_mapping_spec
from app.models import OnboardingBatch, RawRecord, Source
from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, score_all_columns

router = APIRouter()


@router.post("/mapping-spec")
def create_mapping_spec(
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

    raw_rows = session.exec(
        select(RawRecord).where(RawRecord.batch_id == batch.id)
    ).all()
    if not raw_rows:
        raise HTTPException(status_code=400, detail="Batch has no raw records")

    source = session.get(Source, batch.source_id)
    source_kind = source.kind if source else None

    df = build_dataframe([row.raw_json for row in raw_rows])
    stats_list = profile_all_columns(df)
    stats_by_column = {stats.column_name: stats for stats in stats_list}
    mappings = score_all_columns(stats_by_column, source_kind)

    pool = candidate_pool(source_kind)
    llm_decisions: dict[str, LLMDecision] = {}
    for mapping in mappings:
        if mapping.bucket != "unmapped":
            continue
        decision = tiebreak(mapping.column_name, stats_by_column[mapping.column_name], pool)
        if decision is not None:
            llm_decisions[mapping.column_name] = decision

    spec_entries = build_spec_entries(mappings, llm_decisions)
    spec = save_mapping_spec(session, tenant_id, batch.source_id, spec_entries)

    return {
        "mapping_spec_id": str(spec.id),
        "version": spec.version,
        "parent_version": spec.parent_version,
        "status": spec.status,
        "spec_json": spec.spec_json,
    }
```

- [ ] **Step 4: Wire the router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.routers.mapping_spec import router as mapping_spec_router
from app.routers.profile import router as profile_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)
app.include_router(profile_router)
app.include_router(mapping_spec_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_mapping_spec_endpoint.py -v
```

Expected: PASS (4 tests). Requires `db` reachable and a working `GROQ_API_KEY` in `backend/.env` (or a running local Ollama with `llama3` pulled, as the fallback) since `first_name`/`last_name` will genuinely hit the LLM tie-breaker.

- [ ] **Step 6: Run the full backend test suite twice in a row**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: PASS both times.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/mapping_spec.py backend/app/main.py backend/tests/test_mapping_spec_endpoint.py
git commit -m "feat: add POST /mapping-spec endpoint (LLM tie-break + versioned spec)"
```

---

## Task 5: Benchmark eval — fast deterministic regression test + on-demand CLI script

**Files:**
- Create: `backend/tests/test_mapping_benchmark.py`
- Create: `backend/seed/eval_mapping.py`

**Interfaces:**
- Consumes: `app.profiling.build_dataframe`, `app.profiling.profile_all_columns` (Day 2), `app.scoring.candidate_pool`, `app.scoring.name_similarity`, `app.scoring.score_all_columns` (Day 2, Task 1), `app.llm_mapper.tiebreak` (Task 2, CLI script only), `seed.generate_seed_data.generate`, `seed.generate_seed_data.OUTPUT_DIR` (existing).
- Produces: a pytest test (no new importable interface — it's a regression guard), and `backend/seed/eval_mapping.py`'s `run() -> None`, runnable as `uv run python -m seed.eval_mapping`.

- [ ] **Step 1: Write the fast deterministic benchmark test**

```python
# backend/tests/test_mapping_benchmark.py
"""Fast, deterministic regression guard: the full deterministic hybrid
scorer (fuzzy + embedding + type) must not score worse than a pure-fuzzy
baseline on the labeled seed benchmark. No LLM calls, no network — this
runs on every `pytest` invocation. The three-way comparison that adds
the LLM tie-break and prints the full metrics table is
`backend/seed/eval_mapping.py`, run on demand.
"""

import csv
import json

from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, name_similarity, score_all_columns
from seed.generate_seed_data import OUTPUT_DIR, generate

FILE_SOURCE_KIND = {
    "crm_snake.csv": "crm",
    "crm_legacy.csv": "crm",
    "billing.csv": "billing",
    "support.csv": "support",
}


def _baseline_best_field(column_name: str, source_kind: str) -> str | None:
    pool = candidate_pool(source_kind)
    if not pool:
        return None
    best = max(pool, key=lambda f: name_similarity(column_name, f))
    return best.name


def test_hybrid_scorer_is_at_least_as_accurate_as_fuzzy_baseline():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())[
        "column_mappings"
    ]

    baseline_correct = 0
    hybrid_correct = 0
    total_mapped = 0

    for filename, mapping in ground_truth.items():
        source_kind = FILE_SOURCE_KIND[filename]
        with (OUTPUT_DIR / filename).open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        df = build_dataframe(rows)
        stats_by_column = {s.column_name: s for s in profile_all_columns(df)}
        hybrid_mappings = {
            m.column_name: m for m in score_all_columns(stats_by_column, source_kind)
        }

        for column_name, true_field in mapping.items():
            if true_field is None:
                continue
            total_mapped += 1

            if _baseline_best_field(column_name, source_kind) == true_field:
                baseline_correct += 1

            hybrid_mapping = hybrid_mappings[column_name]
            hybrid_field = (
                hybrid_mapping.best_field if hybrid_mapping.bucket != "unmapped" else None
            )
            if hybrid_field == true_field:
                hybrid_correct += 1

    assert total_mapped >= 40
    baseline_accuracy = baseline_correct / total_mapped
    hybrid_accuracy = hybrid_correct / total_mapped
    print(f"\nfuzzy-only baseline accuracy@1: {baseline_accuracy:.2%}")
    print(f"deterministic hybrid accuracy@1: {hybrid_accuracy:.2%}")

    assert hybrid_accuracy >= baseline_accuracy
```

- [ ] **Step 2: Run it**

```bash
cd backend && uv run pytest tests/test_mapping_benchmark.py -v -s
```

Expected: PASS. `-s` shows the printed accuracy numbers — confirm `total_mapped >= 40` and note both percentages for later comparison against Step 4's full three-way run.

- [ ] **Step 3: Commit the fast test**

```bash
git add backend/tests/test_mapping_benchmark.py
git commit -m "test: add fast deterministic benchmark regression guard (hybrid vs fuzzy baseline)"
```

- [ ] **Step 4: Write the CLI eval script**

```python
# backend/seed/eval_mapping.py
"""Benchmark eval script — CLI, on-demand.

Compares three passes on the labeled seed benchmark:
1. baseline: pure rapidfuzz name-matching only.
2. hybrid: rapidfuzz + fastembed + type/format signal (Day 2, no LLM).
3. full: hybrid, plus an LLM tie-break for every column Day 2 buckets
   "unmapped" (Day 3).

Pass 3 makes real Groq/Ollama calls — only run this on demand, not as
part of the regular test suite (see tests/test_mapping_benchmark.py for
the fast, LLM-free regression guard). Run with:

    uv run python -m seed.eval_mapping
"""

import csv
import json

from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, name_similarity, score_all_columns
from app.llm_mapper import tiebreak
from seed.generate_seed_data import OUTPUT_DIR, generate

FILE_SOURCE_KIND = {
    "crm_snake.csv": "crm",
    "crm_legacy.csv": "crm",
    "billing.csv": "billing",
    "support.csv": "support",
}

_TOP_K = 3


def _baseline_ranked_fields(column_name: str, source_kind: str) -> list[str]:
    pool = candidate_pool(source_kind)
    ranked = sorted(pool, key=lambda f: name_similarity(column_name, f), reverse=True)
    return [f.name for f in ranked]


def _score(true_field: str | None, predicted_top1: str | None, ranked: list[str]) -> dict:
    is_positive_prediction = predicted_top1 is not None
    is_correct_top1 = true_field is not None and predicted_top1 == true_field
    is_correct_topk = true_field is not None and true_field in ranked[:_TOP_K]
    return {
        "tp": 1 if is_correct_top1 else 0,
        "fp": 1 if (is_positive_prediction and not is_correct_top1) else 0,
        "fn": 1 if (true_field is not None and not is_correct_top1) else 0,
        "correct_top1": 1 if is_correct_top1 else 0,
        "correct_topk": 1 if is_correct_topk else 0,
    }


def _print_pass(
    name: str, mapped_total: int, tp: int, fp: int, fn: int, correct_top1: int, correct_topk: int
) -> None:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    print(
        f"{name:<10}"
        f"{correct_top1 / mapped_total:>10.2%}"
        f"{correct_topk / mapped_total:>10.2%}"
        f"{precision:>12.2%}"
        f"{recall:>10.2%}"
    )


def run() -> None:
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())[
        "column_mappings"
    ]

    mapped_total = 0
    baseline = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    hybrid = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    full_correct = 0

    for filename, mapping in ground_truth.items():
        source_kind = FILE_SOURCE_KIND[filename]
        with (OUTPUT_DIR / filename).open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        df = build_dataframe(rows)
        stats_by_column = {s.column_name: s for s in profile_all_columns(df)}
        hybrid_mappings = {
            m.column_name: m for m in score_all_columns(stats_by_column, source_kind)
        }
        pool = candidate_pool(source_kind)

        for column_name, true_field in mapping.items():
            baseline_ranked = _baseline_ranked_fields(column_name, source_kind)
            baseline_top1 = baseline_ranked[0] if baseline_ranked else None
            for key, value in _score(true_field, baseline_top1, baseline_ranked).items():
                baseline[key] += value

            hybrid_mapping = hybrid_mappings[column_name]
            hybrid_ranked = [c.canonical_field for c in hybrid_mapping.candidates]
            hybrid_top1 = (
                hybrid_mapping.best_field if hybrid_mapping.bucket != "unmapped" else None
            )
            for key, value in _score(true_field, hybrid_top1, hybrid_ranked).items():
                hybrid[key] += value

            if true_field is None:
                continue
            mapped_total += 1

            if hybrid_mapping.bucket != "unmapped":
                full_field = hybrid_top1
            else:
                decision = tiebreak(column_name, stats_by_column[column_name], pool)
                if decision is not None and decision.canonical_field != "UNKNOWN":
                    full_field = decision.canonical_field
                else:
                    full_field = hybrid_mapping.best_field  # deterministic_fallback
            if full_field == true_field:
                full_correct += 1

    print(f"Labeled benchmark: {mapped_total} mapped columns\n")
    print(f"{'pass':<10}{'acc@1':>10}{'acc@' + str(_TOP_K):>10}{'precision':>12}{'recall':>10}")
    _print_pass("baseline", mapped_total, **baseline)
    _print_pass("hybrid", mapped_total, **hybrid)
    print(f"{'full':<10}{full_correct / mapped_total:>10.2%}  (LLM tie-break adds no ranked list beyond top-1)")


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run it**

```bash
cd backend && uv run python -m seed.eval_mapping
```

Expected: prints the labeled-benchmark size and a three-row table (`baseline`, `hybrid`, `full`) with accuracy@1, accuracy@3, precision, and recall for `baseline`/`hybrid`, and accuracy@1 for `full`. Confirm `full`'s accuracy@1 is `>=` `hybrid`'s — this is the "AI > baseline" printout the Day 3 deliverable asks for. Requires `db` NOT required (script is standalone), but does require `GROQ_API_KEY` set (or Ollama running locally) since it calls `tiebreak` for real on every `unmapped`-bucket column across all four files.

- [ ] **Step 6: Commit**

```bash
git add backend/seed/eval_mapping.py
git commit -m "feat: add benchmark eval CLI script (baseline vs hybrid vs hybrid+LLM)"
```
