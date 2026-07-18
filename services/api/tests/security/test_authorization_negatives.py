"""Authorization negatives for the newer case-scoped APIs (in-process
``TestClient``, RS256 JWT fixtures copied from ``tests/api/test_risk_review.py``).

Covers ``api/gap_requests.py``, ``api/intake.py`` and ``api/audit.py``: every
state-changing or read endpoint on these three routers must, for EACH of
- a request with no bearer token,
- a request with a syntactically valid token that lacks the required role,
- a request from an actor who is not assigned to the case,
fail closed with the flat five-field ``ApiError`` contract
(``code``/``messageVi``/``correlationId``/``retryable``/``details``) and never
leak a stack trace, a SQL fragment, or a ``creditops.`` python module path into
the response body.

All customer data, cases, and roles here are synthetic fixtures created solely
for this test suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.application.orchestration.roles import INTAKE_OFFICER_ROLE, RISK_REVIEWER_ROLE
from creditops.application.ports.gap_requests import (
    GapRequestBatchDispositionRecord,
    PersistedGapRequestBatch,
)
from creditops.application.ports.intake import CurrentHandoff
from creditops.application.ports.orchestration import AuditEventRow
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.gap_request_batches import GapRequestBatch, compute_open_gap_snapshot_hash
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000b1")
BATCH_ID = UUID("30000000-0000-0000-0000-0000000000b1")
HANDOFF_ID = UUID("40000000-0000-0000-0000-0000000000b1")
AUDIT_EVENT_ID = UUID("50000000-0000-0000-0000-0000000000b1")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

#: Markers that must never appear in a response body: a leaked traceback, a
#: leaked SQL driver name, or a leaked internal python module path.
_LEAK_MARKERS = ("Traceback", "psycopg", "creditops.")

_FLAT_ERROR_KEYS = {"code", "messageVi", "correlationId", "retryable", "details"}


def _assert_no_internal_leakage(response: Any) -> None:
    text = response.text
    for marker in _LEAK_MARKERS:
        assert marker not in text, f"response leaked {marker!r}: {text}"


def _assert_flat_error_contract(response: Any, *, code: str) -> None:
    body = response.json()
    assert set(body) == _FLAT_ERROR_KEYS
    assert body["code"] == code
    assert isinstance(body["messageVi"], str) and body["messageVi"]
    assert isinstance(body["correlationId"], str) and body["correlationId"]
    assert isinstance(body["retryable"], bool)
    assert isinstance(body["details"], dict)
    _assert_no_internal_leakage(response)


class FakeCases:
    """Only ``(CASE_ID, OFFICER_A)`` is an assigned pair; everything else is
    unassigned, whether or not the case id even exists."""

    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
            requested_amount="1",
            purpose_vi="Vốn lưu động cho nông sản (demo)",
            created_at=NOW,
        )


class FakeUnitOfWork:
    cases = FakeCases()

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=JwksKeyResolver({"keys": [jwk]})
    )
    application = create_app(
        settings=Settings(app_env="test"),
        jwt_verifier=verifier,
        uow_factory=lambda actor: FakeUnitOfWork(),
    )
    return TestClient(application)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    return _build_client(signing_key)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = OFFICER_A,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or [INTAKE_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _authorization(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    #: A genuine human role that fails THIS endpoint's role gate.
    wrong_role: str
    body: dict[str, object] | None


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "assemble_gap_request_batch",
        "POST",
        f"/api/v1/cases/{CASE_ID}/gap-request-batches",
        RISK_REVIEWER_ROLE,
        None,
    ),
    Endpoint(
        "get_gap_request_batch",
        "GET",
        f"/api/v1/cases/{CASE_ID}/gap-request-batches",
        "AUDITOR",
        None,
    ),
    Endpoint(
        "record_gap_request_batch_disposition",
        "POST",
        f"/api/v1/cases/{CASE_ID}/gap-request-batches/{BATCH_ID}/disposition",
        RISK_REVIEWER_ROLE,
        {"dispositionType": "REJECTED", "rationale": "Không được phép."},
    ),
    Endpoint(
        "complete_intake",
        "POST",
        f"/api/v1/cases/{CASE_ID}/intake-completion",
        RISK_REVIEWER_ROLE,
        None,
    ),
    Endpoint(
        "get_handoff",
        "GET",
        f"/api/v1/cases/{CASE_ID}/handoffs",
        "AUDITOR",
        None,
    ),
    Endpoint(
        "list_audit_events",
        "GET",
        f"/api/v1/cases/{CASE_ID}/audit-events",
        "AUDITOR",
        None,
    ),
)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_missing_token_is_401_with_flat_contract(
    client: TestClient, endpoint: Endpoint
) -> None:
    response = client.request(endpoint.method, endpoint.path, json=endpoint.body)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_flat_error_contract(response, code="AUTHENTICATION_REQUIRED")


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_wrong_role_is_403_with_flat_contract(
    client: TestClient, signing_key: rsa.RSAPrivateKey, endpoint: Endpoint
) -> None:
    response = client.request(
        endpoint.method,
        endpoint.path,
        json=endpoint.body,
        headers=_authorization(token(signing_key, roles=[endpoint.wrong_role])),
    )

    assert response.status_code == 403
    _assert_flat_error_contract(response, code="INSUFFICIENT_ROLE")


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_unassigned_actor_is_404_with_flat_contract(
    client: TestClient, signing_key: rsa.RSAPrivateKey, endpoint: Endpoint
) -> None:
    # A role that WOULD pass this endpoint's role gate, but a subject that is
    # not assigned to CASE_ID.
    response = client.request(
        endpoint.method,
        endpoint.path,
        json=endpoint.body,
        headers=_authorization(
            token(signing_key, subject=uuid4(), roles=[INTAKE_OFFICER_ROLE])
        ),
    )

    assert response.status_code == 404
    _assert_flat_error_contract(response, code="CASE_NOT_ACCESSIBLE")


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
def test_unassigned_actor_and_nonexistent_case_are_byte_identical_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey, endpoint: Endpoint
) -> None:
    """Row-access denial must be indistinguishable from "case does not exist"."""

    unassigned_path = endpoint.path
    nonexistent_path = endpoint.path.replace(str(CASE_ID), str(uuid4()))
    headers = _authorization(
        token(signing_key, subject=uuid4(), roles=[INTAKE_OFFICER_ROLE])
    )

    unassigned = client.request(
        endpoint.method, unassigned_path, json=endpoint.body, headers=headers
    )
    nonexistent = client.request(
        endpoint.method, nonexistent_path, json=endpoint.body, headers=headers
    )

    assert unassigned.status_code == nonexistent.status_code == 404
    unassigned_body = {k: v for k, v in unassigned.json().items() if k != "correlationId"}
    nonexistent_body = {k: v for k, v in nonexistent.json().items() if k != "correlationId"}
    assert unassigned_body == nonexistent_body


# -- non-vacuous sanity: an authorized, assigned actor DOES get through -------
#
# Without these, the 403/404 assertions above could pass merely because every
# request happens to fail (e.g. a misrouted path); each canary proves the
# authorized path reaches real business logic and returns 200.


class FakeGapRequestRepository:
    def __init__(self, batch: GapRequestBatch) -> None:
        self._batch = batch

    async def load_open_gaps(self, case_id: UUID, case_version: int) -> tuple[Any, ...]:
        return ()

    async def load_current_batch(
        self, case_id: UUID, case_version: int
    ) -> GapRequestBatch | None:
        if case_id == CASE_ID and case_version == 1:
            return self._batch
        return None

    async def persist_batch(self, batch: GapRequestBatch) -> PersistedGapRequestBatch:
        raise AssertionError("not exercised by the authorization canary")

    async def record_disposition(self, **kwargs: object) -> GapRequestBatchDispositionRecord:
        raise AssertionError("not exercised by the authorization canary")

    async def load_dispositions(self, batch_id: UUID) -> tuple[Any, ...]:
        return ()


def test_authorized_participant_reads_gap_request_batch(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    batch = GapRequestBatch(
        id=BATCH_ID,
        case_id=CASE_ID,
        case_version=1,
        items=(),
        open_gap_snapshot_hash=compute_open_gap_snapshot_hash([]),
    )
    client = _build_client(signing_key)
    client.app.state.gap_request_repository = FakeGapRequestRepository(batch)  # type: ignore[attr-defined]

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/gap-request-batches",
        headers=_authorization(token(signing_key, roles=[INTAKE_OFFICER_ROLE])),
    )

    assert response.status_code == 200
    assert response.json()["batch"]["batchId"] == str(BATCH_ID)
    assert response.json()["gateStatus"] == "OPEN"


class FakeIntakeRepository:
    async def load_intake_evidence(self, case_id: UUID, case_version: int) -> Any:
        raise AssertionError("not exercised by the authorization canary")

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None:
        if case_id == CASE_ID and case_version == 1:
            return CurrentHandoff(
                id=HANDOFF_ID,
                case_id=CASE_ID,
                case_version=1,
                state="READY_FOR_SPECIALIST_REVIEW",
                created_at=NOW,
            )
        return None

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool:
        raise AssertionError("not exercised by the authorization canary")

    async def persist_handoff(self, handoff: Any, *, actor_id: UUID) -> Any:
        raise AssertionError("not exercised by the authorization canary")

    async def append_audit(self, event: Any) -> None:
        raise AssertionError("not exercised by the authorization canary")


def test_authorized_participant_reads_handoff(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)
    client.app.state.intake_repository = FakeIntakeRepository()  # type: ignore[attr-defined]

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/handoffs",
        headers=_authorization(token(signing_key, roles=[INTAKE_OFFICER_ROLE])),
    )

    assert response.status_code == 200
    assert response.json()["handoffId"] == str(HANDOFF_ID)


class FakeAuditOrchestrationRepository:
    async def list_audit_events(
        self, case_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]:
        if case_id != CASE_ID:
            return (), None
        return (
            (
                AuditEventRow(
                    id=AUDIT_EVENT_ID,
                    case_id=CASE_ID,
                    case_version=1,
                    event_type="CASE_CREATED",
                    actor_type="HUMAN",
                    actor_id=OFFICER_A,
                    artifact_type="CASE",
                    artifact_id=CASE_ID,
                    event_data={"note": "demo"},
                    created_at=NOW,
                ),
            ),
            None,
        )


def test_authorized_participant_reads_audit_events(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key)
    client.app.state.orchestration_repository = FakeAuditOrchestrationRepository()  # type: ignore[attr-defined]

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/audit-events",
        headers=_authorization(token(signing_key, roles=[INTAKE_OFFICER_ROLE])),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["id"] == str(AUDIT_EVENT_ID)
