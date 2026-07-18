"""Role-gated API tests for GET/POST /cases/{id}/risk-review.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture
assessment belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc
Demo".
"""

from __future__ import annotations

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
from creditops.application.orchestration.roles import RISK_REVIEWER_ROLE
from creditops.application.ports.orchestration import GateRecord
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.risk_review import (
    ChallengeDispositionRecord,
    LatestRiskReviewRecord,
)
from creditops.config import Settings
from creditops.domain.orchestration import GateStatus, GateType
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000002")
ASSESSMENT_ID = UUID("50000000-0000-0000-0000-000000000002")
HANDOFF_ID = UUID("60000000-0000-0000-0000-000000000002")
CHALLENGE_ID = UUID("70000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _assessment_payload(*, severity: str = "HIGH") -> dict[str, Any]:
    return {
        "challenges": [
            {
                "id": str(CHALLENGE_ID),
                "target": {
                    "maker_source": "CREDIT_UNDERWRITING",
                    "maker_assessment_id": str(uuid4()),
                    "section_path": "risks[0]",
                },
                "challenge_type": "UNSUPPORTED_ASSUMPTION",
                "statement_vi": "Gia dinh chua co can cu.",
                "citations": [{"kind": "CONFIRMED_FACT", "confirmed_fact_id": str(uuid4())}],
                "severity": severity,
                "confidence": "MEDIUM",
                "raised_by": "LLM",
            }
        ]
    }


class FakeRiskReviewRepository:
    def __init__(self, *, has_assessment: bool = True, severity: str = "HIGH") -> None:
        self.has_assessment = has_assessment
        self.severity = severity
        self.dispositions: list[ChallengeDispositionRecord] = []

    async def load_evidence_view(self, case_id: UUID) -> Any:
        raise AssertionError("not used by the API")

    async def load_maker_outputs(self, case_id: UUID, case_version: int) -> Any:
        raise AssertionError("not used by the API")

    async def load_open_gaps(self, case_id: UUID, case_version: int) -> Any:
        raise AssertionError("not used by the API")

    async def load_latest_assessment(self, case_id: UUID) -> LatestRiskReviewRecord | None:
        if case_id != CASE_ID or not self.has_assessment:
            return None
        return LatestRiskReviewRecord(
            assessment_id=ASSESSMENT_ID,
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            agent_role="INDEPENDENT_RISK_REVIEW",
            prompt_version="risk-review-prompt-v1",
            created_at=NOW,
            assessment=_assessment_payload(severity=self.severity),
            handoff_id=HANDOFF_ID,
            handoff_state="READY_FOR_OPERATIONS",
            handoff_created_at=NOW,
        )

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionRecord, ...]:
        return tuple(self.dispositions)

    async def find_persisted(self, **kwargs: object) -> Any:
        raise AssertionError("not used by the API")

    async def persist_assessment(self, **kwargs: object) -> Any:
        raise AssertionError("the API must never write an assessment")

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        assessment_id: UUID,
        challenge_id: UUID | None,
        disposition_type: str,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> ChallengeDispositionRecord:
        record = ChallengeDispositionRecord(
            id=disposition_id,
            assessment_id=assessment_id,
            challenge_id=challenge_id,
            disposition_type=disposition_type,
            rationale_vi=rationale_vi,
            actor_id=actor_id,
            actor_role=actor_role,
            created_at=NOW,
        )
        self.dispositions.append(record)
        return record

    async def append_audit(self, event: object) -> None:
        raise AssertionError("not used by the API")


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.ensure_gate_calls: list[dict[str, Any]] = []

    async def load_snapshot(self, case_id: UUID) -> Any:
        raise AssertionError("not used by the risk-review API")

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        return GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
        )

    async def create_task(self, **kwargs: object) -> Any:
        raise AssertionError("not used by the risk-review API")

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the risk-review API")

    async def append_audit(self, event: object) -> None:
        raise AssertionError("not used by the risk-review API")


class FakeCases:
    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=OFFICER_A,
            requested_amount="1",
            purpose_vi="Vốn lưu động cho nông sản",
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


def _build_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeRiskReviewRepository,
    orchestration_repository: FakeOrchestrationRepository | None = None,
) -> TestClient:
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
    application.state.risk_review_repository = repository
    application.state.orchestration_repository = orchestration_repository
    return TestClient(application)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    return _build_client(
        signing_key, repository=FakeRiskReviewRepository(), orchestration_repository=None
    )


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
            "roles": roles or [RISK_REVIEWER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def test_participant_reads_latest_risk_review_with_challenges(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['INTAKE_OFFICER'])}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assessmentId"] == str(ASSESSMENT_ID)
    assert body["agentRole"] == "INDEPENDENT_RISK_REVIEW"
    assert body["handoff"]["state"] == "READY_FOR_OPERATIONS"
    # (d) unresolved challenges remain visible.
    assert len(body["challenges"]) == 1
    assert body["challenges"][0]["id"] == str(CHALLENGE_ID)
    assert body["challenges"][0]["dispositions"] == []
    assert body["unresolvedChallengeCount"] == 1
    assert body["gateStatus"] == "OPEN"
    # No decision-capable field leaks through the read model.
    lowered = {key.lower() for key in body}
    assert not lowered & {"decision", "approved", "score", "waiver", "resolve", "clear"}


