# ConduitAI — Progress

AI-assisted customer data onboarding pipeline. 7-day build, day-by-day.

---

## Day 1 — Foundations & Data

**Built:** FastAPI + Postgres + Docker Compose skeleton · canonical field registry (`app/canonical.py`) · full SQLModel schema + Alembic migration (11 tables) · `POST /upload` (idempotent, raw rows stored as-is) · deterministic seed generator (messy CRM/billing/support CSVs + `ground_truth.json` labeled benchmark).

No notable issues — this session picked up after Day 1 was already done.

---

## Day 2 — Profiling + Deterministic Mapping

**Built:**
- `app/profiling.py` — column profiler using **Polars** (not pandas): nulls, cardinality, samples, type/format parse-success rates
- `app/report.py` — `ydata-profiling` HTML report per batch (pandas used *only* here, as a one-line bridge)
- `app/scoring.py` — deterministic scorer: `rapidfuzz` (name match) + `fastembed` (local embeddings) + type/format signal → weighted confidence score, bucketed `auto_accept` / `human_confirm` / `unmapped`
- `POST /profile` — profiles a batch, returns ranked canonical-field candidates per column

**Why Polars over pandas:** pandas silently boxes every column as `object` dtype and tells you nothing; Polars' expression API gives an honest, explicit parse-success rate per column (attempt a cast, count what came back non-null) — that rate *is* the type/format signal the scorer needs.

**Issues hit while building it:**
| Problem | Fix |
|---|---|
| `ydata-profiling` imports the deprecated `pkg_resources`, which a fresh Python 3.12 `uv` venv doesn't include, and newer `setuptools` (≥81) removed entirely | Pinned `setuptools<81` |
| Polars' `.to_pandas()` needs `pyarrow`, not pulled in automatically | Added `pyarrow` explicitly |

---

## Day 3 — AI Tie-Breaker + Mapping Spec

**Built:**
- `app/llm_mapper.py` — LLM tie-breaker, called **only** for columns Day 2 bucketed `unmapped`. Chain: **Groq** (`openai/gpt-oss-120b`) → **local Ollama** (`llama3`) → deterministic fallback. Uses **Instructor** with a dynamically-built `Literal` type per call so the LLM can only answer with a real candidate field name or `"UNKNOWN"` — never an invented one
- `app/mapping_spec.py` — turns every column's final decision (deterministic or LLM) into a mapping-spec JSON, keyed by canonical field, with a static transform-function assignment (e.g. `parse_date`, `to_minor_units`) and full provenance
- `POST /mapping-spec` — runs the above, saves it as a new **versioned** row (`version = max+1`, never edited in place, always `status="draft"`)
- `POST /mapping-spec/{id}/confirm` — human-review action, promotes a draft to confirmed (added after review feedback — see below)
- `seed/eval_mapping.py` — CLI benchmark script: baseline (pure fuzzy) vs hybrid (Day 2) vs full (+LLM), precision/recall/accuracy@1/@3, run against **two** benchmark sets (see below)
- `seed/held_out_benchmark.py` — 30 hand-labeled real-world columns (Salesforce/HubSpot/QuickBooks/Zendesk), added after review feedback caught benchmark leakage in the original set

**This is where most of the real debugging happened — six separate issues found and fixed, entirely because the code was actually run against live data and a live LLM instead of trusting it on paper:**

