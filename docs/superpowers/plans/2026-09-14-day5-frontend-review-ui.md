# Day 5: Frontend Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Scope note (why this plan reads differently from Days 1-4's):** those plans inlined complete, copy-pasteable code for every file because the backend's correctness hinges on exact algorithmic details (regex behavior, upsert semantics, transform edge cases) that are cheap to get wrong silently. A Next.js/shadcn UI's correctness hinges instead on matching the API contracts exactly and following shadcn/TanStack's own conventions — this plan specifies those contracts and component responsibilities precisely, but leaves exact JSX to be written against shadcn's generated component code during execution (via `npx shadcn add`), the way a human frontend engineer would. Task 1's steps are fully inlined (scaffolding has no ambiguity); Tasks 2-6 specify **exact API request/response shapes, component props, and acceptance criteria** rather than full JSX.

**Goal:** A clickable Next.js UI over the whole pipeline: upload a file, review and override the AI-proposed mapping, confirm it, load the batch, triage quarantined rows (including bulk-fix), and see pipeline metrics.

**Architecture:** Next.js 14 App Router + TypeScript + Tailwind + shadcn/ui + TanStack Table v8, calling the existing FastAPI backend directly from the browser (no separate BFF layer — CORS is enabled on the backend for this). Three small, focused backend additions are needed first (Task 0) because the review/override/bulk-fix/dashboard deliverables need endpoints that don't exist yet — everything built in Days 1-4 is POST-only, compute-and-return, with no way to revisit or edit a batch's state afterward.

**Tech Stack:** Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui (copies source in, no runtime dependency), **TanStack Table v8 — pinned, not v9** (the spec's own warning: v9 shipped breaking API renames in August 2026 that most tutorials/docs haven't caught up to). Backend additions use the existing FastAPI/SQLModel stack.

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` — §1 frontend recommendation (Next.js + shadcn/ui + TanStack Table, Streamlit as explicit fallback if behind), §4 exception queue design (bulk-fix by error code), §5 metrics. Builds on all of Days 1-4's endpoints (`POST /upload`, `POST /profile`, `POST /mapping-spec`, `POST /mapping-spec/{id}/confirm`, `POST /load`) and models (`MappingSpec`, `Quarantine`, `Customer`/`Invoice`/`SupportTicket`/`Account`).

**Design direction (frontend-design skill, approved by user):** Dark console aesthetic for a data-ops review tool — base `#12161C`, surface `#1B212A`, text `#E7EBF0`/`#8A94A3`, one purposeful accent `#E0A72E` (amber, "needs your attention" — human_confirm bucket, warnings) never used decoratively elsewhere. Semantic colors: success `#3FB27F` (auto_accept/loaded), danger `#E5484D` (error/quarantine). Typography: IBM Plex Sans (UI) + IBM Plex Mono (data — natural keys, error codes, confidence numbers, anywhere alignment matters), both via Google Fonts. Layout: left rail is a literal pipeline-stage sequence (Upload → Profile → Mapping Review → Load & Quarantine → Metrics) — functionally justified since the workflow genuinely is sequential, not decorative step-numbering. No ALL-CAPS labels, no em-dash-joined labels, no `→` on buttons, no decorative gradients.

## Global Constraints

