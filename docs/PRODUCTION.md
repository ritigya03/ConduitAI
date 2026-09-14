# Moving ConduitAI to a VPC / private cloud

This is a portfolio build running on free-tier Neon/Render/Vercel with everything public-facing by default — the honest, deliberate trade-off for a project meant to be cloned and clicked through in five minutes. This doc is the answer to "what would you actually do in production," written the way a Forward-Deployed Engineer would need to answer it in front of a customer's security team, not as a diagram nobody checks against the running system.

## Network topology

```
                              ┌─────────────────────────────────────────┐
                              │                  VPC                     │
                              │                                           │
  Internet ──► ALB/NLB ───────┼──► Public subnet                         │
                (public       │      │   NAT Gateway                     │
                 subnet)      │      │                                   │
                              │      ▼                                   │
                              │   Private subnet(s)                      │
                              │      │  api (FastAPI, autoscaled)        │
                              │      │  mock-crm / external connectors   │
                              │      │  worker (async job processing)    │
                              │      │                                   │
                              │      ▼                                   │
                              │   Private subnet (data tier)             │
                              │      │  Postgres (RDS/Cloud SQL,         │
                              │      │            no public IP)          │
                              │      │  object store (S3/GCS, VPC        │
                              │      │            endpoint, no public    │
                              │      │            internet path)         │
                              │                                           │
  Ops laptop ──► VPN/bastion ─┼──────► private subnets (break-glass only) │
                              └─────────────────────────────────────────┘
```

- **API tier** (what's currently `backend/`) sits in a private subnet behind an internal or public load balancer, never with a public IP of its own — inbound traffic terminates at the LB, which is the only thing in a public subnet besides the NAT gateway.
- **Database has no public IP, ever.** Today's Neon/Render setup is reachable from the public internet by connection string (fine for a free-tier demo, not for a real customer's data). In a VPC it's a private-subnet-only RDS/Cloud SQL instance; the only way in is from the API tier's security group, or a bastion host / VPN for break-glass ops access — never a direct `psql` from a laptop over the internet.
- **Egress is via NAT gateway only.** The one thing that genuinely needs internet access from inside the private subnets is the LLM tie-break call (Groq/Ollama) and, if connecting a real customer source system, that system's API — both go out through NAT, never in.
- **`mock-crm`** stands in for "a customer's actual CRM/billing API" in this build. In production that's typically the reverse: *their* system calls *into* your VPC via a signed webhook or a scoped, audited pull — either way it's a deliberate integration point, not an open connector.
- **The worker/job tier** doesn't exist yet in this build (everything runs synchronously in-request — see "What changes at scale" below); it would live in the same private subnet as the API, consuming from a durable queue instead of `BackgroundTasks`.

## PII handling

This is the section that should come up unprompted in any real conversation about this system, because it's the one place the current design already makes the right call by construction, not as an afterthought:

- **Classify PII during profiling.** `app/profiling.py` already computes per-column type/format signals (email-match fraction, phone-match fraction, ...) — the same signal that helps mapping accuracy is exactly what a PII classifier needs. In production, tag columns matching email/phone/SSN/national-ID patterns at profile time and carry that tag through to the mapping spec, not as a separate manual step.
- **Only column names and a few sample values ever reach the LLM — never a full dataset, and never in production would that touch a third-party API at all.** `app/llm_mapper.py`'s prompt sends a column name, its inferred type, and up to 5 sample values — enough for the model to judge what a column *is*, never enough to reconstruct who's in the dataset. This is already true today because it made the scorer cheap and fast; in a regulated production deployment it's also the entire PII answer for the AI layer. Free-tier LLM providers generally train on inputs by default (Groq, Google, Mistral's free tiers) — another reason full datasets never touch them, today or in production. The production version of this would run entirely in-VPC (see below).
- **Field-level encryption or tokenization for sensitive columns** (email, phone, national ID, anything classified as PII above) at rest — application-level encryption or a tokenization service in front of the canonical tables, so a database snapshot or a compromised read replica doesn't hand over raw PII.
- **Data retention and deletion.** `raw_records` is deliberately immutable and append-only today (it's the audit trail — see `app/models.py`'s docstring) with no retention policy, which is correct for a demo and wrong for production: real deployments need a retention window per data class, and a real deletion path (not a soft-delete flag) for right-to-erasure requests that actually removes PII from `raw_records`, canonical tables, *and* any quarantine rows still holding the original raw JSON.
- **Minimize what leaves the boundary, generally.** The mapping spec, the profile stats, the metrics dashboard — none of them need to leave the VPC to be useful. The one thing that currently calls an external service at all is the LLM tie-break, and even that only ever sees column metadata.

## What changes at real scale

- **A durable workflow engine instead of synchronous HTTP calls.** Every stage today (`profile` → `mapping-spec` → `load`) is a plain FastAPI request/response — fine for a demo file, wrong for a multi-GB customer export or a pipeline that needs to survive a pod restart mid-load. Temporal is the correct answer here: durable execution state, automatic retries with backoff already built into the primitive (this build hand-rolled `tenacity` retries for the LLM/mock-API calls specifically because there's no workflow engine underneath yet), and visibility into exactly which stage a given batch is stuck in.
- **Streaming ingestion for large files.** `app/routers/upload.py` reads the whole file into memory and parses it with `csv.DictReader` in one pass — fine for a demo CSV, not for a multi-hundred-MB export. Production ingestion streams rows through Polars' lazy API instead of materializing the whole file, and caps/chunks the upload at the API boundary.
- **A real object store for landing**, not just a Postgres `raw_records` JSONB table. Works fine at demo scale; a production landing zone puts the raw bytes in S3/GCS (with the VPC endpoint from the diagram above) and keeps Postgres for structured metadata and the parsed row-level JSONB, not the original file.
- **Great Expectations Data Docs for multi-team governance.** Pandera (what this build uses) is the right call for a single-team, code-first validation layer — it's simpler and the checks live next to the code that needs them. GX's value is Data Docs: a shareable, browsable report of what's validated and why, for when validation rules need to be reviewed by people who don't read Python. Worth adding the moment more than one team owns canonical fields.
- **A self-hosted or VPC-hosted LLM**, not Groq/Ollama-over-the-internet. This build's free-tier chain (Groq → local Ollama → deterministic fallback) is the right zero-budget choice for a portfolio project; a real deployment handling actual customer data would run the tie-break model inside the VPC (a self-hosted open-weight model, or a cloud provider's VPC-peered inference endpoint) so column names/samples never leave the boundary at all, closing even the narrow gap described in the PII section above.
- **Multi-tenant isolation gets stricter.** Every table already carries `tenant_id` and every query is scoped by it — the right foundation. At real scale that graduates to Postgres row-level security enforcing the scoping at the database layer (not just application code discipline), and likely per-tenant encryption keys for the field-level encryption mentioned above.