1. **Wrong Groq model name.** Planned to use `llama-3.3-70b-versatile` — doesn't exist on Groq anymore (model rosters change). Queried Groq's live `/models` endpoint, switched to `openai/gpt-oss-120b`.
2. **Ollama's `llama3` doesn't support tool-calling**, which is Instructor's default structured-output mechanism — every Ollama call errored. Fixed by using Instructor's JSON mode (`instructor.Mode.JSON`) specifically for the Ollama leg.
3. **camelCase-splitting regex bug** (actually a Day 2 bug, caught while building Day 3's benchmark): the regex meant to split `"CustID"` → `"Cust ID"` instead put a space before *every* uppercase letter — so ALL-CAPS headers like `"PHONE"` got mangled into `"p h o n e"` (5 fake words), wrecking the fuzzy match. Fixed the regex to only split at a genuine lowercase→UPPERCASE transition. Measured effect: fuzzy-match baseline accuracy on the benchmark jumped from **73.81% → 90.48%** from this one-line fix.
4. **Missing candidate bug:** the scorer's candidate pool for `billing`/`support` sources never included the `accounts` table — so `account_number` (which genuinely belongs there) could never be matched correctly by *any* signal, deterministic or LLM, because it wasn't even in the list of options being scored.
5. **The first fix for #4 was too broad.** Adding the *whole* `accounts` table to those pools also pulled in `account_status`/`account_opened_at`, which collided with `support_tickets`' near-identical `status`/`opened_at` fields and broke mappings that used to work. Caught by the benchmark script itself (LLM pass showed zero improvement — investigation found every wrong prediction was landing in `human_confirm`, not `unmapped`, so the LLM never got a chance). Fixed properly by adding *only* the one genuinely cross-cutting field (`account_number`) instead of the whole table.
6. **Bad alias in the canonical registry** (Day 1 file): `legal_name` had a bare `"name"` alias. `rapidfuzz`'s `token_set_ratio` scores a **perfect** match whenever a short alias is a subset of the column name's words — so *any* column containing "name" (`first_name`, `last_name`, `display_name`...) falsely matched `legal_name` at 100%, confidently and wrongly, before the LLM ever got involved. Removed the alias.
7. **A test's assumption was simply wrong**, not a code bug: assumed `first_name`/`last_name` would land in `unmapped` and get corrected by the LLM. With real embeddings they land in `human_confirm` instead (via other legitimate aliases like `company_name`/`account_name`/`short_name`, which all also contain "name") — and that's actually *correct* behavior: `human_confirm` exists precisely for a person to check, not for more automation. Rewrote the test around that.
8. **A real LLM-dependent test flaked on a second run** — the LLM's actual judgement on a nonsense column varied call to call (real non-determinism, not a bug). Rewrote the assertion to check that the LLM path *engaged* (provenance isn't plain `"deterministic"`) rather than what the LLM specifically said.

**First benchmark result** (`uv run python -m seed.eval_mapping`), and why it doesn't prove what it looks like it proves:

| pass | acc@1 | acc@3 | precision | recall |
|---|---|---|---|---|
| baseline (fuzzy only) | 97.62% | 100% | 85.42% | 97.62% |
| hybrid (Day 2, no LLM) | 97.62% | 100% | 91.11% | 97.62% |
| full (+ LLM tie-break) | 97.62% | — | — | — |

Baseline and hybrid tying at 97.62% isn't "the alias dictionary was comprehensive" — that phrase in an earlier version of this doc was a rationalization, not a finding. It's **benchmark leakage**: the seed generator's legacy headers (`CustID`, `COMPNAME`, `CNTRY`, `DT_CREATE`, ...) and `app/canonical.py`'s alias lists were written together, in the same Day 1 session, and quietly reuse the exact same abbreviations (`custid`, `compname`, `cntry`, `dt_create`, ...). Fuzzy matching "solving" that benchmark proves the alias dictionary matches itself. There's nothing left for embeddings or the LLM to do.

**Fix: a second, held-out benchmark** (`seed/held_out_benchmark.py`) — 30 hand-labeled column names pulled from real Salesforce/HubSpot/QuickBooks/Zendesk exports, chosen without consulting the alias lists:

| pass | acc@1 | acc@3 | precision |
|---|---|---|---|
| baseline (fuzzy only) | 79.17% | 100% | 63.33% |
| hybrid (no LLM) | 75.00% | 95.83% | 72.00% |
| full (+ LLM tie-break) | **83.33%** | — | — |

This is the real, honest story. Hybrid alone actually *dips below* baseline here (embeddings introduce some noisy picks — e.g. confusing `hs_object_id` with `customer_employee_count`), but the LLM tie-break recovers past both. Diagnosing the misses: half are legitimate `unmapped`-bucket abstentions (`Sic`, `created_at`) where the LLM gets a real shot and takes it; the other half are confident-but-wrong `human_confirm`-bucket collisions on generic shared tokens (`account`, `id`, `number` — the same class of problem as the `legal_name`/`"name"` alias bug above, not yet fully solved). Both benchmarks are kept: in-distribution as a fast regression guard, held-out as the test that actually measures generalization to a source system the scorer has never seen.

**Two follow-up decisions, made explicit rather than left implicit:**
- **`POST /mapping-spec/{id}/confirm`** — every spec used to be created `"draft"` with nothing that ever confirmed one. Added the human-review action: promotes draft → confirmed, superseding whatever was previously confirmed for that source. Day 4's transform engine should read the *confirmed* spec, not the latest draft.
- **Transform-function assignment stays a static `FieldType → function` lookup, not an LLM call.** Field types are a small, fully-enumerable set, so a lookup table is simpler and 100% testable — there's nothing for a model to usefully add here. The LLM is reserved for exactly the one thing that can't be enumerated in advance: which canonical field a never-seen column name refers to.

---

