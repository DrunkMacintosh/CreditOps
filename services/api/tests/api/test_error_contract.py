"""The stable error contract (master design section 15).

Every error body must be exactly
``{code, messageVi, retryable, correlationId, details}`` — machine-stable,
Vietnamese-safe, and free of stack traces, prompts, secrets, or raw provider
responses.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from creditops.api.errors import (
    ApiException,
    api_exception_handler,
    unexpected_exception_handler,
    validation_exception_handler,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ApiException, api_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_exception_handler)

    @app.get("/boom-plain")
    async def boom_plain() -> None:
        raise ApiException(
            status_code=409,
            code="STALE_CASE_VERSION",
            message_vi="Phiên bản hồ sơ đã thay đổi.",
        )

    @app.get("/boom-details")
    async def boom_details() -> None:
        raise ApiException(
            status_code=409,
            code="STALE_CASE_VERSION",
            message_vi="Phiên bản hồ sơ đã thay đổi.",
            details={"expectedVersion": 3, "currentVersion": 5},
        )

    @app.get("/boom-unexpected")
    async def boom_unexpected() -> None:
        raise RuntimeError("secret internal state that must never leak")

    return app


def test_error_body_always_carries_the_five_contract_fields() -> None:
    client = TestClient(_build_app())
    body = client.get("/boom-plain").json()
    assert set(body) == {"code", "messageVi", "retryable", "correlationId", "details"}
    assert body["code"] == "STALE_CASE_VERSION"
    assert body["retryable"] is False
    assert body["details"] == {}


def test_details_payload_is_carried_verbatim() -> None:
    client = TestClient(_build_app())
    body = client.get("/boom-details").json()
    assert body["details"] == {"expectedVersion": 3, "currentVersion": 5}


def test_unexpected_exception_never_leaks_internals() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/boom-unexpected")
    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "messageVi", "retryable", "correlationId", "details"}
    assert body["details"] == {}
    assert "secret internal state" not in response.text
