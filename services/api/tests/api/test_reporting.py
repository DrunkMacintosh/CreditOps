"""Role-gated API tests for GET /api/v1/reporting/operations.

Mirrors the house RS256 JWT harness in ``tests/api/test_work_items.py``.  A fake
``ReportingRepository`` (a structural Protocol) backs the one aggregate read.
These tests pin the REPORTING_VIEWER role gate, the camelCase metric shape, and
-- load-bearing -- that NO per-case identifier ever appears in the payload.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.reporting import router as reporting_router
from creditops.application.ports.reporting import (
    GateStatusCount,
    OperationsMetrics,
    OutboxBacklog,
    QueueAgeBucketCount,
    StageCount,
    StatusCount,
)
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _metrics() -> OperationsMetrics:
    return OperationsMetrics(
        tasks_by_status=(
            StatusCount(status="PENDING", count=3),
            StatusCount(status="SUCCEEDED", count=7),
        ),
        queue_age_buckets=(
            QueueAgeBucketCount(status="PENDING", bucket="LE_5M", count=2),
            QueueAgeBucketCount(status="RETRY_WAIT", bucket="GT_60M", count=1),
        ),
        human_gates=(
            GateStatusCount(gate_type="G1_INTAKE_COMPLETE", status="SATISFIED", count=4),
            GateStatusCount(gate_type="G3_RISK_DISPOSITION", status="OPEN", count=2),
        ),
        outbox=OutboxBacklog(undispatched_count=5, max_attempts=2),
        documents_by_stage=(StageCount(stage="REGISTERED", count=6),),
        alerts_by_status=(StatusCount(status="OPEN", count=1),),
    )


class FakeReportingRepository:
    def __init__(self, metrics: OperationsMetrics | None = None) -> None:
        self._metrics = metrics or _metrics()
        self.calls = 0

    async def load_operations_metrics(self) -> OperationsMetrics:
        self.calls += 1
        return self._metrics


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeReportingRepository | None = None,
    wire_repository: bool = True,
) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(settings=Settings(app_env="test"), jwt_verifier=verifier)
    application.include_router(reporting_router)
    if wire_repository:
        application.state.reporting_repository = repository or FakeReportingRepository()
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or ["REPORTING_VIEWER"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def auth(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-reporting"}


def test_reporting_viewer_can_read_operations_report(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)

    response = client.get("/api/v1/reporting/operations", headers=auth(token(signing_key)))

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "SYNTHETIC"
    assert set(body) == {
        "label",
        "tasksByStatus",
        "queueAgeBuckets",
        "humanGatesByTypeStatus",
        "outbox",
        "documentsByStage",
        "alertsByStatus",
    }


def test_metric_families_have_expected_camel_case_shape(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(signing_key)

    body = client.get(
        "/api/v1/reporting/operations", headers=auth(token(signing_key))
    ).json()

    assert body["tasksByStatus"][0] == {"status": "PENDING", "count": 3}
    assert body["queueAgeBuckets"][0] == {
        "status": "PENDING",
        "bucket": "LE_5M",
        "count": 2,
    }
    assert body["humanGatesByTypeStatus"][0] == {
        "gateType": "G1_INTAKE_COMPLETE",
        "status": "SATISFIED",
        "count": 4,
    }
    assert body["outbox"] == {"undispatchedCount": 5, "maxAttempts": 2}
    assert body["documentsByStage"][0] == {"stage": "REGISTERED", "count": 6}
    assert body["alertsByStatus"][0] == {"status": "OPEN", "count": 1}


def test_payload_carries_no_per_case_identifier(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)

    response = client.get("/api/v1/reporting/operations", headers=auth(token(signing_key)))

    raw = response.text
    # No UUID-shaped string, and no case-scoped key, anywhere in the payload.
    assert _UUID_RE.search(raw) is None
    lowered = raw.lower()
    assert "caseid" not in lowered
    assert "case_id" not in lowered

    # And recursively: every leaf is a status/type/stage label or a count.
    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            assert _UUID_RE.search(node) is None

    _walk(response.json())


def test_missing_bearer_is_401(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)

    response = client.get("/api/v1/reporting/operations", headers={"X-Request-ID": "x"})

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_non_reporting_role_is_403_and_does_not_reach_repository(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeReportingRepository()
    client = _build_client(signing_key, repository=repository)

    # A case-participant role (but NOT REPORTING_VIEWER) must still be rejected.
    response = client.get(
        "/api/v1/reporting/operations",
        headers=auth(token(signing_key, roles=["INTAKE_OFFICER", "AUDITOR"])),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.calls == 0


def test_service_unavailable_when_repository_absent(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, wire_repository=False)

    response = client.get("/api/v1/reporting/operations", headers=auth(token(signing_key)))

    assert response.status_code == 503
    assert response.json()["code"] == "REPORTING_SERVICE_UNAVAILABLE"
