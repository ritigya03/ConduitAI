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

## Where things stand

- **Tests:** 87 passing, verified re-runnable across repeat runs
- **Endpoints:** `POST /upload`, `POST /profile`, `POST /mapping-spec`, `POST /mapping-spec/{id}/confirm`, `GET /health`
- **Secrets:** `GROQ_API_KEY` in `backend/.env` (gitignored)
- **Not done yet:** transform engine, validation, exception queue, load into canonical tables (Day 4), frontend (Day 5)

## Stack

FastAPI · SQLModel · Postgres 16 · Alembic · Polars · rapidfuzz · fastembed · Instructor · Groq / Ollama · Docker Compose
