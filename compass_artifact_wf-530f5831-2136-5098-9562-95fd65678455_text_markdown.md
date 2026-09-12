# Build Plan: An AI-Assisted Customer Data Onboarding & Integration Pipeline (Forward-Deployed Portfolio Project)

## TL;DR
- **Build a FastAPI + Postgres + Next.js pipeline that ingests a messy customer file, auto-profiles it, proposes a versioned column mapping using a hybrid of local embeddings + a free LLM, routes ambiguous mappings to a human review UI, then runs a deterministic transform → validation → load with an exception queue.** This is buildable in one week and hits every signal Palantir-style FDE screens look for: handling incomplete requirements, protecting data quality, and pivoting when requirements change.
- **For the zero-budget AI layer, use Groq's free tier as the primary LLM (30 req/min, 6,000 tokens/min, 14,400 req/day at the org level, no credit card), Google AI Studio (Gemini Flash) and Cerebras (1M tokens/day) as fallbacks, and run embeddings locally for free with `fastembed`/`sentence-transformers` (all-MiniLM-L6-v2). The LLM only touches the ~10-20% of columns that deterministic signals can't resolve confidently.**
- **Deploy free on Neon (Postgres) + Render free web service + Vercel (frontend), and record a 3-5 minute demo. The single most important design principle to state repeatedly in your README and interview: "the LLM proposes, the deterministic engine executes" — mappings are reviewable, versioned JSON, never live model calls in the hot path.**

---

## Key Findings

1. **This brief is a near-exact replica of a Palantir/OpenAI/Anthropic Forward-Deployed Engineer (FDE/FDSE) take-home.** These roles explicitly screen for handling under-defined prompts, data integration and data-quality problems, and customer-facing ambiguity. Building this project *is* the interview prep.
2. **A zero-budget AI setup is genuinely viable in 2026** because (a) multiple providers offer permanent no-credit-card free tiers, and (b) the hard part of schema matching — embedding column names and values — runs locally for free. The LLM is a small, swappable component, not the core.
3. **The correct architecture is "LLM proposes, deterministic engine executes."** Every serious 2026 source on production LLM data pipelines converges on this: use constrained/structured output for the AI, then a versioned mapping spec that a deterministic, testable engine applies. This is also the single most defensible design decision in an interview.
4. **The trending, role-relevant stack is FastAPI + Pydantic v2 + Polars/DuckDB + Postgres/SQLModel + a Next.js/shadcn/ui frontend.** These are the exact tools showing up in 2026 data-engineering and AI-engineering job posts.
5. **Free hosting narrowed in 2025-2026** — Railway and Fly.io killed their free tiers; Render's free web tier survives (with 15-min sleep) and Neon/Supabase remain the best free Postgres. Plan around this.

---

## Details

### 1. TECH STACK RECOMMENDATION

