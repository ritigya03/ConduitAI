from fastapi import FastAPI

from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
