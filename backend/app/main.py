from fastapi import FastAPI

from app.routers.load import router as load_router
from app.routers.mapping_spec import router as mapping_spec_router
from app.routers.profile import router as profile_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)
app.include_router(profile_router)
app.include_router(mapping_spec_router)
app.include_router(load_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