#### Backend framework — **Recommendation: FastAPI**
FastAPI is the default modern Python backend in 2026 and is what AI/data-engineering job posts expect. It gives you async I/O, automatic OpenAPI docs (great for the demo), and native Pydantic v2 integration.
- **Alternatives considered:** *Django REST* (too heavyweight for a one-week API-first project, but better if you wanted a built-in admin); *Flask* (fine but you'd rebuild validation/serialization by hand); *Litestar* (excellent, arguably cleaner DI, but smaller ecosystem and less interview name-recognition). **Pick FastAPI** — maximum ecosystem, maximum recognizability, best Pydantic fit.
- Language: Python primary. Keep the frontend in TypeScript.

#### Data validation — **Recommendation: Pydantic v2 for API/records + Pandera for DataFrame validation**
These solve *different* layers and you should use both, which is itself a great interview talking point:
- **Pydantic v2** — validate API request/response bodies, the mapping-spec JSON, and per-record canonical objects. It's the ecosystem standard; v2's core is written in Rust and is fast.
- **Pandera** — validate the *tabular* data during transformation with typed DataFrame schemas (`coerce=True` to normalize types, uniqueness/nullability checks). It's Python-first, lightweight, and runs inline in your pipeline code — ideal for a one-week build on pandas/Polars.
- **Honest comparison of the four you asked about:**
  - *Great Expectations* — the most powerful, generates human-readable "Data Docs" HTML reports (which look impressive) and acts as a data contract, but it's heavy: it needs a DataContext, has a real learning curve, and can be over-engineering for one week. GX Core is free/Apache-2.0; GX Cloud is paid for teams.
  - *Soda Core* — declarative YAML/SQL checks, fast to stand up (checks in minutes), great for *monitoring* an existing warehouse; less natural for row-level, in-pipeline, Python-native transformation validation. Note Soda's `scan.yml` requires exact 2-space indentation or it silently fails.
  - *Pandera vs GX*: the consensus is GX for multi-team, multi-engine governance (Spark/SQL/pandas + shared expectation suites); Pandera for pure-Python/pandas/Polars in-code contracts. For this project, **Pandera** is the right fit. (Neither GX nor Soda treats column-type schema validation as its primary job — another reason to pair with Pandera/Pydantic.)
  - **Verdict:** Pydantic v2 (records/API) + Pandera (DataFrames). Mention GX as "what I'd add for a multi-team production deployment with shareable Data Docs."

#### Data profiling — **Recommendation: Polars + DuckDB for the engine, `ydata-profiling` for the impressive report**
- **Polars + DuckDB** are *the* trending in-process analytics combo of 2026 — Rust/C++ speed, lazy evaluation, and zero-copy Arrow interchange (a DuckDB query result becomes a Polars DataFrame directly via `.pl()`). Use **DuckDB** to read messy CSV/Excel/JSON directly with SQL (`read_csv('...', AUTO_DETECT=TRUE)`) and do heavy filtering; use **Polars** for transformations. This is a strong, current résumé signal — sources describe the "DuckDB + Polars + pandas" workflow as the 2026 direction for tabular processing.
- **`ydata-profiling`** — generates a rich HTML EDA report from a pandas DataFrame in one call. Use it to produce a "profile report" artifact per upload; it's visually impressive in a demo. **Version caveat:** it started as pandas-profiling, became `ydata-profiling` (Feb 2023), and was renamed again to **`fg-data-profiling` in April 2026** (a drop-in replacement); the original `ydata-profiling` still installs and runs but no longer receives updates — pin your version. It's pandas-only and slow on millions of rows — fine for sample files; down-sample if needed.
- For your *own* profiling logic (which you should write to demonstrate understanding), compute per-column: inferred type, null %, distinct count/cardinality, sample values, min/max/mean for numerics, detected format via regex (dates, emails, currency, phone), and candidate primary-key detection (high uniqueness).

#### Job/queue layer — **Recommendation: a Postgres-backed job table + FastAPI, OR ARQ if you want a real queue**
In one week, realism matters:
- **Simplest that impresses:** a `jobs` table in Postgres with status (`queued/running/succeeded/failed`), plus FastAPI `BackgroundTasks` or a tiny worker loop. This keeps infra to one service and lets you talk intelligently about idempotency and retries. (Know the limit: `BackgroundTasks` runs in-process and won't survive a worker crash — only use it for short, idempotent, loss-tolerant work.)
- **If you want a "real" queue on your résumé:** **ARQ** (async-native, Redis-backed, pairs naturally with async FastAPI, much lighter than Celery) or **RQ** (simplest Redis queue).
- **Honest comparison:** *Celery* is the industry veteran and most recognizable, but it's heavy, sync-first, and a time sink to configure well in a week (silent Redis-disconnect task loss, `result_expires` gotchas). *Redis Streams* is powerful but low-level. *Temporal* is the gold standard for durable multi-step workflows with compensation/retries and durable state — it is the "correct" answer for a production onboarding pipeline and a *fantastic* thing to mention as "what I'd use in production," but it's too much to learn and run in one week. **Verdict:** Postgres-backed job table for the build (defensible, simple, idempotent), and say "I'd move to Temporal for durable workflow orchestration in production." If you have spare time on Day 5, swap in ARQ.

#### Database — **Recommendation: Postgres + SQLModel + Alembic**
- **Postgres** — the obvious choice; you need referential integrity, JSONB (for storing mapping specs, profile reports, and error objects), and upserts (`INSERT ... ON CONFLICT`).
- **SQLModel** (SQLAlchemy + Pydantic, from FastAPI's author) — one model class is both your ORM table and your Pydantic schema, eliminating the schema-drift class of bugs; production DB behavior is identical to SQLAlchemy since it's built on it. **Alternative:** plain SQLAlchemy 2.0 gives maximum control for complex domains; SQLModel is the better greenfield-FastAPI pick. Use SQLModel.
- **Alembic** for migrations — non-negotiable and directly demonstrates the "schema evolution" requirement. `alembic revision --autogenerate` + `alembic upgrade head`. When the customer "adds a new required field," you show a real migration. Frame Alembic as "version control for your database schema."

#### Frontend — **Recommendation: Next.js + shadcn/ui + TanStack Table** (with Streamlit as the fallback if you fall behind)
- The mapping-review UI is the visual centerpiece of your demo, so it's worth real effort. **Next.js (App Router) + shadcn/ui + TanStack Table** is the trending 2026 combo; shadcn/ui is MIT-licensed and copies source into your repo (you own the code), and TanStack Table is headless — it computes sorting/filtering/row-selection logic while you own every `<td>`. There are ready-made shadcn data-table templates (e.g., Tablecn) to accelerate this.
  - *Version gotcha to know:* TanStack Table's docs moved to **v9 in August 2026** with API changes (`useReactTable` → `useTable`, `get*RowModel` → a features object, `flexRender` → a component) that most tutorials haven't caught up to, and the change wasn't in the changelog. **Pin v8** and follow v8 guides to avoid churn, or knowingly adopt v9.
- **Honest trade-off:** *Streamlit* would let you build the entire UI in pure Python in a day — hugely time-efficient and fine for a data tool — but it looks like a prototype, not a product. *React + Vite* is lighter than Next.js if you don't need SSR/routing. **Verdict:** Next.js + shadcn/ui if you can afford ~2 days on frontend; **Streamlit as the explicit fallback** in your cut-list if you're behind. A polished Next.js UI meaningfully raises the "this looks like a real product" signal for interviewers.

#### File handling — Python `pandas`/`polars` readers + DuckDB
- CSV/Excel via `polars.read_csv`, `read_excel` (or DuckDB auto-detect), JSON via `polars.read_json`/`pandas`. Handle encoding detection (`charset-normalizer`), delimiter sniffing, and Excel multi-sheet. Cap upload size and stream large files.

#### Mock API creation
- Build a tiny separate FastAPI "mock CRM" service that serves paginated JSON from a seeded dataset (deliberately messy), so "connect a mock API" is a real code path, not a stub. This demonstrates real API integration (pagination, auth header, retries).

#### Containerization — **Docker Compose**
- One `docker-compose.yml` with services: `api` (FastAPI), `worker` (if used), `db` (Postgres), `frontend` (Next.js), optional `redis`, and `mock-crm`. This is table stakes for the "system integrity" and "move to VPC" story.

#### What's genuinely trending in 2026 for these roles
FastAPI, Pydantic v2, **Polars + DuckDB**, SQLModel/SQLAlchemy 2.0, structured-output LLM tooling (Instructor/Outlines), shadcn/ui + TanStack Table, and Docker Compose. Splink for entity resolution is a standout niche skill. Use these signals as "current and intentional," not trend-chasing — always tie each back to why it fits *this* problem.

---

### 2. SYSTEM DESIGN / ARCHITECTURE

**End-to-end flow:**
```
Upload/Mock-API → Ingest (raw landing) → Profile → AI-assisted Mapping Proposal
   → Human Review UI → Versioned Mapping Spec (JSON + version row)
   → Deterministic Transform → Validation → Load into Canonical Schema
                                     ↓ (invalid)
                               Exception Queue → Fix/Resubmit
```

**Stages:**
1. **Ingestion** — accept file or pull from mock API. Store the *raw* bytes/rows unchanged in a `raw_records` / landing area (JSONB), tagged with a `source_id`, `batch_id`, and `content_hash`. Never mutate raw data — this is auditability and lets you re-run.
2. **Profiling** — compute the column profile (types, nulls, cardinality, sample values, regex-detected formats) + optional `ydata-profiling` HTML artifact.
3. **AI-assisted mapping** — hybrid scorer (see §3) produces, for each source column, a ranked list of canonical-field candidates with confidence. High-confidence → auto-map; low-confidence → flagged for review.
4. **Human review UI** — reviewer sees each source column, its samples, the proposed canonical field, confidence, and alternatives; can accept/override. Every decision is recorded.
5. **Versioned mapping spec** — the confirmed mapping is saved as a JSON document with a version number (see below).
6. **Deterministic transform** — a pure function applies the mapping spec to raw rows → canonical rows. No LLM calls here. Fully unit-testable.
7. **Validation** — Pandera + custom rules (see §4). Valid rows proceed; invalid rows become exception records.
8. **Load** — upsert into canonical tables using natural keys + idempotency (see below).
9. **Exception queue** — invalid records land in a `quarantine` table with structured error objects, explanations, and suggested fixes.

#### Canonical schema (risk-platform flavored)
```sql
-- tenant isolation on every table
customers(
  id PK, tenant_id, natural_key,           -- e.g. source_system + source_customer_id
  legal_name, display_name, email, phone,
  country, industry, risk_tier,
  created_at, updated_at, content_hash, source_batch_id
)
accounts(
  id PK, tenant_id, customer_id FK→customers,
  account_number, status, opened_at, ...
)
invoices(
  id PK, tenant_id, customer_id FK→customers, account_id FK→accounts,
  invoice_number, amount_minor_units, currency, issue_date, due_date, status,
  content_hash, source_batch_id
)
support_tickets(
  id PK, tenant_id, customer_id FK→customers,
  ticket_ref, subject, priority, status, opened_at, closed_at
)

-- meta/control tables
sources(id, tenant_id, name, kind)                    -- crm/billing/support
onboarding_batches(id, tenant_id, source_id, status, mapping_spec_version, metrics_json, created_at)
column_profiles(id, batch_id, column_name, profile_json)
mapping_specs(id, tenant_id, source_id, version, spec_json, status, created_by, created_at, parent_version)
mapping_reviews(id, mapping_spec_id, column_name, proposed, chosen, confidence, decided_by, decided_at)
quarantine(id, tenant_id, batch_id, raw_record_json, error_codes[], severity, explanation, suggested_fix, status)
```
Store money as integer **minor units** (`amount_minor_units` + `currency`) — never floats. This is a classic "did they think about it" detail interviewers love.

#### Mapping-spec versioning
- A **mapping spec** is a JSON document: for each canonical field, the source column(s), transform function name + params (e.g., `parse_date(fmt="DMY")`, `split_name`, `to_minor_units`), and provenance (auto vs human-confirmed, model, confidence).
- Store each spec as a **row in `mapping_specs`** with an incrementing `version` and a `parent_version`. Never edit in place. A run records *which spec version* it used (`onboarding_batches.mapping_spec_version`). This gives full reproducibility: "run #12 used mapping v3." Diffing v3→v4 shows exactly what changed when the customer added a field.

#### Idempotency design
- **Natural key** per entity (e.g., `source_system + source_customer_id`).
- **Content hash**: `sha256` of the normalized canonical record. Store it. On load, upsert with `INSERT ... ON CONFLICT (tenant_id, natural_key) DO UPDATE ... WHERE content_hash <> excluded.content_hash` — so unchanged rows are no-ops and retries never create duplicates.
- **Idempotency key** per batch/request (client-supplied `Idempotency-Key` header, stored) so re-submitting the same file doesn't double-load. This mirrors how Stripe-style APIs do it — a great thing to name-drop.
- **Dedupe**: within a batch, collapse rows with identical natural keys; across the dataset, run fuzzy dedupe (see §4) to catch "same customer, slightly different name."

#### Retries
- Every stage is re-runnable because raw data is immutable and loads are idempotent. A failed batch can be retried from any stage. Record attempt count and last error on the job row. Use exponential backoff for the LLM/mock-API calls.

#### Schema evolution / new required field mid-project
This is the money demo. When the customer adds a new required field (say `risk_tier`):
1. `alembic revision --autogenerate` adds the nullable column, then a data-backfill migration.
2. Create **mapping spec v(n+1)** adding the new field mapping — old runs still reference their old spec, so nothing breaks.
3. Add the new validation rule as **enabled-from-version** so historical batches aren't retroactively marked invalid (rules are versioned too, or scoped by `effective_date`).
4. Re-run only the new/affected batches. Because loads are idempotent, re-running is safe.
Being able to *show* this live — "watch me add a required field and re-onboard without breaking prior data" — directly satisfies the brief's "Adaptability" signal.

---

### 3. THE AI LAYER (done properly)

#### The pattern: "LLM proposes, deterministic engine executes"
The LLM only ever produces a *proposed mapping spec* (reviewable JSON). A deterministic, unit-tested engine applies it. This means: reproducibility, no LLM in the data hot path, cheap re-runs, and full auditability. State this explicitly everywhere — it's the core FDE-grade insight, and both academic work (Magneto, LLMatch, "It's AI Match") and practitioner write-ups back the pattern: always add a validation layer after AI mapping, persist successful mappings, and keep humans in the loop for critical integrations.

#### Hybrid mapping scorer (deterministic first, LLM last)
For each source column, combine signals into a confidence score:
1. **String similarity** on column names — normalize (lowercase, strip, snake/camel split), then `rapidfuzz` token-set ratio / Levenshtein against canonical field names and known aliases.
2. **Embedding cosine similarity** — embed the *column name* AND a synthesized "column description + sample values" string, compare (cosine) to embedded canonical field descriptions. Research shows instance/value-based embeddings catch cases pure name-matching misses (e.g., `primary_contact` vs `secondary_contact`, or disjoint-but-similar values). Run embeddings **locally & free** with `fastembed` (ONNX, `BAAI/bge-small-en-v1.5` default) or `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim, fast, free) — per the MTEB leaderboard these small models rank close to paid commercial embeddings at zero per-token cost.
3. **Type/format inference** — does the column's data look like the canonical field's expected type/format (date, currency, email regex)? Boost/penalize accordingly.
4. **LLM tie-breaker** — only for columns where the top candidates are close/low-confidence, send the column name + a few sample values + the shortlist of canonical candidates to the LLM and ask it to pick + explain. This keeps you far under free rate limits (you might make 3-10 LLM calls per file, not hundreds).

**Confidence thresholds (tune with your benchmark):**
- ≥ 0.85 → **auto-accept**
- 0.55–0.85 → **human-confirm** (pre-filled with best guess)
- < 0.55 → **unmapped**, force human choice

#### Reliable structured output from free models
- Prompt-only "return JSON" fails 5-20% of the time in production, usually silently. Use schema enforcement:
  - **Instructor** (Pydantic models as schema + automatic retry on validation failure) is the de-facto Python standard in 2026 (≈3M monthly downloads, 11k+ GitHub stars) and works across OpenAI-compatible endpoints (Groq, Gemini compat, local Ollama). This is your primary approach — easy and cross-provider (adds ~15-25% latency from retries, irrelevant at your volume).
  - **Ollama** supports native structured output via the `format` parameter (pass a JSON schema) for local models (Qwen 2.5/3, Phi) — token-level, so malformed JSON becomes mechanically hard.
  - **Outlines** for token-level constrained decoding if you run models locally/vLLM (2-3x faster, near-zero schema violations, <5% overhead) — mention as the "constrained decoding" option. Note the quality-vs-validity nuance: structured modes guarantee the *schema*, not the *content*, so you still need semantic validation.
- Always **parse → validate (Pydantic) → retry** and, on repeated failure, **fall back to the embedding/fuzzy score** (exactly what production schema-matchers like Magneto do: try LLM up to 3 times, then revert to embedding scores).

#### Prompt design
- **Column-mapping prompt:** give the model the canonical schema (field names + one-line descriptions), the source column name, its inferred type, and 5-10 sample values; ask for `{canonical_field, confidence, reasoning}` constrained to the enum of canonical fields (+ `"UNKNOWN"`). One example (one-shot) keeps tokens low. Force enum membership so it can't invent fields.
- **Transformation-suggestion prompt:** given source samples and target field, propose a transform from a *fixed catalogue* of transform functions (`parse_date`, `split_name`, `to_minor_units`, `trim`, `map_enum`, ...) with params — never free-form code. The deterministic engine only knows how to run catalogue functions, so the LLM can't inject arbitrary logic.

#### Evaluation of the AI component (this is what makes it stand out)
- Build a **small labeled benchmark**: ~40-80 messy source columns (from your seed data + hand-crafted nasties like `cust_id`, `CustomerID`, `client_ref`, `e-mail`, `amt_usd`, `dt_created`) each with a ground-truth canonical field.
- Compute **precision, recall, accuracy@1 and accuracy@k** for the mapping proposals.
- **Baseline comparison:** run a pure fuzzy-string matcher (rapidfuzz) as the baseline, then your hybrid (embeddings + LLM), and **report the delta**. Showing "fuzzy baseline = 68% acc@1, hybrid = 91% acc@1" *proves the AI adds value* — this quantified ablation is exactly the rigor FDE/AI-engineer interviewers reward, and most portfolio projects skip it.

---

### 4. VALIDATION & EXCEPTION DESIGN

#### Validation rule catalogue (risk-platform domain)
**Structural / type:**
- Required fields present & non-null (per canonical field, per mapping-spec version).
- Type coercion: strings→dates, strings→numbers, with failures captured not crashed (`coerce=True`, catch errors).
- **Date normalization**: detect and normalize `DD/MM/YYYY` vs `MM/DD/YYYY` vs ISO vs Excel serial; flag ambiguous dates (e.g., `03/04/2025`) for review.
- **Currency/amount**: parse `"$1,234.56"`, `"1.234,56"` (EU), currency symbols → minor units + ISO currency code; reject non-numeric.

**Referential integrity:**
- Every `invoice.customer_id` and `ticket.customer_id` resolves to a `customers` natural key (**orphan foreign key** detection).
- **Inconsistent customer IDs across systems** — CRM uses `C-1001`, billing uses `1001`; detect via normalization rules + fuzzy match on name/email, surface as a reconciliation exception.

**Business rules:**
- Invoice `issue_date` not in the future; `due_date ≥ issue_date`.
- No **negative amounts** on invoices (unless credit-note flagged).
- Enum validation (status ∈ {open, paid, void, ...}).
- Duplicate detection: exact (natural key/content hash) + **fuzzy dedupe**.

**Fuzzy dedupe:** use **Splink** (free, open-source, DuckDB backend, probabilistic Fellegi-Sunter record linkage) for "same customer, different spelling." Per Splink's official README (moj-analytical-services/splink) it is "capable of linking a million records on a laptop in around a minute," and Robin Linacre's benchmark deduplicated 7 million records in ~2 minutes (1B+ pairwise comparisons) on the DuckDB backend. It was developed by the **UK Ministry of Justice** and is adopted by the UK Dept for Business and Trade (Matchbox), the Welsh Revenue Authority, and cited in the *American Journal of Epidemiology* (2025) by Harvard Medical School/Vanderbilt for linkage of 8.1M death records — putting Splink on your résumé is a strong niche signal. For a lighter touch, `rapidfuzz` blocking + threshold works (reach for Splink when the blocking key is unstable or the data shape changes).

#### Exception queue design
Each invalid record → a structured **error object**:
```json
{
  "record_ref": "batch_42:row_318",
  "errors": [{
    "code": "REF_INTEGRITY_ORPHAN_FK",
    "field": "customer_id",
    "severity": "error",          // error | warning | info
    "message": "customer_id 'C-1001' not found in customers",
    "suggested_fix": "Map to existing customer 1001 (billing) — 0.93 name match",
    "raw_value": "C-1001"
  }],
  "status": "open"                 // open | fixed | ignored | resubmitted
}
```
- **Error taxonomy / codes**: `MISSING_REQUIRED`, `TYPE_COERCION_FAILED`, `DATE_AMBIGUOUS`, `AMOUNT_INVALID`, `REF_INTEGRITY_ORPHAN_FK`, `DUPLICATE_EXACT`, `DUPLICATE_FUZZY`, `BUSINESS_RULE_FUTURE_DATE`, `BUSINESS_RULE_NEGATIVE_AMOUNT`, `ENUM_INVALID`, `INCONSISTENT_ID_ACROSS_SOURCES`.
- **Severity** drives behavior: `error` → quarantine; `warning` → load but flag; `info` → log.
- **Workflow**: quarantine table → review UI shows the error + suggested fix → user edits value or edits mapping → **resubmit** (single or **bulk-fix** by error code, e.g., "apply DMY date format to all 214 DATE_AMBIGUOUS rows"). Resubmit re-runs transform+validate for just those rows; idempotent load means no duplicates.
- **Never fail silently** — the whole point of the brief. Every rejected row is visible, explained, and fixable.

---

### 5. METRICS

Instrument these and show them on a dashboard (and store per-batch in `onboarding_batches.metrics_json`):
- **Mapping accuracy** = (correct canonical mappings) / (total source columns), measured against your labeled benchmark and against human-confirmed decisions. Track **auto-map precision** separately (of columns auto-accepted at ≥0.85, what % did the human leave unchanged?).
- **Validation coverage** = (# of canonical fields/records touched by ≥1 validation rule) / (total). Shows you're not leaving fields unchecked. Also report rules-run count and rule pass/fail rates.
- **Correction rate** = (records sent to exception queue) / (total records), and (mappings a human overrode) / (mappings proposed). Downward trend across runs = system learning/improving.
- **Time-to-onboard** = wall-clock from upload to "all valid records loaded," broken into stage timings (profile / map / review / transform / validate / load). This is the headline FDE metric — "reduced onboarding from X to Y."

**Dashboard should show:** per-batch record counts (loaded / quarantined / warning), mapping confidence distribution, top error codes (bar chart), stage timings, and the auto-map vs human-confirm ratio.

**Making metrics real, not fake:** seed *deliberately messy* data (below) with a **known ground truth**, so accuracy/correction numbers are computed against real labels, not asserted. Run the pipeline 3-4 times improving thresholds/aliases and show the metrics moving. That before/after is the story.

---

### 6. DAY-BY-DAY 7-DAY BUILD PLAN

**Day 1 — Foundations & data.** Repo + Docker Compose (Postgres, api). SQLModel canonical schema + Alembic initial migration. Write the **seed-data generator** producing messy CRM/billing/support files with a ground-truth mapping + labels. Get `POST /upload` storing raw records + a `jobs`/`batches` row. *Deliverable: you can upload a file and see raw rows persisted.*

**Day 2 — Profiling + deterministic mapping.** Column profiler (types, nulls, cardinality, formats, samples) + `ydata-profiling` artifact. Implement the deterministic scorer (rapidfuzz + local embeddings via fastembed, + type/format signals) producing ranked candidates with confidence. *Deliverable: `POST /profile` returns a proposed mapping with confidences.*

**Day 3 — AI tie-breaker + mapping spec + versioning.** Wire Instructor + Groq (with fallback chain) for low-confidence columns only. Define the mapping-spec JSON, save as versioned rows, record provenance. Build the labeled-benchmark eval script (precision/recall/acc@k) + fuzzy baseline comparison. *Deliverable: versioned mapping spec + a metrics printout proving AI > baseline.*

**Day 4 — Deterministic transform + validation + exception queue.** Transform engine (catalogue functions applying the spec). Pandera schema + custom business rules. Quarantine table + structured error objects + suggested fixes. Idempotent upsert load (natural key + content hash). *Deliverable: end-to-end upload→load with valid rows in canonical tables and bad rows in quarantine. **This is the Minimum Demoable Product (MDP).***

**Day 5 — Frontend review UI.** Next.js + shadcn/ui + TanStack Table (v8): upload page, mapping-review grid (accept/override, confidence badges, samples), exception queue view with bulk-fix, and a metrics dashboard. (If behind: **Streamlit** version of the same three screens.) *Deliverable: clickable UI over the whole flow.*

**Day 6 — Idempotency, retries, schema-evolution demo, mock API.** Idempotency-Key handling; retry/backoff; the mock-CRM service + "connect API" path. Rehearse the **add-a-required-field** live change (migration + spec v+1 + re-run). Add Splink fuzzy dedupe. *Deliverable: the "pivot" demo works.*

**Day 7 — Packaging.** README + architecture diagram, "move to VPC" doc, seed a clean demo dataset, record the 3-5 min video, deploy to free tiers, final metrics run. *Deliverable: shipped, documented, deployed.*

**Cut-list if behind (in order):** drop Next.js → Streamlit; drop ARQ/Redis → Postgres job table; drop Splink → rapidfuzz dedupe; drop live mock-API → file upload only; drop deployment → local Docker + video. **Never cut:** the versioned mapping spec, the deterministic transform/validation, the exception queue, and the metrics/benchmark — those *are* the FDE signal.

---

### 7. LEARNING TRACK (defend every decision)

- **FastAPI & async** — official FastAPI docs (tutorial + "SQL Databases"); understand dependency injection, `async def` vs sync, and BackgroundTasks limits.
- **Pydantic v2** — official docs; know validators, `model_validate`, and why v2 core is Rust/fast. (Remember v2 changed error structure: use `error.errors()`.)
- **Pandera / validation** — Pandera docs; the "Great Expectations vs Pandera" trade-off article. Be able to say when you'd choose GX. Know `.validate()` returns the DataFrame (chain it).
- **Polars + DuckDB** — Polars user guide, DuckDB docs; understand lazy evaluation, Arrow zero-copy (`duckdb...pl()`), and why in-process beats Spark for GB-scale.
- **SQLModel + Alembic** — FastAPI's SQLModel tutorial; Alembic autogenerate docs. Understand migrations as "version control for your schema."
- **Embeddings & schema matching** — sentence-transformers docs; the "It's AI Match," Magneto, and LLMatch papers (embeddings + LLM for schema matching); MTEB leaderboard for model choice.
- **Structured LLM output** — Instructor docs; Ollama structured-outputs docs; an Outlines/constrained-decoding explainer. Learn *why* prompt-only JSON fails 5-20% and why structured modes guarantee schema, not content.
- **Idempotency & data modeling** — Stripe's idempotency-key article (pattern reference); Postgres `INSERT ... ON CONFLICT` docs.
- **Record linkage** — Splink docs + Robin Linacre's blog (Fellegi-Sunter, blocking).
- **System design** — read about medallion (raw/landing → curated) architecture and Temporal's durable-execution concepts so you can articulate the production version.

---

### 8. DEMO & PORTFOLIO PACKAGING

**README should contain:** one-paragraph problem statement (the FDE framing), architecture diagram, the "LLM proposes, deterministic engine executes" principle up top, quickstart (`docker compose up`), the canonical schema, the metrics with the fuzzy-vs-hybrid benchmark table, a GIF of the review UI, and a "Production considerations / move to VPC" section.

**Architecture diagram:** use Excalidraw or Mermaid; show the pipeline stages, the human-in-the-loop review, the versioned mapping spec store, and the exception queue as a first-class path. Keep it legible in 5 seconds.

**3-5 minute demo video:** (1) upload a deliberately messy file; (2) show the auto-profile + proposed mapping with confidence badges; (3) confirm one ambiguous mapping in the UI; (4) run it → show valid rows loaded + bad rows in the exception queue with explanations + a bulk-fix; (5) the money shot: **add a new required field live**, migrate, bump the mapping spec version, re-run without breaking prior data; (6) end on the metrics dashboard showing time-to-onboard and hybrid-vs-baseline mapping accuracy.

**Seed realistic messy data:** three exports — CRM (customers), billing (invoices), support (tickets) — with deliberate problems: inconsistent customer IDs (`C-1001` vs `1001`), mixed date formats, currency strings, missing required fields, duplicate/near-duplicate customers (typos, casing), orphan invoices (customer not in CRM), future invoice dates, negative amounts, extra/renamed columns, and encoding quirks. Ship the ground-truth labels so metrics are real.

**"How I'd move this to a VPC / private cloud" doc — cover:**
- **VPC design**: services in private subnets, DB with no public IP, access via bastion/VPN; API behind a load balancer in a public subnet; egress via NAT.
- **Secrets management**: no secrets in env files in prod — use a secrets manager (AWS Secrets Manager / Vault); rotate DB creds and LLM keys.
- **PII handling**: classify PII columns during profiling; field-level encryption or tokenization for sensitive fields; data-retention & deletion policy; minimize what leaves the boundary (this is why embeddings run *locally* and only column *names*/samples — not full datasets — ever touch an external LLM; in a real deployment you'd use a VPC-hosted/self-hosted model).
- **Encryption**: TLS in transit, encryption at rest (KMS-backed), encrypted backups.
- **Audit logs**: immutable log of every mapping decision, who confirmed it, every data access — you already have the review/version tables for this.
- **SOC 2-style considerations**: least-privilege IAM, change management via migrations + PRs, monitoring/alerting, separation of duties.
- **Tenant isolation**: `tenant_id` on every row + row-level security (Postgres RLS) or schema-per-tenant; per-tenant encryption keys for stronger isolation.

**Interview talking points & likely questions:**
- *"Why LLM proposes but doesn't execute?"* → reproducibility, testability, cost, auditability; models drift, deterministic engines don't.
- *"How do retries not create duplicates?"* → natural key + content hash + idempotent upsert + idempotency key.
- *"A customer adds a required field mid-deployment — what breaks?"* → nothing: versioned specs + versioned rules + additive migrations + idempotent re-runs. (Then show it.)
- *"How do you know the AI actually helps?"* → the labeled benchmark + fuzzy baseline ablation.
- *"What would you change for production scale?"* → Temporal for orchestration, a real object store for landing, streaming/Polars for large files, GX Data Docs for multi-team governance, self-hosted LLM in-VPC.
- *"How do you handle bad/ambiguous data without a human?"* → confidence thresholds, quarantine, suggested fixes, bulk-fix workflows.
- Expect **decomposition, learning, and under-defined-prompt** rounds (Palantir FDE loops are ~60-min CodePair coding + decomposition + learning + system design, with ~15-20 min of behavioral embedded in each) — this project gives you concrete war stories for each. Palantir also weights *mission-driven ownership* and comfort with ambiguity heavily, and AI-lab FDE roles (OpenAI Forward Deployed Engineer, Anthropic Applied AI Engineer) weight production LLM systems (RAG, evals, structured output) — all of which this project demonstrates.

---

### 9. FREE DEPLOYMENT / HOSTING (2026 reality)

- **Postgres — Neon (recommended) or Supabase.** Per Neon's official FAQ (updated May 2026), the Free plan is "$0/month and includes 100 projects, 10 branches per project, 100 CU-hours of compute per project per month, 0.5 GB of storage per project, and 5 GB of public network transfer per project"; compute scales to zero after ~5 min idle (cold start ~500ms-2s). Supabase free: ~500 MB DB, 1 GB file storage, 50k MAU, pauses after ~1 week inactivity, 2 active projects, and bundles auth/APIs — and it has a **Mumbai (India) region**, useful for you. **Pick Neon** for a pure DB; Supabase if you want bundled auth. Both are "dev/prototype" grade — fine for a portfolio demo.
- **Backend (FastAPI) + worker — Render free web service.** Render is the last major PaaS with a *permanent* free web tier (512 MB RAM, 0.1 CPU, 750 instance-hours/workspace/month). Per Render's official docs, it "spins down a Free web service that goes 15 minutes without receiving any inbound traffic," and it "spins back up whenever it next receives an HTTP request... This process takes about one minute." Render's *free* Postgres **expires 30 days after creation** — so use **Neon for the DB** and **Render only for compute**.
- **Frontend (Next.js) — Vercel Hobby** (free, 100 GB bandwidth/month, no card; non-commercial license). Ideal for Next.js; serverless functions have a 5-min timeout and there's no managed DB on free.
- **⚠️ What's NOT free anymore:** **Railway** removed its free tier (now ~$5/mo Hobby with $5 credit), and **Fly.io** removed free allowances for new accounts in Oct 2024 (pay-as-you-go, ~$2-5/mo minimum for a small always-on app; new signups get only a short trial). Don't rely on old tutorials claiming these are free.
- **Hugging Face Spaces (2026 nuance):** CPU Basic hardware is still free (**2 vCPU, 16 GB RAM, 50 GB non-persistent disk**, sleeps after 48h idle), BUT as of 2026 HF's docs state that **creating a Docker or Gradio compute Space requires a paid plan (PRO ~$9/mo)** — only **static** Spaces and up to **2 ZeroGPU Gradio Spaces** are free for personal accounts in good standing. So HF Spaces is **not** a good fit for your Dockerized FastAPI+Postgres stack on the free tier; use it only if you build a Streamlit/Gradio-on-ZeroGPU variant.
- **Recommended free deployment:** **Neon (DB) + Render free web service (FastAPI, + worker if used) + Vercel (Next.js frontend)**. Accept cold starts; in the demo, "warm up" the services first. Keep everything reproducible with Docker Compose for local + a one-command story.

#### The zero-budget AI stack (with fallback chain so the demo never breaks)
**Embeddings (the bulk of the matching) — 100% local & free:** `fastembed` (ONNX, default `BAAI/bge-small-en-v1.5`) or `sentence-transformers` `all-MiniLM-L6-v2` (384-dim, fast). No API, no rate limit, no cost — this is why the design scales to zero budget, and per the MTEB leaderboard these small models rank close to paid commercial embeddings.

**LLM (tie-breaker only) — free-tier chain, fail over on 429/error:**
1. **Groq (primary)** — free tier, no credit card. Per Groq's official docs as reported by Klymentiev (June 2026): "Free-tier limits: 30 requests per minute, 6,000 tokens per minute, and 14,400 requests per day per organization" — limits are org-level, so multiple keys don't multiply quota, and the 6,000 TPM is the real bottleneck. Fast, OpenAI-compatible, models like Llama 3.3 70B / GPT-OSS 120B. Best default for a demo.
2. **Google AI Studio / Gemini Flash (fallback 1)** — free tier, no card; Flash models ~10-15 RPM with daily caps. **Verify live:** on Dec 7, 2025 Google cut Gemini 2.5 Flash's free daily requests dramatically (reported ~250/day → ~20-50/day) and removed Gemini 2.5 Pro from the free tier entirely; the current free lineup is Flash-only (Gemini 3 Flash, 3.1 Flash-Lite, 2.x Flash). Limits are per-project, not per-key.
3. **Cerebras (fallback 2)** — free tier, no card. Per TokenMix (verified April 2026): "1,000,000 tokens per day free... The catch: 30 requests/minute and 60,000-100,000 tokens/minute rate limits, plus a temporary 8,192 token context cap." Extremely fast; models incl. Llama/Qwen/GPT-OSS.
4. **OpenRouter free models (fallback 3)** — one OpenAI-compatible key, ~20-29 `:free` models, but ~20 RPM / 50 req/day (rising to 1,000/day after a one-time $10 credit purchase) and the roster churns weekly — don't hardcode a model ID; check `openrouter.ai/models` filtered by "Free" before the demo.
5. **Local Ollama (offline fallback)** — Qwen 2.5/3 or Phi with native `format` structured output; guarantees the demo works with zero internet and zero cost. `ollama pull qwen2.5:7b`.

Wrap all of these behind one **Instructor** client (swap OpenAI-compatible base URLs) with a try-next-on-failure loop, and a **final deterministic fallback to embedding/fuzzy scores** if every LLM fails. Because you only call the LLM for the handful of ambiguous columns, free rate limits are never a problem. Verify each provider's live limits before your demo — free tiers change often (the `cheahjs/free-llm-api-resources` GitHub repo is a well-maintained 2026 community aggregator of free LLM tiers to check current numbers). Also note: free tiers usually **train on your inputs** (Google, Groq, Mistral's Experiment tier), so only send column names + a few sample values (never full sensitive datasets) — which doubles as a great PII talking point.

---

## Recommendations

**Immediate (before Day 1):** Sign up for Groq + Google AI Studio + Cerebras keys now (no cards). Install Ollama + pull `qwen2.5:7b` as the offline safety net. Create Neon + Render + Vercel accounts.

**Build order priority:** Get to the **Minimum Demoable Product (end-to-end upload→transform→validate→load+quarantine, Day 4)** before touching the fancy frontend. A working CLI/API pipeline with a great data model beats a pretty UI over nothing.

**Thresholds that change the plan:**
- If by **end of Day 4** the MDP isn't working → cut Next.js for Streamlit and cut ARQ/Redis for the Postgres job table immediately.
- If mapping **accuracy@1 < ~80%** on your benchmark → improve the alias dictionary and add value-based embeddings before adding more LLM calls (deterministic signals are cheaper and more reliable).
- If free LLM rate limits bite during the demo → flip the chain to **local Ollama** (it never rate-limits).

**To stand out to an FDE interviewer, prioritize (in order):** (1) the versioned mapping spec + live "add a required field" pivot; (2) the exception queue with suggested fixes + bulk-fix; (3) the quantified AI benchmark vs fuzzy baseline; (4) idempotency story; (5) the "move to VPC" doc. These map 1:1 to what Palantir/OpenAI/Anthropic FDE loops test: ambiguity, data quality, and adaptability.

---

## Caveats

- **Free tiers change constantly.** Every rate limit and hosting figure here reflects 2026 reporting and should be re-verified the week you build — Google cut Gemini free quotas ~50-92% in Dec 2025, Groq limits are org-level (multiple keys don't help), and OpenRouter's free roster rotates weekly. Treat the fallback chain as essential, not optional.
- **Some cited figures come from third-party trackers, not always primary docs.** Where a number matters for your demo (e.g., Groq TPM, Neon storage), confirm on the provider's own dashboard/pricing page.
- **`ydata-profiling` was renamed to `fg-data-profiling` (April 2026)**; the old package still installs but may stop receiving updates — pin your version.
- **TanStack Table v9 (Aug 2026) broke API compatibility** with most tutorials; pin v8 unless you deliberately adopt v9.
- **HF Spaces free-tier Docker restriction** is a 2026 change confirmed by HF docs + user reports; one tracker still cites a larger "100K credits" inference figure that conflicts with the better-sourced ~$0.10/month free inference credit — verify before relying on HF for hosting. (HF is not recommended for your Docker stack regardless.)
- **This is a portfolio/demo build, not production.** The security, tenancy, and scale items are documented as "what I'd do," which is appropriate for the interview — but be honest about that boundary; interviewers respect knowing the difference more than pretending the demo is production-hardened.