- **TanStack Table v8, explicitly pinned in `package.json`** (`@tanstack/react-table@^8`) — do not let a bare install pull v9.
- **CORS**: the backend needs `CORSMiddleware` added (Task 0) before the frontend can call it from a browser origin — without this every fetch fails silently with an opaque network error, not a helpful one.
- **No new database migration** — Task 0's additions read/write existing tables (`mapping_specs`, `quarantine`) through existing columns; `MappingSpec.spec_json` already holds everything an override needs to change.
- **Every mutating frontend action calls a real backend endpoint** — no client-only state that pretends to be saved. A confirmed mapping, an overridden field, a resubmitted quarantine row: all of it round-trips through the API, matching this project's standing "deterministic engine executes, nothing silent" principle.
- **`tenant_id` is a fixed demo value in this UI** (`settings.default_tenant_id`, already `"demo-tenant"` in `app/config.py`) — Day 5 has no auth/multi-tenant switcher; every request just uses it.
- Frontend env var `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) — never hardcode the backend URL in a component.

---

## Task 0: Backend additions — override, quarantine list/bulk-fix, metrics

**Files:**
- Modify: `backend/app/main.py` (add `CORSMiddleware`)
- Modify: `backend/app/mapping_spec.py`, `backend/app/routers/mapping_spec.py` (add spec override)
- Create: `backend/app/routers/quarantine.py`
- Create: `backend/app/routers/metrics.py`
- Tests: `backend/tests/test_mapping_spec_override.py`, `backend/tests/test_quarantine_endpoint.py`, `backend/tests/test_metrics_endpoint.py`

**New interfaces:**

1. **`PATCH /mapping-spec/{id}`** (form: `tenant_id`, `spec_json` as a JSON string) — merges the given entries into a **draft** spec's `spec_json` (same shape Day 4's test `_apply_human_corrections` used directly against the DB — this is that same capability, exposed as a real endpoint instead of a test-only DB poke). `409` if the spec isn't `draft` (an already-confirmed spec is immutable — a reviewer wanting to change a confirmed mapping creates a new version via `POST /mapping-spec` instead). `404` if not found. Returns the updated `{mapping_spec_id, version, status, spec_json}`, same shape as `POST /mapping-spec`.

2. **`GET /quarantine?tenant_id=...&batch_id=...&status=open`** — lists `Quarantine` rows (`batch_id` optional; `status` optional, defaults to `open`). Returns `{"items": [{"id", "batch_id", "raw_record_json", "error_codes", "severity", "explanation", "suggested_fix", "status"}]}`.

3. **`POST /quarantine/{id}/resolve`** (form: `tenant_id`, `action`: `"fixed" | "ignored"`, optional `corrected_values` as a JSON string of `{column_name: new_value}` overrides applied to `raw_record_json` before it's treated as fixed) — sets `status` accordingly. `"fixed"` with `corrected_values` re-runs `app.loader`'s per-row pipeline (reuse `apply_spec_to_row` + validation + upsert, the same building blocks `app.loader.load_batch` uses per row) against the corrected raw values; on success the row loads and `status` becomes `"resubmitted"`, on continued failure it stays `"open"` with the new errors. Returns `{"status": str, "loaded": bool}`.

4. **`POST /quarantine/bulk-resolve`** (form: `tenant_id`, `error_code`: str, `action`: `"fixed" | "ignored"`, optional `corrected_values`) — applies the same resolution to every `open` quarantine row for that tenant whose `error_codes` contains `error_code` (the spec's own example: "apply DMY date format to all 214 DATE_AMBIGUOUS rows"). Returns `{"processed": int, "loaded": int, "still_open": int}`.

5. **`GET /metrics?tenant_id=...`** — aggregate counts for the dashboard: `{"batches": int, "customers": int, "invoices": int, "support_tickets": int, "accounts": int, "quarantine_open": int, "quarantine_fixed": int, "mapping_specs_confirmed": int}` — plain `COUNT(*)` queries scoped to `tenant_id`, no new tables.

- [ ] **Step 1: Add CORS middleware**

```python
# backend/app/main.py — add near the top, after `app = FastAPI(...)`
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 2: Write failing tests for `PATCH /mapping-spec/{id}`, `GET /quarantine`, `POST /quarantine/{id}/resolve`, `POST /quarantine/bulk-resolve`, `GET /metrics`** covering: happy path for each, the `409` on patching a confirmed spec, `404`s for unknown ids, and — for bulk-resolve — that it only touches rows matching the given `error_code` and leaves others untouched. Use the `unique_tenant_id` fixture and the existing upload→mapping-spec→confirm→load helpers from `tests/test_mdp_end_to_end.py` as a starting point for building quarantined rows to resolve.

- [ ] **Step 3: Implement each endpoint**, reusing existing building blocks: `app.mapping_spec.build_spec_entries`/`save_mapping_spec` patterns for the override (add an `update_spec_json(session, spec_id, tenant_id, overrides) -> MappingSpec` function to `app/mapping_spec.py` that merges into an existing **draft** row rather than creating a new version), and `app.record_builder.apply_spec_to_row` + `app.validation.*` + the loader's per-record upsert helpers for resolve/bulk-resolve (factor the "transform one row, validate, upsert-or-report-errors" logic `load_batch` already has into a small reusable `app.loader.process_row(session, tenant_id, spec, raw_json) -> RowResult` if it isn't naturally reusable as-is — check before duplicating).

- [ ] **Step 4: Run the new tests, then the full suite twice, then commit.**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
git add backend/app/main.py backend/app/mapping_spec.py backend/app/routers/mapping_spec.py \
        backend/app/routers/quarantine.py backend/app/routers/metrics.py backend/app/loader.py \
        backend/tests/test_mapping_spec_override.py backend/tests/test_quarantine_endpoint.py \
        backend/tests/test_metrics_endpoint.py
git commit -m "feat: add mapping-spec override, quarantine list/resolve/bulk-resolve, metrics endpoints"
```

---

## Task 1: Next.js scaffold

**Files:**
- Create: `frontend/` (entire Next.js project)

- [ ] **Step 1: Scaffold**

```bash
cd /Users/ritigya/Desktop/Projects/ConduitAI/ConduitAI
npx create-next-app@latest frontend --typescript --tailwind --app --no-src-dir --import-alias "@/*"
cd frontend
npx shadcn@latest init
```

When `shadcn init` prompts for a base color, choose **slate** (closest starting point to this plan's console palette — Task 2 overrides the exact tokens).

- [ ] **Step 2: Install TanStack Table v8, pinned**

```bash
npm install @tanstack/react-table@^8
```

Open `package.json` and confirm the installed line reads `"@tanstack/react-table": "^8.x.x"` — if it resolved to a `9.x`, uninstall and pin explicitly: `npm install @tanstack/react-table@8`.

- [ ] **Step 3: Add IBM Plex fonts via `next/font/google`** in `app/layout.tsx`:

```tsx
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";

const plexSans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });
const plexMono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });
```

Apply both font variables' classes to the root `<html>`/`<body>` element.

- [ ] **Step 4: Set the design tokens in `app/globals.css`** — replace shadcn's default CSS variables under `:root`/`.dark` with this plan's palette (`--background: #12161C`, `--foreground: #E7EBF0`, `--card` (surface): `#1B212A`, `--muted-foreground: #8A94A3`, a custom `--accent-amber: #E0A72E`, `--success: #3FB27F`, `--danger: #E5484D`), converted to the HSL triples shadcn's CSS variables expect. Force dark mode as the only mode for this build (`<html className="dark">`) — this is an ops console, not a page that needs to match OS light/dark preference.

- [ ] **Step 5: Write the typed API client** — `frontend/lib/api.ts`:

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TENANT_ID = "demo-tenant"; // matches backend/app/config.py's default_tenant_id

async function apiForm<T>(path: string, fields: Record<string, string | File>): Promise<T> {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`${path} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

// One typed function per endpoint (upload, profile, createMappingSpec,
// patchMappingSpec, confirmMappingSpec, loadBatch, listQuarantine,
// resolveQuarantine, bulkResolveQuarantine, getMetrics), each returning
// the exact response shape documented in this plan and in Days 1-4's
// plans. TENANT_ID is threaded into every call automatically so no
// component ever passes it explicitly.
```

Define the TypeScript types for every request/response shape (`UploadResponse`, `ProfileResponse`, `ColumnMapping`, `MappingSpecResponse`, `QuarantineItem`, `MetricsResponse`, ...) in `frontend/lib/types.ts`, matching the backend's Pydantic models field-for-field — these are the contract this whole plan hinges on, so get them from the actual backend source (`app/loader.py`'s `LoadSummary`, `app/scoring.py`'s `ColumnMapping`/`MappingCandidate`, etc.), not from memory of what they "should" be.

- [ ] **Step 6: Verify the scaffold runs**

```bash
npm run dev
```

Expected: default Next.js page loads at `http://localhost:3000` with the dark console background and IBM Plex fonts visibly applied (check devtools' computed font-family).

- [ ] **Step 7: Commit**

```bash
cd /Users/ritigya/Desktop/Projects/ConduitAI/ConduitAI
git add frontend
git commit -m "feat: scaffold Next.js frontend with design tokens and API client"
```

---

## Task 2: Upload page

**Route:** `/` (or `/upload`, redirect `/` to it).

**Behavior:** A form (tenant is fixed/hidden — see Global Constraints) with: file picker, source name text input, source kind select (`crm`/`billing`/`support`). On submit, calls `POST /upload`, shows the resulting `{batch_id, row_count, idempotent}`, and a "Profile this batch" button that navigates to `/batches/[batchId]/profile`. Below the form, list recently-uploaded batches for this tenant (needs a small addition: either reuse `GET /metrics` for nothing here, or — simpler — keep an in-memory/localStorage list of batch IDs the user has created this session, since there's no `GET /batches` endpoint and adding one is out of this plan's scope; note this explicitly as a deliberate scope cut, not an oversight).

**Components:** shadcn `Form`, `Input`, `Select`, `Button`, `Card`. Use shadcn's `react-hook-form` + `zod` pattern (what `shadcn add form` scaffolds) for validation — file required, source name non-empty.

**Acceptance:** uploading `backend/tests/fixtures/crm_tiny.csv` (or any seed CSV) with `source_kind=crm` succeeds and navigates to the profile page for the new batch.

- [ ] Scaffold via `npx shadcn@latest add form input select button card`, build the page, verify the acceptance criterion manually against the running backend, commit.

---

## Task 3: Profile view

**Route:** `/batches/[batchId]/profile`

**Behavior:** On load, calls `POST /profile` with the batch's `tenant_id`/`batch_id` (this endpoint is idempotent/safe to call on page load — it recomputes and returns, per Day 2's design). Renders a table: one row per source column, showing null %, distinct count, sample values (as a comma-joined `<code>` string, monospace), and a link to the generated `ydata-profiling` HTML report (`report_path` in the response — served as a static file; add a tiny FastAPI static-files mount for `backend/reports/` if one doesn't already exist, or note this as a one-line Task 0 addition if discovered missing during Step 3). A "Generate mapping" button navigates to `/batches/[batchId]/mapping`.

**Components:** TanStack Table v8 for the column-stats grid (this is the first of three TanStack tables in this plan — build a small reusable `<DataTable columns={} data={} />` wrapper in `frontend/components/data-table.tsx` now, reused by Tasks 4 and 5).

- [ ] Build the reusable `DataTable` wrapper, the profile page, verify manually, commit.

---

## Task 4: Mapping-review grid (the deliverable's centerpiece)

**Route:** `/batches/[batchId]/mapping`

**Behavior:** Calls `POST /mapping-spec`, renders one row per source column with: the column name (mono), a few **sample values** (from that column's `stats.sample_values` in the response, comma-joined, mono, truncated — this is the row-level context a reviewer needs to judge an override without leaving the grid), a **confidence badge** (colored by bucket — success/`auto_accept`, amber/`human_confirm`, danger/`unmapped`; the numeric confidence shown alongside, not replaced by the color — color augments the number, never substitutes for it), the current best-guess canonical field, its provenance (`deterministic` / `llm` / `deterministic_fallback`, shown as plain text, not another badge — don't over-badge), and an **override control**: a `Select` populated with every candidate field name from that column's ranked `candidates` list (plus "None of these"). Changing the override calls `PATCH /mapping-spec/{id}` immediately (optimistic UI update, roll back on failure) — this is the "accept/override" interaction the Day 5 bullet names directly. A persistent "Confirm mapping" button (disabled while any row's override request is in flight) calls `POST /mapping-spec/{id}/confirm` and navigates to `/batches/[batchId]/load`.

**Components:** the `DataTable` wrapper from Task 3, shadcn `Badge` (for confidence bucket — map bucket to `variant`), `Select` per row for override, `Button`.

**Acceptance:** overriding a column's mapping and confirming produces a `spec_json` (verify via `GET`-ing the batch's quarantine/metrics afterward, or by checking the network tab) that reflects the override, not the original AI guess.

