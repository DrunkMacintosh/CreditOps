"""Role-gated read-only API tests for GET /cases/{id}/underwriting.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture
assessment belongs to the invented SME "Cong ty TNHH Banh Mi Que Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.application.orchestration.roles import RISK_REVIEWER_ROLE
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.underwriting import LatestAssessmentRecord
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
ASSESSMENT_ID = UUID("50000000-0000-0000-0000-000000000001")
HANDOFF_ID = UUID("60000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeUnderwritingReadRepository:
    def __init__(self, *, has_assessment: bool = True) -> None:
        self.has_assessment = has_assessment

    async def load_latest_assessment(
        self, case_id: UUID
    ) -> LatestAssessmentRecord | None:
        if case_id != CASE_ID or not self.has_assessment:
            return None
        return LatestAssessmentRecord(
            assessment_id=ASSESSMENT_ID,
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            agent_role="CREDIT_UNDERWRITING",
            prompt_version="underwriting-prompt-v1",
            created_at=NOW,
            assessment={"business": {"findings": []}},
            handoff_id=HANDOFF_ID,
            handoff_state="READY_FOR_RISK_REVIEW",
            handoff_created_at=NOW,
        )


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
            requested_amount="1",
            purpose_vi="Vốn lưu động",
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


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_resolver=JwksKeyResolver({"keys": [jwk]}),
    )
    application = create_app(
        settings=Settings(app_env="test"),
        jwt_verifier=verifier,
        uow_factory=lambda actor: FakeUnitOfWork(),
    )
    application.state.underwriting_repository = FakeUnderwritingReadRepository()
    return TestClient(application)


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
            "roles": roles or ["INTAKE_OFFICER"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def test_participant_reads_latest_assessment_and_handoff_status(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/underwriting",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assessmentId"] == str(ASSESSMENT_ID)
    assert body["agentRole"] == "CREDIT_UNDERWRITING"
    assert body["caseVersion"] == 1
    assert body["handoff"]["state"] == "READY_FOR_RISK_REVIEW"
    assert body["handoff"]["handoffId"] == str(HANDOFF_ID)
    # No decision-capable field leaks through the read model.
    lowered = {key.lower() for key in body}
    assert not lowered & {"decision", "approved", "score", "waiver"}


def test_risk_reviewer_role_may_read(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/underwriting",
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
            )
        },
    )
    assert response.status_code == 200


def test_non_participant_role_is_rejected(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/underwriting",
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=['AUDITOR'])}"
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_unassigned_actor_gets_indistinguishable_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/underwriting",
        headers={
            "Authorization": f"Bearer {token(signing_key, subject=uuid4())}"
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_missing_assessment_is_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    client.app.state.underwriting_repository = FakeUnderwritingReadRepository(
        has_assessment=False
    )
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/underwriting",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "UNDERWRITING_NOT_AVAILABLE"


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(f"/api/v1/cases/{CASE_ID}/underwriting")
    assert response.status_code in (401, 403)