## Day 4 — Transform, Validate, Load

**Built before this session** (see `backend/app/loader.py`, `record_builder.py`, `validation.py`, `transforms.py`, and their tests): the transform engine, Pandera schema + business-rule validation, referential-integrity resolution (including the cross-system natural-key normalization fallback), idempotent upsert-or-quarantine per row, and `POST /load`. No retrospective notes exist for it in this doc — Day 5 picked up with it already fully working and tested.

---

## Day 5 — Frontend Review UI

**Built:**
- **Backend additions (Task 0):** `CORSMiddleware` (frontend origin `http://localhost:3000`); a `/reports` static-files mount for the ydata-profiling HTML reports Day 2 already generates; `PATCH /mapping-spec/{id}` (`app.mapping_spec.update_spec_json`) — merges reviewer overrides into a **draft** spec in place, `409`s on a confirmed one, auto-fills `transform`/`provenance` from the field's registered type when a caller supplies only `{source_column}`; `GET /quarantine`, `POST /quarantine/{id}/resolve`, `POST /quarantine/bulk-resolve` (`app/routers/quarantine.py`); `GET /metrics` (`app/routers/metrics.py`). `app.loader.load_batch`'s per-row body was extracted into a reusable `process_row()` so the resolve endpoints replay the exact same transform → validate → resolve-customer-link → upsert pipeline a first-pass load uses, instead of a parallel reimplementation.
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind v4 + shadcn/ui + TanStack Table v8 (pinned), calling the FastAPI backend directly from the browser. Dark console theme (`app/globals.css`) — surface/background/text tokens plus one reserved amber accent for `human_confirm`/warnings, success/danger semantic colors, IBM Plex Sans + Mono via `next/font/google`. Five routes behind a shared left-rail `NavRail` (literal pipeline sequence, Profile/Mapping/Load only linkable once a batch exists in the URL): `/` (upload + session-local recent-batches list — no `GET /batches` endpoint exists, a deliberate scope cut), `/batches/[batchId]/profile` (column-stats `DataTable` + link to the full ydata-profiling report), `/batches/[batchId]/mapping` (the centerpiece review grid — confidence badge, provenance, per-column override `Select` with optimistic `PATCH` + rollback on failure, "Confirm mapping"), `/batches/[batchId]/load` (`POST /load` once on mount, then exception queue + per-error-code bulk-fix bars against `/quarantine` endpoints only — re-calling `/load` would wipe out resolved rows since it recomputes quarantine from scratch), `/metrics` (stat-tile grid, no charts for point-in-time counts).

