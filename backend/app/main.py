from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers.connect import router as connect_router
from app.routers.dedupe import router as dedupe_router
from app.routers.load import router as load_router
from app.routers.mapping_spec import router as mapping_spec_router
from app.routers.metrics import router as metrics_router
from app.routers.profile import router as profile_router
from app.routers.quarantine import router as quarantine_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves the ydata-profiling HTML reports app.report generates, so the
# frontend's profile page can link straight to `{API_BASE}/reports/{batch_id}.html`
# instead of needing a dedicated download endpoint. REPORTS_DIR is
# computed directly here (matching app.report's own definition) rather
# than imported from app.report -- importing that module pulls in
# ydata-profiling's full dependency chain (pandas, matplotlib, scipy...)
# at *app startup*, on every process, whether or not /profile is ever
# called. On a memory-constrained deployment that's a real cost paid for
# nothing (found running this for real on Render's free 512MB tier).
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")

app.include_router(upload_router)
app.include_router(profile_router)
app.include_router(mapping_spec_router)
app.include_router(load_router)
app.include_router(quarantine_router)
app.include_router(metrics_router)
app.include_router(connect_router)
app.include_router(dedupe_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
