from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient
from starlette.responses import Response

from creditops.config import Settings
from creditops.log_redaction import LogRedactor
from creditops.main import create_app
from creditops.observability import StructuredJsonFormatter, configure_structured_logging
from creditops.security_headers import apply_security_headers


def test_signed_urls_and_tokens_are_redacted() -> None:
    redactor = LogRedactor()

    event = redactor.clean({"authorization": "Bearer secret", "signedUrl": "https://storage/token"})

    assert event == {"authorization": "[REDACTED]", "signedUrl": "[REDACTED]"}


def test_redaction_is_recursive_and_does_not_mutate_the_event() -> None:
    redactor = LogRedactor()
    event = {
        "request": {
            "headers": {"Cookie": "session=secret", "Accept": "application/json"},
            "items": [{"api_key": "provider-secret", "task_id": "task-1"}],
        }
    }

    cleaned = redactor.clean(event)

    assert cleaned == {
        "request": {
            "headers": {"Cookie": "[REDACTED]", "Accept": "application/json"},
            "items": [{"api_key": "[REDACTED]", "task_id": "task-1"}],
        }
    }
    assert event["request"]["headers"]["Cookie"] == "session=secret"


def test_common_secret_header_aliases_are_redacted() -> None:
    cleaned = LogRedactor().clean(
        {
            "access_token": "access-secret",
            "X-API-Key": "api-secret",
            "Set-Cookie": "session=secret",
        }
    )

    assert cleaned == {
        "access_token": "[REDACTED]",
        "X-API-Key": "[REDACTED]",
        "Set-Cookie": "[REDACTED]",
    }


def test_embedded_bearer_and_url_credentials_are_redacted() -> None:
    redactor = LogRedactor()

    cleaned = redactor.clean(
        {
            "message": "request failed with Authorization: Bearer abc.def-123",
            "endpoint": "https://user:password@example.test/path?token=secret&safe=yes",
        }
    )

    assert "abc.def-123" not in cleaned["message"]
    assert "password" not in cleaned["endpoint"]
    assert "token=secret" not in cleaned["endpoint"]
    assert "safe=yes" in cleaned["endpoint"]


def test_aws_security_tokens_and_url_fragments_are_redacted() -> None:
    cleaned = LogRedactor().clean(
        {
            "endpoint": (
                "https://objects.example.test/document.pdf?"
                "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
                "X-Amz-Credential=AKIA_TEST%2Fscope&"
                "X-Amz-Security-Token=session-secret&"
                "X-Amz-Signature=signature-secret&safe=yes#access-token-fragment"
            )
        }
    )

    endpoint = cleaned["endpoint"]
    assert isinstance(endpoint, str)
    assert "AKIA_TEST" not in endpoint
    assert "session-secret" not in endpoint
    assert "signature-secret" not in endpoint
    assert "access-token-fragment" not in endpoint
    assert "safe=yes" in endpoint


def test_relative_access_targets_and_non_http_dsn_values_are_redacted() -> None:
    cleaned = LogRedactor().clean(
        {
            "message": (
                "GET /storage/v1/object/sign/private.pdf?"
                "X-Amz-Security-Token=session-secret&business_id=customer-42&limit=20"
            ),
            "error": "postgresql://db-user:db-password@db.internal:5432/creditops",
        }
    )

    assert "session-secret" not in cleaned["message"]
    assert "customer-42" not in cleaned["message"]
    assert "limit=20" in cleaned["message"]
    assert "db-password" not in cleaned["error"]


def test_structured_formatter_redacts_context_and_exception_text() -> None:
    formatter = StructuredJsonFormatter(service_name="creditops-api")
    record = logging.LogRecord(
        name="creditops.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failed for Bearer provider-token",
        args=(),
        exc_info=None,
    )
    record.context = {"request_id": "req-1", "signed_url": "https://secret"}

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "creditops-api"
    assert payload["severity"] == "ERROR"
    assert payload["request_id"] == "req-1"
    assert payload["signed_url"] == "[REDACTED]"
    assert "provider-token" not in payload["message"]


def test_structured_context_cannot_replace_log_envelope() -> None:
    formatter = StructuredJsonFormatter(service_name="creditops-api")
    record = logging.LogRecord(
        name="creditops.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="expected message",
        args=(),
        exc_info=None,
    )
    record.context = {
        "service": "attacker-service",
        "severity": "INFO",
        "message": "forged message",
    }

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "creditops-api"
    assert payload["severity"] == "WARNING"
    assert payload["message"] == "expected message"


def test_structured_logging_replaces_named_server_handlers() -> None:
    logger_names = ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")
    original = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in logger_names
    }
    try:
        for name in logger_names:
            logging.getLogger(name).addHandler(logging.NullHandler())
            logging.getLogger(name).propagate = False

        configure_structured_logging(service_name="creditops-api")

        for name in logger_names:
            logger = logging.getLogger(name)
            assert logger.handlers == []
            assert logger.propagate is True
    finally:
        for name, (handlers, propagate) in original.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.propagate = propagate


def test_security_headers_are_fail_closed() -> None:
    response = apply_security_headers(Response())

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


def test_fastapi_installs_security_headers_middleware() -> None:
    app = create_app(settings=Settings(app_env="test"))

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
