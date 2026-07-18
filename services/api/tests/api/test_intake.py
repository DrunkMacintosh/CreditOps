"""Role-gated API tests for the intake completion + handoff endpoints.

All customer data, evidence, and handoffs here are synthetic and created solely
for demonstration.  The completeness verdict is the domain validator's alone;
these tests prove the endpoint honours it, is assignment-scoped, idempotent, and
re-ticks the orchestrator on a fresh handoff.
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
from creditops.api.intake import router as intake_router
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    OPS_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.intake import (
    CurrentHandoff,
    IntakeAuditEvent,
    IntakeEvidenceView,
    PersistedHandoff,
)
from creditops.application.ports.orchestration import (
    CreatedTask,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.enums import FactDisposition, TaskStatus
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    FactConfirmation,
    PageRegion,
)
from creditops.domain.handoffs import HandoffArtifact
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000004")
CASE_VERSION = 1
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
GRANTED_AT = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


def _bundle() -> tuple[CandidateFact, FactConfirmation, ConfirmedFact]:
    candidate = CandidateFact(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        document_version_id=uuid4(),
        field_key="requested_amount",
        proposed_value="5000000000",
        confidence=0.9,
        source=PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04),
    )
    confirmation = FactConfirmation(
        id=uuid4(),
        candidate_id=candidate.id,
        disposition=FactDisposition.ACCEPTED,
        authority=ConfirmationAuthority(
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            actor_id=OFFICER_A,
            assigned_officer_id=OFFICER_A,
            granted_at=GRANTED_AT,
            source="CASE_ASSIGNMENT",
        ),
        confirmed_at=GRANTED_AT + timedelta(minutes=5),
    )
    fact = ConfirmedFact.from_confirmation(
        id=uuid4(), candidate=candidate, confirmation=confirmation
    )
    return candidate, confirmation, fact


def _complete_view() -> IntakeEvidenceView:
    candidate, confirmation, fact = _bundle()
    return IntakeEvidenceView(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        candidates=(candidate,),
        confirmations=(confirmation,),
        confirmed_facts=(fact,),
    )


def _incomplete_view() -> IntakeEvidenceView:
    candidate, _, _ = _bundle()
    return IntakeEvidenceView(
        case_id=CASE_ID, case_version=CASE_VERSION, candidates=(candidate,)
    )


class FakeIntakeRepository:
    def __init__(
        self, *, view: IntakeEvidenceView, current: CurrentHandoff | None = None
    ) -> None:
        self._view = view
        self.current = current
        self.persisted: list[HandoffArtifact] = []
        self.audit_events: list[IntakeAuditEvent] = []

    async def load_intake_evidence(
        self, case_id: UUID, case_version: int
    ) -> IntakeEvidenceView:
        return self._view

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None:
        return self.current

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool:
        return self.current is not None

    async def persist_handoff(
        self, handoff: HandoffArtifact, *, actor_id: UUID
    ) -> PersistedHandoff:
        if self.current is not None:
            return PersistedHandoff(handoff_id=self.current.id, created=False)
        self.persisted.append(handoff)
        self.current = CurrentHandoff(
            id=handoff.id,
            case_id=handoff.case_id,
            case_version=handoff.case_version,
            state=handoff.state,
            created_at=NOW,
        )
        return PersistedHandoff(handoff_id=handoff.id, created=True)

    async def append_audit(self, event: IntakeAuditEvent) -> None:
        self.audit_events.append(event)


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.created_tasks: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id, case_version=CASE_VERSION, has_intake_handoff=True
        )

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

    async def append_audit(self, event: object) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]:
        return tuple(e for e in self.outbox if e.dispatched_at is None)[:limit]

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
            version=CASE_VERSION,
            assigned_officer_id=OFFICER_A,
            requested_amount="5000000000",
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
    repository: FakeIntakeRepository,
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
    # The lead wires this router into main.py; the test includes it directly so
    # it exercises the real router without touching the composition root.
    application.include_router(intake_router)
    application.state.intake_repository = repository
    application.state.orchestration_repository = orchestration_repository
    application.state.agent_task_queue = agent_queue
    application.state.worker_dispatcher = None
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
            "roles": roles or [INTAKE_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def test_intake_officer_completes_intake_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/intake-completion",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["state"] == "READY_FOR_SPECIALIST_REVIEW"
    assert body["caseVersion"] == CASE_VERSION
    handoff_id = body["handoffId"]
    assert len(repository.persisted) == 1
    assert len(repository.audit_events) == 1
    # A fresh ORCHESTRATOR_PLAN task keyed on the handoff, and its envelope
    # dispatched to the queue.
    plan_tasks = [
        call
        for call in orchestration.created_tasks
        if call["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert f"HANDOFF:{handoff_id}" in str(plan_tasks[0]["idempotency_key"])
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN


def test_incomplete_evidence_returns_409_with_reasons_and_persists_nothing(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_incomplete_view())
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/intake-completion",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "INTAKE_INCOMPLETE"
    reasons = body["details"]["reasons"]
    assert reasons
    assert any("missing confirmation" in reason for reason in reasons)
    # Nothing persisted, nothing scheduled.
    assert repository.persisted == []
    assert repository.audit_events == []
    assert orchestration.created_tasks == []
    assert queue.sent == []


def test_repeat_completion_is_idempotent_created_false(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    headers = {"Authorization": f"Bearer {token(signing_key)}"}
    url = f"/api/v1/cases/{CASE_ID}/intake-completion"

    first = client.post(url, headers=headers)
    second = client.post(url, headers=headers)

    assert first.status_code == 201
    assert first.json()["created"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["handoffId"] == first.json()["handoffId"]
    # Exactly one handoff, one audit event, one plan task.
    assert len(repository.persisted) == 1
    assert len(repository.audit_events) == 1
    plan_tasks = [
        call
        for call in orchestration.created_tasks
        if call["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1


def test_non_intake_role_is_forbidden(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/intake-completion",
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.persisted == []


def test_unassigned_actor_gets_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/intake-completion",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.persisted == []


def test_participant_reads_current_handoff(signing_key: rsa.RSAPrivateKey) -> None:
    handoff_id = uuid4()
    current = CurrentHandoff(
        id=handoff_id,
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        state="READY_FOR_SPECIALIST_REVIEW",
        created_at=NOW,
    )
    repository = FakeIntakeRepository(view=_complete_view(), current=current)
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/handoffs",
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["handoffId"] == str(handoff_id)
    assert body["state"] == "READY_FOR_SPECIALIST_REVIEW"
    assert body["caseVersion"] == CASE_VERSION
    assert "createdAt" in body


def test_get_handoff_is_404_before_intake_completes(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/handoffs",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "HANDOFF_NOT_AVAILABLE"


def test_get_handoff_rejects_non_participant_role(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/handoffs",
        headers={"Authorization": f"Bearer {token(signing_key, roles=['AUDITOR'])}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_get_handoff_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/handoffs",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
