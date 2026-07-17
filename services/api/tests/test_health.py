from fastapi.testclient import TestClient

from creditops.main import app


def test_health_is_process_only() -> None:
    assert TestClient(app).get("/api/v1/health").json() == {
        "service": "creditops-api",
        "status": "ok",
    }
