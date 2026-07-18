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
from creditops.application.orchestration.roles import RISK_REVIEWER_ROLE
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationAuditEvent,
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
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.tasks_by_key: dict[str, OrchestrationTaskRow] = {}
        self.gates: dict[GateType, GateRecord] = {}
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.proposals: list[dict[str, object]] = []
        self.outbox: list[OutboxEventRow] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=1,
            has_intake_handoff=True,
            tasks=tuple(self.tasks_by_key.values()),
            gates=tuple(self.gates.values()),
        )

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        gate_type: GateType = kwargs["gate_type"]
        record = self.gates.get(gate_type) or GateRecord(gate_type, 1, GateStatus.OPEN)
        self.gates[gate_type] = record
        return record

    async def create_task(self, **kwargs: Any) -> CreatedTask:
        key = str(kwargs["idempotency_key"])
        existing = self.tasks_by_key.get(key)
        if existing is not None:
            return CreatedTask(row=existing, created=False)
        row = OrchestrationTaskRow(
            task_id=kwargs["task_id"],
            task_type=kwargs["task_type"],
            case_version=int(kwargs["case_version"]),
            status=TaskStatus.PENDING,
        )
        self.tasks_by_key[key] = row
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
        return CreatedTask(row=row, created=True)

    async def record_proposal(self, **kwargs: object) -> None:
        self.proposals.append(dict(kwargs))

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]:
        return tuple(
            event for event in self.outbox if event.dispatched_at is None
        )[:limit]

    async def mark_outbox_dispatched(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(
                    event, dispatched_at=datetime.now(UTC)
                )

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = replace(
                    event, dispatch_attempts=event.dispatch_attempts + 1
                )


class RecordingQueue:
    def __init__(self) -> None:
        self.sent: list[TaskEnvelopeV1] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del delay_seconds
        self.sent.append(envelope)
        return len(self.sent)

    async def read_one(self, *, visibility_timeout_seconds: int) -> None:
        del visibility_timeout_seconds
        return None

    async def extend_visibility(self, message_id: int, *, visibility_timeout_seconds: int) -> None:
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
            created_at=datetime.now(UTC),
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
def orchestration_repository() -> FakeOrchestrationRepository:
    return FakeOrchestrationRepository()


@pytest.fixture
def agent_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def client(
    signing_key: rsa.RSAPrivateKey,
    orchestration_repository: FakeOrchestrationRepository,
    agent_queue: RecordingQueue,
) -> TestClient:
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
    application.state.orchestration_repository = orchestration_repository
    application.state.agent_task_queue = agent_queue
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


def auth(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}", "X-Request-ID": "request-orch"}


def test_non_participant_roles_cannot_reach_orchestration(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    headers = auth(token(signing_key, roles=["AUDITOR"]))

    read = client.get(f"/api/v1/cases/{CASE_ID}/orchestration", headers=headers)
    advance = client.post(
        f"/api/v1/cases/{CASE_ID}/orchestration/advance", headers=headers
    )

    assert read.status_code == advance.status_code == 403
    assert read.json()["code"] == advance.json()["code"] == "INSUFFICIENT_ROLE"


def test_unassigned_actor_cannot_distinguish_case_from_missing(
    client: TestClient, signing_key: rsa.RSAPrivateKey
) -> None:
    headers = auth(token(signing_key, subject=uuid4(), roles=[RISK_REVIEWER_ROLE]))

    known = client.get(f"/api/v1/cases/{CASE_ID}/orchestration", headers=headers)
    missing = client.get(f"/api/v1/cases/{uuid4()}/orchestration", headers=headers)

    assert known.status_code == missing.status_code == 404
    assert known.json()["code"] == missing.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_status_reports_plan_tasks_gates_and_no_capability_leak(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    orchestration_repository: FakeOrchestrationRepository,
) -> None:
    orchestration_repository.tasks_by_key["seed"] = OrchestrationTaskRow(
        uuid4(), TaskType.CREDIT_UNDERWRITING, 1, TaskStatus.RUNNING
    )

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/orchestration",
        headers=auth(token(signing_key)),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["caseId"] == str(CASE_ID)
    assert body["hasIntakeHandoff"] is True
    assert body["planSource"] == "DEFAULT"
    gate_status = {gate["gateType"]: gate["status"] for gate in body["gates"]}
    assert gate_status["G1_INTAKE_COMPLETE"] == "SATISFIED"
    assert gate_status["G3_RISK_DISPOSITION"] == "OPEN"
    readiness = {entry["taskType"]: entry["readiness"] for entry in body["readiness"]}
    assert readiness["CREDIT_UNDERWRITING"] == "IN_PROGRESS"
    assert readiness["INDEPENDENT_RISK_REVIEW"] == "BLOCKED"
    assert body["deadlock"] is None
    # The neutral status view must not leak an action capability (e.g. an
    # "approve"/"reject" affordance).  Gate TYPE names are recorded human state,
    # not capabilities, so they are excluded from the scan -- a gate such as
    # HG_CREDIT_NOTIFICATION_APPROVED legitimately ends in "_APPROVED".
    scannable = response.text.lower()
    for gate_type in gate_status:
        scannable = scannable.replace(gate_type.lower(), "")
    assert "approve" not in scannable


def test_advance_is_a_202_idempotent_kickoff(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    agent_queue: RecordingQueue,
) -> None:
    headers = auth(token(signing_key))

    first = client.post(f"/api/v1/cases/{CASE_ID}/orchestration/advance", headers=headers)
    second = client.post(f"/api/v1/cases/{CASE_ID}/orchestration/advance", headers=headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["taskId"] == second.json()["taskId"]
    assert len(agent_queue.sent) == 1
    assert agent_queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN
