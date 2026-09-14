# Demo video script (3-5 minutes)

Six beats, each mapped to an exact action against this repo's actual seed data and running services — not a vague "show the UI" note. Record against **local dev** (`npm run dev` + `uv run uvicorn`) so beat 5 can run a real migration on camera; the deployed instance (Render/Vercel/Neon) is for the README link and casual clicking, not this recording.

**Before recording:**
- Both services up: backend on `:8000`, frontend on `:3000`.
- Fresh seed data: `cd backend && rm -rf seed/output && uv run python -m seed.eval_mapping` (also prints the benchmark numbers for beat 6).
- `demo-tenant` (the frontend's fixed tenant) is already pre-seeded from this session's Day 7 work — 4 batches, 55 customers, 9 open quarantine rows, 2 open dedupe candidates. That's your beat-6 dashboard state; don't wipe it.
- Have `backend/seed/output/crm_legacy.csv` and `backend/tests/fixtures/kyc_status_demo.csv` ready to pick in the file dialog.

---

### 1. Upload a deliberately messy file (0:00–0:30)

Open `http://localhost:3000`. Narrate: *"This is a legacy CRM export — abbreviated headers, no consistent casing, the kind of file that shows up on day one of any real customer onboarding."*

Upload `backend/seed/output/crm_legacy.csv`, source name `crm-legacy-demo`, kind `CRM`. Click through to the profile page — point out the null %, distinct counts, and sample values Polars computed per column, and the link to the full `ydata-profiling` report.

### 2. Auto-profile + proposed mapping with confidence badges (0:30–1:30)

Click "Generate mapping." Narrate the three-bucket story as the grid renders: *"Green is auto-accepted, amber is 'a human should look at this,' red is unmapped — nothing gets silently guessed."* Point at one auto-accept row (deterministic, high confidence) and one amber row — read its confidence number out loud, not just the color: *"Color augments the number, it never replaces it."*

### 3. Confirm one ambiguous mapping (1:30–2:00)

Pick an amber (`human_confirm`) row. Open its override dropdown, show the ranked candidate list, either accept the pre-filled guess or pick a different one — watch the `PATCH` fire and the badge update. Click **Confirm mapping**.

### 4. Load it — valid rows, exception queue, bulk-fix (2:00–3:00)

Land on the load page. Narrate the three stat tiles as they populate: total / loaded / still in queue. Scroll to the exception queue — read one row's `explanation` and `error_codes` out loud. Find the **DATE_AMBIGUOUS** bulk-fix bar (there are two DATE_AMBIGUOUS rows across the pre-seeded batches — this new upload may add a third): type the corrected format into the value field, click **Apply to all**, watch the count drop and the "still in queue" tile update live.

### 5. The money shot: add a required field live, without breaking prior data (3:00–4:15)

Narrate: *"Now the customer says every record needs a KYC verification status. Watch what that actually takes — and watch that it doesn't break anything already loaded."*

- Terminal, split screen: show `backend/app/models.py`'s `Customer.kyc_status` and `backend/app/canonical.py`'s `kyc_status` `CanonicalField` entry already in place (or, for a from-scratch version of this beat, git-stash them first and un-stash live to show the actual diff) — either way, narrate the two real migrations: `alembic revision --autogenerate` for the nullable column, a hand-written one for the backfill + `NOT NULL`.
- Back in the UI: upload `backend/tests/fixtures/kyc_status_demo.csv` (3 rows: verified / blank / rejected) as a **new** batch. Generate its mapping — point out `kyc_status` now auto-maps, because it's a real registered field the scorer can see.
- Confirm, load. Show the split live: 2 loaded (`verified`, `rejected`), 1 quarantined with `MISSING_REQUIRED` on `kyc_status` for the blank row.
- Switch to the metrics dashboard (or the already-loaded `demo-tenant` customers, if querying the DB directly) and show the *original* `crm-legacy-demo` batch's customers still have `kyc_status = "pending"` — the server-side default, untouched, no retroactive quarantine. *"Old spec, old data, completely unaffected. New spec enforces it going forward."*

### 6. End on the metrics dashboard (4:15–5:00)

Navigate to `/metrics`. Read the stat tiles: batches, customers, quarantine open/fixed, confirmed specs. Then cut to (or narrate from memory) the benchmark table from `uv run python -m seed.eval_mapping`:

> *"On a held-out set of column names from real Salesforce, HubSpot, QuickBooks, and Zendesk exports — never seen while building the alias lists — a pure fuzzy-string baseline gets 79% top-1 accuracy. The hybrid scorer with local embeddings alone actually dips to 75%. Adding the LLM tie-break, called only for the ambiguous columns, recovers past both to 83%. That delta is the whole argument for the AI layer — quantified, not asserted."*

Close on the architecture diagram in the README, or the terminal running the full test suite green (`181 passed`).

---

**Total: ~4:45.** Trim beat 1 or 6 first if running long — beats 3-5 (the review UI, the exception queue, and the schema-evolution pivot) are the ones that actually differentiate this from a script that just loads a CSV into a database.
