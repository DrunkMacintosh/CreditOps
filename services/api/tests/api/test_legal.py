"""Role-gated read-only API tests for GET /cases/{id}/legal.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture
assessment belongs to the invented SME "Cong ty TNHH Kho Van An Binh Demo".
"""

from __future__ import annotations

from dataclasses import replace
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
from creditops.api.legal import LEGAL_REVIEWER_ROLE
from creditops.application.orchestration.roles import RISK_REVIEWER_ROLE
from creditops.application.ports.legal import LatestLegalAssessmentRecord
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000002")
ASSESSMENT_ID = UUID("50000000-0000-0000-0000-000000000002")
HANDOFF_ID = UUID("60000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeLegalReadRepository:
    def __init__(self, *, has_assessment: bool = True) -> None:
        self.has_assessment = has_assessment
        self.audit_events: list[Any] = []

    async def load_latest_assessment(
        self, case_id: UUID
    ) -> LatestLegalAssessmentRecord | None:
        if case_id != CASE_ID or not self.has_assessment:
            return None
        return LatestLegalAssessmentRecord(
            assessment_id=ASSESSMENT_ID,
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            agent_role="LEGAL_COMPLIANCE_COLLATERAL",
            prompt_version="legal-prompt-v1",
            created_at=NOW,
            assessment={
                "legal_entity_review": {"findings": []},
                "policy_review": [],
                "exceptions": [],
            },
            handoff_id=HANDOFF_ID,
            handoff_state="READY_FOR_RISK_REVIEW",
            handoff_created_at=NOW,
        )

    async def append_audit(self, event: Any) -> None:
        self.audit_events.append(event)


class FakeOrchestrationRepository:
    """Copied from tests/api/test_risk_review.py (outbox + queue), extended so
    ``load_snapshot`` reflects the gates written via ``ensure_gate``."""

    def __init__(self) -> None:
        self.ensure_gate_calls: list[dict[str, Any]] = []
        self.gates: list[GateRecord] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> Any:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=1,
            has_intake_handoff=True,
            gates=tuple(self.gates),
        )

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        record = GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
            satisfied_by_actor_id=kwargs.get("satisfied_by_actor_id"),
            disposition_ref=kwargs.get("disposition_ref"),
        )
        for existing in self.gates:
            if (
                existing.gate_type == record.gate_type
                and existing.case_version == record.case_version
            ):
                return existing
        self.gates.append(record)
        return record

    async def create_task(self, **kwargs: Any) -> CreatedTask:
        for existing in self.created_tasks:
            if existing["idempotency_key"] == kwargs["idempotency_key"]:
                return CreatedTask(
                    row=OrchestrationTaskRow(
                        task_id=existing["task_id"],
                        task_type=existing["task_type"],
                        case_version=int(existing["case_version"]),
                        status=TaskStatus.PENDING,
                    ),
                    created=False,
                )
        self.created_tasks.append(dict(kwargs))
        envelope = TaskEnvelopeV1(
            task_id=kwargs["task_id"],
            case_id=kwargs["case_id"],
            case_version=int(kwargs["case_version"]),
            task_type=kwargs["task_type"],
            document_version_id=None,
        )
        self.outbox.append(
            OutboxEventRow(
                event_id=uuid4(),
                case_id=kwargs["case_id"],
                case_version=int(kwargs["case_version"]),
                event_type="TASK_READY",
                payload=envelope.model_dump(mode="json"),
            )
        )
        return CreatedTask(
            row=OrchestrationTaskRow(
                task_id=kwargs["task_id"],
                task_type=kwargs["task_type"],
                case_version=int(kwargs["case_version"]),
                status=TaskStatus.PENDING,
            ),
            created=True,
        )

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the legal API")

    async def append_audit(self, event: object) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]:
        return tuple(event for event in self.outbox if event.dispatched_at is None)[:limit]

    async def mark_outbox_dispatched(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(event, dispatched_at=NOW)

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(
                    event, dispatch_attempts=event.dispatch_attempts + 1
                )


class RecordingAgentQueue:
    def __init__(self) -> None:
        self.sent: list[TaskEnvelopeV1] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del delay_seconds
        self.sent.append(envelope)
        return len(self.sent)

    async def read_one(self, *, visibility_timeout_seconds: int) -> None:
        del visibility_timeout_seconds
        return None

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        del message_id


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
    application.state.legal_repository = FakeLegalReadRepository()
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
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assessmentId"] == str(ASSESSMENT_ID)
    assert body["agentRole"] == "LEGAL_COMPLIANCE_COLLATERAL"
    assert body["caseVersion"] == 1
    assert body["handoff"]["state"] == "READY_FOR_RISK_REVIEW"
    assert body["handoff"]["handoffId"] == str(HANDOFF_ID)
    # No decision/determination-capable field leaks through the read model.
    lowered = {key.lower() for key in body}
    assert not lowered & {
        "decision",
        "approved",
        "score",
        "waiver",
        "legalconclusion",
        "wrongdoing",
        "collateralvalue",
    }


def test_risk_reviewer_role_may_read(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
        },
    )
    assert response.status_code == 200