def test_risk_reviewer_role_may_read(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 200


def test_non_participant_role_is_rejected(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['AUDITOR'])}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_unassigned_actor_gets_indistinguishable_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_no_risk_review_yet_is_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    client = _build_client(
        signing_key, repository=FakeRiskReviewRepository(has_assessment=False)
    )
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RISK_REVIEW_NOT_AVAILABLE"


def test_risk_reviewer_can_record_a_challenge_disposition(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRiskReviewRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/challenges/{CHALLENGE_ID}/disposition",
        json={"dispositionType": "MAKER_MUST_REVISE", "rationale": "Can bo sung can cu."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["dispositionType"] == "MAKER_MUST_REVISE"
    assert body["actorRole"] == RISK_REVIEWER_ROLE
    assert body["actorId"] == str(OFFICER_A)
    assert len(repository.dispositions) == 1
    # One HIGH-severity challenge, now disposed: G3 becomes SATISFIED and the
    # human-triggered write path records it -- the checker never does this.
    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.G3_RISK_DISPOSITION
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A


def test_disposition_endpoint_rejects_non_risk_reviewer_actors(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeRiskReviewRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/challenges/{CHALLENGE_ID}/disposition",
        json={"dispositionType": "NOTED", "rationale": "khong duoc phep"},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=['INTAKE_OFFICER'])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.dispositions == []


def test_disposition_on_unknown_challenge_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRiskReviewRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/challenges/{uuid4()}/disposition",
        json={"dispositionType": "NOTED", "rationale": "khong ton tai"},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CHALLENGE_NOT_FOUND"


def test_assessment_level_disposition_requires_noted(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeRiskReviewRepository()
    client = _build_client(signing_key, repository=repository)

    rejected = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/disposition",
        json={"dispositionType": "ACCEPTED_RISK", "rationale": "khong duoc phep"},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert rejected.status_code == 422

    accepted = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/disposition",
        json={"dispositionType": "NOTED", "rationale": "Da xem xet, khong co van de nghiem trong."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert accepted.status_code == 201
    assert accepted.json()["dispositionType"] == "NOTED"
    assert repository.dispositions[-1].challenge_id is None


def test_g3_not_satisfied_when_a_severe_challenge_is_still_undisposed(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # Two challenges would exist in a richer fixture; here disposing the
    # ASSESSMENT LEVEL alone while the (still-existing, HIGH-severity)
    # challenge remains undisposed must not satisfy G3.
    repository = FakeRiskReviewRepository(severity="HIGH")
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/disposition",
        json={"dispositionType": "NOTED", "rationale": "Ghi nhan nhung chua xu ly thach thuc."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    assert orchestration.ensure_gate_calls == []


def test_g3_satisfied_by_noted_when_checker_found_nothing_severe(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # (f) empty/low-severity case: G3 becomes SATISFIED once the explicit
    # assessment-level NOTED disposition is recorded.
    repository = FakeRiskReviewRepository(severity="LOW")
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/disposition",
        json={"dispositionType": "NOTED", "rationale": "Khong co thach thuc nghiem trong."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    assert len(orchestration.ensure_gate_calls) == 1
    assert orchestration.ensure_gate_calls[0]["status"] == GateStatus.SATISFIED


def test_multiple_dispositions_on_same_challenge_all_persist_full_history(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # (c) disagreements persist: a later disposition never deletes or edits
    # an earlier one on the same challenge; the full history stays readable.
    repository = FakeRiskReviewRepository()
    client = _build_client(signing_key, repository=repository)

    first = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/challenges/{CHALLENGE_ID}/disposition",
        json={"dispositionType": "MAKER_MUST_REVISE", "rationale": "Yeu cau bo sung lan 1."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    second = client.post(
        f"/api/v1/cases/{CASE_ID}/risk-review/challenges/{CHALLENGE_ID}/disposition",
        json={"dispositionType": "ESCALATED", "rationale": "Van chua duoc giai quyet, chuyen cap."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert len(repository.dispositions) == 2
    assert repository.dispositions[0].disposition_type == "MAKER_MUST_REVISE"
    assert repository.dispositions[1].disposition_type == "ESCALATED"

    status = client.get(
        f"/api/v1/cases/{CASE_ID}/risk-review",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    challenge = status.json()["challenges"][0]
    assert len(challenge["dispositions"]) == 2
    assert {d["dispositionType"] for d in challenge["dispositions"]} == {
        "MAKER_MUST_REVISE",
        "ESCALATED",
    }
