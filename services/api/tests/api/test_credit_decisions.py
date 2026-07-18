"""Role-gated API tests for POST/GET /cases/{id}/human-credit-decisions.

Human-only authority: the ``CREDIT_APPROVER`` JWT role plus a case assignment
are both required and both fail closed.  The router is mounted onto the app
built by ``create_app`` here (its ``main.py`` wiring is a deferred lead
decision), and ``app.state.credit_decision_repository`` is injected directly.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration; the fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from creditops.api.auth import JwksKeyResolver, JwtVerifier
from creditops.api.credit_decisions import CREDIT_APPROVER_ROLE
from creditops.api.credit_decisions import router as credit_decisions_router
from creditops.application.ports.credit_decisions import (
    DecisionBinding,
    RecordedDecision,
    RecordedTermSnapshot,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-00000000000e")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeCreditDecisionRepository:
    def __init__(
        self,
        *,
        current_version: int = 1,
        latest_memo: UUID | None = None,
        latest_risk: UUID | None = None,
        latest_underwriting: UUID | None = None,
    ) -> None:
        self.current_version = current_version
        self.latest_memo = latest_memo
        self.latest_risk = latest_risk
        self.latest_underwriting = latest_underwriting
        self.records: dict[tuple[UUID, int], RecordedDecision] = {}
        self.record_calls = 0

    async def load_decision_binding(self, case_id: UUID) -> DecisionBinding | None:
        if case_id != CASE_ID:
            return None
        return DecisionBinding(
            current_case_version=self.current_version,
            latest_memo_artifact_id=self.latest_memo,
            latest_risk_assessment_id=self.latest_risk,
            latest_underwriting_assessment_id=self.latest_underwriting,
        )

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None:
        return self.records.get((case_id, case_version))

    async def record_decision(self, *, decision: object, snapshot: object) -> RecordedDecision:
        self.record_calls += 1
        key = (decision.case_id, decision.case_version)  # type: ignore[attr-defined]
        if key in self.records:
            return replace(self.records[key], created=False)
        snapshot_record: RecordedTermSnapshot | None = None
        if snapshot is not None:
            snapshot_record = RecordedTermSnapshot(
                id=snapshot.id,  # type: ignore[attr-defined]
                decision_id=decision.id,  # type: ignore[attr-defined]
                case_id=decision.case_id,  # type: ignore[attr-defined]
                case_version=decision.case_version,  # type: ignore[attr-defined]
                terms=snapshot.terms.model_dump(mode="json"),  # type: ignore[attr-defined]
                snapshot_hash=snapshot.snapshot_hash,  # type: ignore[attr-defined]
                created_at=NOW,
            )
        record = RecordedDecision(
            id=decision.id,  # type: ignore[attr-defined]
            case_id=decision.case_id,  # type: ignore[attr-defined]
            case_version=decision.case_version,  # type: ignore[attr-defined]
            decision=decision.decision.value,  # type: ignore[attr-defined]
            rationale_vi=decision.rationale_vi,  # type: ignore[attr-defined]
            decided_by=decision.decided_by,  # type: ignore[attr-defined]
            decided_by_role=decision.decided_by_role,  # type: ignore[attr-defined]
            memo_artifact_id=decision.memo_artifact_id,  # type: ignore[attr-defined]
            risk_assessment_id=decision.risk_assessment_id,  # type: ignore[attr-defined]
            underwriting_assessment_id=decision.underwriting_assessment_id,  # type: ignore[attr-defined]
            conditions=decision.conditions,  # type: ignore[attr-defined]
            created_at=NOW,
            snapshot=snapshot_record,
            created=True,
        )
        self.records[key] = record
        return record


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
    repository: FakeCreditDecisionRepository,
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
    application.include_router(credit_decisions_router)
    application.state.credit_decision_repository = repository
    return TestClient(application)


@pytest.fixture
def client(signing_key: rsa.RSAPrivateKey) -> TestClient:
    return _build_client(signing_key, repository=FakeCreditDecisionRepository())


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
            "roles": roles or [CREDIT_APPROVER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _url() -> str:
    return f"/api/v1/cases/{CASE_ID}/human-credit-decisions"


def test_approver_records_approved_with_conditions_and_terms(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_WITH_CONDITIONS",
            "rationale": "Phê duyệt có điều kiện.",
            "caseVersion": 1,
            "conditions": ["Bổ sung hợp đồng bảo đảm."],
            "terms": {"amount": "5000000000", "currency": "VND", "term": "12 tháng", "rate": "9.5"},
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "APPROVED_WITH_CONDITIONS"
    assert body["decidedByRole"] == CREDIT_APPROVER_ROLE
    assert body["decidedBy"] == str(OFFICER_A)
    assert body["conditions"] == ["Bổ sung hợp đồng bảo đảm."]
    assert body["approvedTerms"] is not None
    assert len(body["approvedTerms"]["snapshotHash"]) == 64
    assert body["approvedTerms"]["terms"]["currency"] == "VND"


def test_missing_conditions_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_WITH_CONDITIONS",
            "rationale": "Thiếu điều kiện.",
            "caseVersion": 1,
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_CREDIT_DECISION"
    assert repository.record_calls == 0


def test_declined_with_terms_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "DECLINED_BY_HUMAN",
            "rationale": "Từ chối nhưng lại đính kèm điều khoản.",
            "caseVersion": 1,
            "terms": {"amount": "1000"},
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_CREDIT_DECISION"
    assert repository.record_calls == 0


def test_stale_case_version_is_409_and_persists_nothing(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditDecisionRepository(current_version=2)
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_AS_PROPOSED",
            "rationale": "Phiên bản đã cũ.",
            "caseVersion": 1,
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_CASE_VERSION"
    assert body["details"]["expectedVersion"] == 2
    assert repository.record_calls == 0


def test_second_decision_same_version_is_200_created_false(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)
    payload = {
        "decision": "APPROVED_AS_PROPOSED",
        "rationale": "Phê duyệt theo đề xuất.",
        "caseVersion": 1,
    }
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    first = client.post(_url(), json=payload, headers=headers)
    second = client.post(_url(), json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_non_approver_is_403(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_AS_PROPOSED",
            "rationale": "Không đủ thẩm quyền.",
            "caseVersion": 1,
        },
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=['INTAKE_OFFICER'])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.record_calls == 0


def test_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_AS_PROPOSED",
            "rationale": "Không được phân công.",
            "caseVersion": 1,
        },
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.record_calls == 0


def test_invalid_decision_type_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_BY_AGENT",
            "rationale": "Nhãn quyết định không hợp lệ.",
            "caseVersion": 1,
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DECISION_TYPE"
    assert repository.record_calls == 0


def test_artifact_binding_mismatch_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository(latest_memo=uuid4())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_AS_PROPOSED",
            "rationale": "Tham chiếu gói hồ sơ sai phiên bản.",
            "caseVersion": 1,
            "memoArtifactId": str(uuid4()),
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_ARTIFACT_BINDING"
    assert body["details"]["field"] == "memoArtifactId"
    assert repository.record_calls == 0


def test_approved_as_proposed_without_terms_has_no_snapshot(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={
            "decision": "APPROVED_AS_PROPOSED",
            "rationale": "Phê duyệt theo đề xuất, không đóng băng điều khoản.",
            "caseVersion": 1,
        },
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    assert response.json()["approvedTerms"] is None


def test_participant_reads_recorded_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    client.post(
        _url(),
        json={
            "decision": "APPROVED_WITH_CONDITIONS",
            "rationale": "Phê duyệt có điều kiện.",
            "caseVersion": 1,
            "conditions": ["Bổ sung bảo đảm."],
            "terms": {"amount": "1000", "currency": "VND"},
        },
        headers=headers,
    )

    response = client.get(
        _url(),
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=['INTAKE_OFFICER'])}"
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVED_WITH_CONDITIONS"
    assert body["approvedTerms"]["terms"]["currency"] == "VND"


def test_get_is_404_when_no_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeCreditDecisionRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        _url(), headers={"Authorization": f"Bearer {token(signing_key)}"}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CREDIT_DECISION_NOT_AVAILABLE"
