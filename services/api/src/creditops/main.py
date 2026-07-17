from fastapi import FastAPI

from creditops.config import Settings

settings = Settings()
app = FastAPI(title="SHB CreditOps EvidenceGraph", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"service": settings.service_name, "status": "ok"}


@app.get("/api/v1/ready")
def ready() -> dict[str, str]:
    return {"service": settings.service_name, "status": "configuration-valid"}