- [ ] Build the page, verify the acceptance criterion manually, commit.

---

## Task 5: Load + exception queue view (with bulk-fix)

**Route:** `/batches/[batchId]/load`

**Behavior:** On load, calls `POST /load`, shows the `LoadSummary` (`total_rows`, `loaded`, `quarantined`) as three stat tiles (numbers as the hero — big number, small label, per this plan's design principles, not a chart for three numbers). Below, calls `GET /quarantine?batch_id=...` and renders the exception queue: one row per quarantined record, showing `error_codes` (as small `Badge`s, one per code, danger-colored), `explanation`, `suggested_fix` (if present, shown with an inline "Apply suggestion" button that pre-fills a correction), and per-row "Fix" (opens an inline edit of the offending field(s) from `raw_record_json`, then `POST /quarantine/{id}/resolve`) / "Ignore" actions. Group the queue by `error_codes` (a source column value can produce a natural grouping key) and offer a **bulk-fix bar per group**: "N rows tagged DATE_AMBIGUOUS — apply format: [DD/MM/YYYY ▾] to all" → `POST /quarantine/bulk-resolve`, matching the spec's own named example.

**Components:** `DataTable`, `Badge` (danger, one per error code), `Button`, a small `Select` for the bulk-fix's correction input (varies by error code — for `DATE_AMBIGUOUS` it's a format choice; for others it may be a free-text corrected value; keep the bulk-fix UI generic — a single text input whose value becomes every matching row's `corrected_values` for the field named in the group — rather than building per-error-code-specific bulk widgets, which is more scope than this plan needs).

**Acceptance:** bulk-fixing every `DATE_AMBIGUOUS` row in a batch that has some (e.g. `crm_snake.csv`, whose row 5 is exactly this per Day 4's test) reduces `quarantine_open` for that batch to zero for that error code, and increases loaded-row counts correspondingly.

- [ ] Build the page, verify the acceptance criterion manually, commit.

---

## Task 6: Metrics dashboard

**Route:** `/metrics`

**Behavior:** Calls `GET /metrics`, renders the counts as a small grid of stat tiles (batches, customers, invoices, support tickets, accounts, quarantine open/fixed, confirmed specs) — numbers as heroes again, consistent with Task 5. No charts for single point-in-time counts; a chart implies a trend over time this data doesn't have yet (don't fabricate a time series). Add this route to the left-rail nav (Task 1/2's shared layout) as the final pipeline stage.

**Components:** a small `<StatTile value={} label={} />` component (plain, reused from Task 5's load-summary tiles — factor it out now if not already shared).

- [ ] Build the page, add the left-rail nav shared across all pages (Upload / Profile / Mapping / Load / Metrics, highlighting the current stage), verify manually, commit.

---

## Task 7: End-to-end manual walkthrough + polish pass

- [ ] Run the full flow by hand against a real seed CSV: upload → profile → review+override at least one mapping → confirm → load → bulk-fix at least one quarantine group → check metrics reflects it.
- [ ] Self-critique against this plan's design principles (frontend-design skill): confidence/severity shown as real data everywhere, provenance visible, monospace used only where alignment matters, single accent color not spent decoratively, no ALL-CAPS/em-dash/arrow tells. Fix anything that drifted.
- [ ] Check keyboard focus is visible on every interactive element (shadcn's defaults mostly cover this — verify, don't assume) and the layout doesn't break down to a narrow viewport.
- [ ] Commit any polish fixes.