def test_non_participant_role_is_rejected(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['AUDITOR'])}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_unassigned_actor_gets_indistinguishable_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_missing_assessment_is_404(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    client.app.state.legal_repository = FakeLegalReadRepository(has_assessment=False)
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "LEGAL_ASSESSMENT_NOT_AVAILABLE"


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(f"/api/v1/cases/{CASE_ID}/legal")
    assert response.status_code in (401, 403)


def test_service_unavailable_when_repository_not_configured(
    signing_key: rsa.RSAPrivateKey,
) -> None:
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
    application.state.legal_repository = None
    client = TestClient(application)
    response = client.get(
        f"/api/v1/cases/{CASE_ID}/legal",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "LEGAL_SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Stage-4 human gate write: POST /review
# ---------------------------------------------------------------------------


def _build_write_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeLegalReadRepository,
    orchestration_repository: FakeOrchestrationRepository | None = None,
    agent_queue: RecordingAgentQueue | None = None,
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
    application.state.legal_repository = repository
    application.state.orchestration_repository = orchestration_repository
    application.state.agent_task_queue = agent_queue
    return TestClient(application)


def test_legal_reviewer_review_satisfies_gate_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeLegalReadRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_write_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/legal/review",
        json={"assessmentId": str(ASSESSMENT_ID), "rationale": "Da ra soat phap ly."},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[LEGAL_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == GateType.HG_LEGAL_ASSESSMENT_REVIEWED.value
    assert body["status"] == GateStatus.SATISFIED.value
    assert body["assessmentId"] == str(ASSESSMENT_ID)
    assert body["dispositionRef"] == f"legal-assessment:{ASSESSMENT_ID}"

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_LEGAL_ASSESSMENT_REVIEWED
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A
    assert call["disposition_ref"] == f"legal-assessment:{ASSESSMENT_ID}"
    plan_tasks = [
        t for t in orchestration.created_tasks if t["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN
    assert any(
        getattr(e, "event_type", None) == "LEGAL_ASSESSMENT_REVIEWED"
        for e in repository.audit_events
    )


def test_review_of_stale_assessment_is_409_and_writes_no_gate(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeLegalReadRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_write_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/legal/review",
        json={"assessmentId": str(uuid4()), "rationale": "Ban khong con moi nhat."},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[LEGAL_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STALE_ASSESSMENT"
    assert orchestration.ensure_gate_calls == []
    assert repository.audit_events == []


def test_review_with_no_assessment_yet_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeLegalReadRepository(has_assessment=False)
    orchestration = FakeOrchestrationRepository()
    client = _build_write_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/legal/review",
        json={"assessmentId": str(ASSESSMENT_ID), "rationale": "Chua co ban danh gia."},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[LEGAL_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "LEGAL_ASSESSMENT_NOT_AVAILABLE"
    assert orchestration.ensure_gate_calls == []


def test_review_rejects_non_legal_reviewer_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeLegalReadRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_write_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/legal/review",
        json={"assessmentId": str(ASSESSMENT_ID), "rationale": "khong duoc phep"},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert orchestration.ensure_gate_calls == []


def test_review_by_unassigned_reviewer_is_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeLegalReadRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_write_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/legal/review",
        json={"assessmentId": str(ASSESSMENT_ID), "rationale": "khong duoc gan ho so"},
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=uuid4(), roles=[LEGAL_REVIEWER_ROLE])}"
            )
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert orchestration.ensure_gate_calls == []
