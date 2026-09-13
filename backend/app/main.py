from fastapi import FastAPI

from app.routers.profile import router as profile_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)
app.include_router(profile_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
