# Deploying to free tiers

**Neon (Postgres) + Render (FastAPI backend + mock-CRM) + Vercel (Next.js frontend)** — the free-tier stack from `compass_artifact_wf-...md`'s §9. Railway and Fly.io no longer have real free tiers as of 2026; don't use old tutorials pointing at them.

Expect cold starts on Render's free plan (15-min sleep after inactivity) — warm both services up (`curl` their `/health`) before a live demo.

## 1. Neon — Postgres

1. [neon.tech](https://neon.tech) → sign up (no card) → **New Project**. Name it `conduitai`, pick any region.
2. Copy the connection string from the dashboard (**Connection Details**, "Pooled connection" is fine). It looks like:
   `postgresql://<user>:<password>@<host>.neon.tech/conduitai?sslmode=require`
3. This project uses SQLAlchemy's `psycopg` (v3) driver, which needs the `postgresql+psycopg://` scheme, not plain `postgresql://` — rewrite the copied string's scheme before using it anywhere below.
4. Run migrations against it once, from your machine, before the API ever starts:
   ```bash
   cd backend
   DATABASE_URL="postgresql+psycopg://<user>:<password>@<host>.neon.tech/conduitai?sslmode=require" uv run alembic upgrade head
   ```

## 2. Render — backend + mock-CRM

This repo's `render.yaml` (a [Render Blueprint](https://render.com/docs/blueprint-spec)) defines both web services in one file.

1. Push this repo to GitHub if you haven't (`git push`).
2. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint** → connect your GitHub account → select this repo. Render reads `render.yaml` and proposes both services (`conduitai-api`, `conduitai-mock-crm`).
3. Before applying, set the two secret env vars Render will prompt for on `conduitai-api` (marked `sync: false` in `render.yaml` so they're never committed):
   - `DATABASE_URL` — the Neon connection string from Step 1 (with the `postgresql+psycopg://` scheme).
   - `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com) (free, no card).
   - `CORS_ALLOWED_ORIGINS` — leave blank for now, come back after Step 3.
4. Apply the blueprint. Both services build from their `Dockerfile`s and deploy — first build takes a few minutes.
5. Once live, verify:
   ```bash
   curl https://conduitai-api.onrender.com/health
   curl https://conduitai-mock-crm.onrender.com/health
   ```
   (Render slugifies the `name` field from `render.yaml` into the subdomain — if yours differs, use the actual URL shown in the dashboard.)

## 3. Vercel — frontend

```bash
cd frontend
vercel login          # opens a browser to authenticate
vercel link            # creates/links a Vercel project for this directory
vercel env add NEXT_PUBLIC_API_BASE_URL production
# paste: https://conduitai-api.onrender.com   (your actual Render API URL from Step 2)
vercel --prod
```

Note the deployed URL Vercel prints (`https://<project>.vercel.app`).

## 4. Close the loop: CORS

Go back to Render → `conduitai-api` → **Environment** → set `CORS_ALLOWED_ORIGINS` to your Vercel URL from Step 3 (e.g. `https://conduitai.vercel.app`) → save (triggers a redeploy).

## 5. Verify end to end

Open the Vercel URL, upload `backend/seed/output/crm_snake.csv` (regenerate seed data first if needed: `cd backend && uv run python -m seed.eval_mapping`), and walk it through profile → mapping → confirm → load. If the upload page hangs, it's almost always the Render free-tier cold start (up to ~60s) — reload and retry once it's warm.

## Everything still runs locally without any of this

```bash
docker compose up --build   # or: uv run uvicorn app.main:app --reload (per service), see backend/README notes
```

Deployment is for the shareable link; local Docker Compose remains the fast local dev loop and what `docs/DEMO_SCRIPT.md`'s recording uses.
