# ConduitAI

AI-assisted customer data onboarding pipeline. Upload a messy CSV export from any CRM/billing/support system → it gets profiled, its columns get mapped to a canonical schema (deterministic scoring first, LLM only for the ambiguous leftovers), and the mapping is saved as a versioned, human-confirmable spec.

Day-by-day build log: [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Pipeline

```
POST /upload          → raw rows stored as-is, idempotent on file hash
POST /profile          → per-column stats (Polars) + deterministic scorer
                          (rapidfuzz + fastembed + type/format signal)
POST /mapping-spec      → LLM tie-break (Groq → Ollama → deterministic
                          fallback) for whatever the scorer couldn't
                          confidently place, saved as a new spec version
POST /mapping-spec/{id}/confirm → human-review action; promotes a draft
                          spec to the one Day 4's transform engine reads
```

## Engineering notes worth knowing about

**A one-line regex fix moved fuzzy-match accuracy from 73.81% → 90.48%.** The camelCase-splitter meant to turn `CustID` into `Cust ID` was instead putting a space before *every* uppercase letter, so an ALL-CAPS legacy header like `PHONE` was silently getting mangled into `p h o n e` (five fake one-letter words) before it ever reached the matcher. Caught by writing the Day 3 benchmark and asking why a supposedly-easy column was scoring badly.

**A "correct" fix that was actually too broad, caught by the benchmark it was feeding.** The scorer's candidate pool for `billing`/`support` sources was missing the `accounts` table entirely, so `account_number` could never be matched. The first fix — add the whole `accounts` table to those pools — technically worked, but also pulled in `account_status`/`account_opened_at`, which collide with `support_tickets`' near-identical `status`/`opened_at` fields and broke mappings that used to be correct. The benchmark script's own accuracy numbers flagged the regression before it shipped; the real fix added only the one field that was actually missing (`account_number`), not the whole table.

**The first benchmark was leaking, and the fix is a second, held-out one.** The seed generator's legacy column names (`CustID`, `COMPNAME`, `CNTRY`, ...) and the canonical registry's alias lists were written by the same hand, at the same time — the aliases quietly include the exact same abbreviations. 97.62% accuracy on that benchmark proves the alias dictionary matches itself, not that the AI layer earns its cost. Added `seed/held_out_benchmark.py`: 30 hand-labeled column names pulled from real Salesforce/HubSpot/QuickBooks/Zendesk exports, never consulted while writing the aliases. There, the deterministic layer alone actually *underperforms* pure fuzzy matching (75.00% vs 79.17%) — and the LLM tie-break recovers past both, to 83.33%. That's the real delta: fuzzy is enough in-distribution; the hybrid+LLM layer is what survives a source system it's never seen before. Both benchmarks are kept — in-distribution as a fast regression guard, held-out as the honest generalization test. Run both: `uv run python -m seed.eval_mapping`.

**Why the LLM never proposes transform functions.** Each canonical field's transform (`parse_date`, `to_minor_units`, `trim`, ...) is assigned deterministically from its declared type, not by the LLM. Field types are a small, fixed, fully-enumerable set — a lookup table is simpler, free, and 100% testable, so there was nothing for an LLM call to usefully add. It's the same reasoning as everywhere else in the AI layer: reach for the model only where the space of answers can't be enumerated in advance.

**Every mapping spec is `"draft"` until a human confirms it.** `POST /mapping-spec` never marks anything `"confirmed"` on its own — that would let an unreviewed AI-generated spec silently become load-bearing. `POST /mapping-spec/{id}/confirm` is the explicit human-in-the-loop action (what Day 5's review UI will call), and it supersedes whatever was previously confirmed for that source, so at most one confirmed version is ever active.

## Stack

FastAPI · SQLModel · Postgres 16 · Alembic · Polars · rapidfuzz · fastembed · Instructor · Groq / Ollama · Docker Compose