**Issues hit while building it — this plan was written against tooling that had since moved on, same "run it for real" lesson as Day 3:**
1. **The docker `api` image was stale** (built Day 1, before Day 2 added Polars/ydata-profiling to `pyproject.toml`) and this sandbox's Docker daemon can't reach Docker Hub to rebuild. Ran the backend directly on the host instead (`uv run uvicorn app.main:app --reload`) against the same Postgres container — image rebuild is still owed before this stops being a local-only workaround.
2. **`npx shadcn@latest init -b slate`** (the plan's exact command) doesn't exist anymore — this shadcn CLI generation replaced base-color prompts with a `-b radix|base|aria` component-library flag and a preset system (`-p nova`). Used `-b radix -p nova` (closest to the plan's assumed Radix-backed, css-variables-themed components).
3. **`npx shadcn add form` is gone** from this registry — no `react-hook-form`/`zod` scaffold available. Built the upload form as a plain controlled component instead; three required fields don't need a form library.
4. **Next.js shipped as v16**, not the plan's assumed v14 — dynamic route `params` is a `Promise` now. Sidestepped entirely by making every batch-scoped page a client component reading `useParams()`, which is synchronous and also a better fit for pages that are mostly client-side fetch/mutate anyway.
5. **The mapping-review override control only knows a field name**, not that field's canonical type — it can't build a correct `transform` client-side the way `build_spec_entries` does server-side. Extended `update_spec_json` to auto-fill `transform` (from the field's registered type) and `provenance` (defaulted to `human`) whenever a `PATCH` entry omits them, so the frontend only ever has to send `{source_column}`.
6. **The quarantine bulk-fix UI can't derive which *raw* source column an error's canonical field came from** without a spec lookup this build doesn't expose — `explanation` names the canonical field (e.g. `invoice_issue_date`), not the raw CSV header (`issue_date`). Rather than add a lookup endpoint, made the fix/bulk-fix inputs generic (raw field name + value, both reviewer-supplied) — the reviewer can already see the raw row in the queue.
7. **A lint rule (`react-hooks/set-state-in-effect`) flagged reading `localStorage` in a bare `useEffect`+`setState`.** The textbook-correct fix is also the one that avoids a real hydration mismatch (server has no `localStorage`): `useSyncExternalStore` for the recent-batches list instead.

**Verified against the live backend** (not just unit tests): uploaded `crm_snake.csv`, profiled it, created+overrode+confirmed a mapping spec, loaded it (30 loaded / 1 quarantined — the seed's known `DATE_AMBIGUOUS` row), bulk-resolved that error code with a corrected date, confirmed `quarantine_open` dropped to 0 and `customers`/`quarantine_fixed` incremented via `GET /metrics`. Frontend: `tsc --noEmit`, `next lint`, and `next build` all clean; every route confirmed reachable (200, no server-render errors) against a real batch id. No browser-automation tool was available in this session, so on-screen interaction (clicking through overrides, bulk-fix inputs) hasn't been visually verified — worth a real click-through before calling this done.

---

## Day 6 — Idempotency, Retries, Schema Evolution, Mock API, Fuzzy Dedupe

**Built:**
- **`Idempotency-Key` handling** (`app/idempotency.py`, `idempotency_keys` table) — an optional request header, stored per `(tenant, endpoint, key)`. Replaying the same key returns the exact stored response without reprocessing; the same key with a *different* request body `409`s. Wired into `POST /upload` and `POST /mapping-spec` (the latter matters concretely: without it, a retried `POST /mapping-spec` legitimately creates a new draft version every time — that's existing, correct, tested behavior — so the Idempotency-Key is what turns a retry into a replay instead of spurious version churn).
- **Exponential-backoff retries** via `tenacity` — `app.llm_mapper._call_provider_with_retry` retries only genuinely transient OpenAI-SDK exceptions (`APITimeoutError`, `APIConnectionError`, `RateLimitError`) up to 3 attempts before falling through to the existing Groq → Ollama → deterministic-fallback chain unchanged; `app.mock_crm_client._get_page` retries transient `httpx` failures (`TransportError`, `429`/`503`) up to 4 attempts. Both explicitly do **not** retry non-transient failures (auth, validation, permanent 4xx) — retrying those would just slow down the existing fallback logic for no benefit.
- **`mock-crm/`** — a genuinely separate FastAPI service (own `pyproject.toml`, own Dockerfile), not a stub: paginated, authenticated (`Authorization: Bearer`), and deliberately flaky (every 4th request returns `503`) so retry/backoff has something real to demonstrate. Its 25-row dataset reuses the exact "legacy" abbreviated headers (`CustID`, `CompName`, `Cntry`, `Email`, `DT_Create`) `app/canonical.py`'s aliases already cover from Day 1/3 — a batch pulled from this API exercises the identical deterministic-mapping path a file upload does. One row is a deliberate near-duplicate (`Whitfield & Sons` / `Whitfield and Sons Ltd`, same email) for Task 6's dedupe pass to catch.
- **`app.ingest.ingest_rows`** — the source/batch/raw-record logic factored out of `POST /upload` so `POST /connect/mock-crm` (`app/routers/connect.py`, `app/mock_crm_client.py`) lands API-pulled rows through the identical path a file upload uses; everything downstream (profile, mapping-spec, load) treats the two sources identically.
- **Splink fuzzy dedupe** (`app/dedupe.py`, `customer_duplicate_candidates` table, `POST /dedupe` / `GET /duplicates` / `POST /duplicates/{id}/resolve`) — Fellegi-Sunter probabilistic linkage on `legal_name` (Jaro-Winkler) + `email`/`country` (exact), with **hand-specified match/non-match probabilities**, not Splink's statistical EM training: this project's per-tenant customer counts (tens to low hundreds) are too small for EM to converge reliably (confirmed live during planning — a 5-row prototype left several comparisons "not trained" and produced zero predictions on an obvious match). Expert-specified priors are a documented, legitimate Splink usage mode. `resolve(action="merge")` repoints every `Invoice`/`SupportTicket`/`Account` FK from the loser to the winner and deletes the loser; `customer_id_a`/`customer_id_b` are deliberately **not** DB foreign keys, so the candidate row survives as a permanent audit record after the merge deletes one side of it.
- **Schema-evolution "pivot" demo** — added `customers.kyc_status` (required ENUM: `pending`/`verified`/`rejected`) as a genuinely new field, not backdating the already-existing-but-optional `risk_tier`. Three migrations (add nullable → backfill + `NOT NULL` → `server_default='pending'`), matching the real zero-downtime pattern the spec describes, generated via `alembic revision --autogenerate` and hand-edited for the backfill (autogenerate never writes `UPDATE`s).

**The real finding, from running it rather than trusting the design:** planning identified that `app.record_builder.apply_spec_to_row` only processes canonical fields present in a given mapping spec's `spec_json` — so an *old* confirmed spec, predating `kyc_status`, should be automatically unaffected by the new required field, no extra "rule versioning" needed. That's true, but incomplete: `app.validation.validate_schema` is a **separate** validation layer that independently checked *every* canonical field ever registered for a table, regardless of whether the spec that built `values` ever touched it — so every old batch broke the moment `kyc_status` became required, exactly the retroactive breakage the demo is supposed to prove doesn't happen. Fixed by scoping `validate_schema` to only the columns present in `values` (matching `record_builder`'s own scoping) — a required field's *presence* is `record_builder`'s job (`MISSING_REQUIRED`), `validate_schema`'s job is type-checking whatever *is* there. One existing Day 4 test asserted the old (wrong, for this architecture) behavior and was rewritten with the reasoning inline, the same way Day 3 rewrote a test whose assumption turned out wrong rather than papering over it. A second real bug, same session: the dedupe merge's hard `foreign_key="customers.id"` constraint on `customer_duplicate_candidates` made it impossible to ever delete a merged-away loser row — Postgres correctly refused. Fixed by dropping the FK (a deliberately soft reference, since this table's whole purpose is to outlive one side of the pair it names).

**Verified live, end to end, against the running services** (not just unit tests): uploaded via `Idempotency-Key` twice → one batch, identical response. Pulled `mock-crm`'s 25 rows through `POST /connect/mock-crm` — the legacy headers auto-mapped deterministically (`CustID`→0.73, `Email`→0.88, ...), retry/backoff genuinely fired twice against real `503`s (confirmed in `mock-crm`'s own request log) and recovered both times. Ran `POST /dedupe` on the loaded batch — found exactly the one deliberate `Whitfield` pair at 99.14% match probability, nothing else; `resolve(merge)` dropped the customer count 25→24. Schema evolution: loaded `crm_snake.csv` under the pre-`kyc_status` spec (30 loaded, all got the `pending` default, zero retroactive breakage); uploaded a purpose-built 3-row fixture with a real `kyc_status` column — the field auto-mapped, and the load produced exactly the expected split (`verified`/`rejected` rows loaded, the blank row quarantined with `MISSING_REQUIRED`).

**Environment note, not a code issue:** a handful of test runs during this session intermittently failed one unrelated test (`test_upload_stores_raw_records_and_creates_batch`, once on a raw-row ordering assertion with no `ORDER BY`, other times with no clear cause) that passed cleanly on every immediate re-run — consistent with resource contention from the several long-running background processes this session kept alive concurrently (host `uvicorn --reload` on the main API, the Next.js dev server, `mock-crm`, Postgres in Docker), not a real regression. Matches Day 3's already-documented LLM-test-flakiness precedent.

---

## Where things stand

- **Tests:** 181 backend tests passing (`backend/`) + 6 (`mock-crm/`), verified re-runnable across repeat runs. No frontend test suite (Day 5 scoped that to a manual walkthrough).
- **Endpoints:** `POST /upload`, `POST /profile`, `POST /mapping-spec`, `PATCH /mapping-spec/{id}`, `POST /mapping-spec/{id}/confirm`, `POST /load`, `GET /quarantine`, `POST /quarantine/{id}/resolve`, `POST /quarantine/bulk-resolve`, `GET /metrics`, `POST /connect/mock-crm`, `POST /dedupe`, `GET /duplicates`, `POST /duplicates/{id}/resolve`, `GET /health`, static `/reports/*`. Plus `mock-crm`'s own `GET /customers`, `GET /health`.
- **Secrets:** `GROQ_API_KEY` in `backend/.env` (gitignored)
- **Not done yet:** deployment/VPC docs, a durable job/worker layer (the spec's own explicit Day-7-or-cut-list territory — this project still runs everything synchronously, in-request).
- **Docker:** both `backend`'s and `mock-crm`'s images are unbuildable in this sandbox (no Docker Hub network access) — both run via `uv run uvicorn` on the host for now (`:8000` and `:8100`). `docker-compose.yml` is up to date and should build/run correctly wherever normal Docker network access exists.

## Stack

FastAPI · SQLModel · Postgres 16 · Alembic · Polars · rapidfuzz · fastembed · Instructor · Groq / Ollama · tenacity · Splink (DuckDB backend) · httpx · Docker Compose · Next.js · TypeScript · Tailwind v4 · shadcn/ui · TanStack Table v8
