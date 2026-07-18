"""Role-gated API tests for the pre-Risk gap-request workflow (the G2 gate).

Covers: idempotent assemble-or-get; a NO_OUTBOUND_REQUESTS disposition on an
empty batch satisfies G2 and self-reticks the orchestrator (ensure_gate + plan
task + queue send); REJECTED does not satisfy; a stale batch (open gaps changed
since assembly) does not satisfy and reports a staleness indicator; non-intake
role is 403; an unassigned actor gets an indistinguishable 404.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
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
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.gap_requests import (
    GapRequestBatchDispositionRecord,
    OpenGap,
    PersistedGapRequestBatch,
)
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
from creditops.domain.gap_request_batches import BatchDispositionType, GapRequestBatch
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.domain.underwriting import GapBlockingLevel
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000a1")
GAP_A = UUID("61000000-0000-0000-0000-0000000000a1")
GAP_B = UUID("61000000-0000-0000-0000-0000000000a2")
NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


def _open_gap(gap_id: UUID, *, status: str = "FORMAL") -> OpenGap:
    return OpenGap(
        gap_id=gap_id,
        status=status,
        blocking_level=GapBlockingLevel.CONDITIONAL,
        missing_information_vi="Thiếu báo cáo tài chính (mô phỏng).",
        suggested_evidence_vi=("Báo cáo tài chính năm gần nhất.",),
    )


class FakeGapRequestRepository:
    def __init__(self, *, open_gaps: tuple[OpenGap, ...] = ()) -> None:
        self._open_gaps: list[OpenGap] = list(open_gaps)
        self._batches: dict[tuple[UUID, int, str], GapRequestBatch] = {}
        self._order: list[tuple[UUID, int, str]] = []
        self.dispositions: list[GapRequestBatchDispositionRecord] = []

    def set_open_gaps(self, gaps: tuple[OpenGap, ...]) -> None:
        self._open_gaps = list(gaps)

    async def load_open_gaps(self, case_id: UUID, case_version: int) -> tuple[OpenGap, ...]:
        return tuple(self._open_gaps)

    async def load_current_batch(
        self, case_id: UUID, case_version: int
    ) -> GapRequestBatch | None:
        for key in reversed(self._order):
            cid, cver, _ = key
            if cid == case_id and cver == case_version:
                return self._batches[key]
        return None

    async def persist_batch(self, batch: GapRequestBatch) -> PersistedGapRequestBatch:
        key = (batch.case_id, batch.case_version, batch.open_gap_snapshot_hash)
        if key in self._batches:
            return PersistedGapRequestBatch(batch=self._batches[key], created=False)
        self._batches[key] = batch
        self._order.append(key)
        return PersistedGapRequestBatch(batch=batch, created=True)

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        batch_id: UUID,
        case_id: UUID,
        case_version: int,
        disposition_type: BatchDispositionType,
        item_dispositions: Any,
        edited_texts: Any,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> GapRequestBatchDispositionRecord:
        record = GapRequestBatchDispositionRecord(
            id=disposition_id,
            batch_id=batch_id,
            disposition_type=disposition_type,
            item_dispositions=dict(item_dispositions),
            edited_texts=dict(edited_texts),
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=NOW,
        )
        self.dispositions.append(record)
        return record

    async def load_dispositions(
        self, batch_id: UUID
    ) -> tuple[GapRequestBatchDispositionRecord, ...]:
        return tuple(d for d in self.dispositions if d.batch_id == batch_id)


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.ensure_gate_calls: list[dict[str, Any]] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> Any:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id, case_version=1, has_intake_handoff=True
        )

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        return GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
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

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the gap-request API")

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
    repository: FakeGapRequestRepository,
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
    application.state.gap_request_repository = repository
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
            "roles": roles or [INTAKE_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


_BASE = f"/api/v1/cases/{CASE_ID}/gap-request-batches"


# -- assemble -----------------------------------------------------------------


def test_assemble_is_idempotent(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeGapRequestRepository(open_gaps=(_open_gap(GAP_A), _open_gap(GAP_B)))
    client = _build_client(signing_key, repository=repository)
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    first = client.post(_BASE, headers=headers)
    assert first.status_code == 201
    body = first.json()
    assert len(body["items"]) == 2
    assert len(body["openGapSnapshotHash"]) == 64
    first_batch_id = body["batchId"]

    # Re-assembling the same open-gap set returns the existing batch (200).
    second = client.post(_BASE, headers=headers)
    assert second.status_code == 200
    assert second.json()["batchId"] == first_batch_id
    assert second.json()["openGapSnapshotHash"] == body["openGapSnapshotHash"]


def test_assemble_rejects_non_intake_role(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeGapRequestRepository())
    response = client.post(
        _BASE,
        headers={"Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_assemble_unassigned_actor_gets_404(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeGapRequestRepository())
    response = client.post(
        _BASE,
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


# -- disposition --------------------------------------------------------------


def test_no_outbound_requests_on_empty_batch_satisfies_g2_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # No open gaps -> an EMPTY batch.  It is NOT vacuously satisfied: an
    # explicit NO_OUTBOUND_REQUESTS disposition is required, and only then does
    # G2 satisfy and the orchestrator re-tick.
    repository = FakeGapRequestRepository(open_gaps=())
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    assembled = client.post(_BASE, headers=headers)
    assert assembled.status_code == 201
    batch_id = assembled.json()["batchId"]
    assert assembled.json()["items"] == []

    disposition = client.post(
        f"{_BASE}/{batch_id}/disposition",
        json={"dispositionType": "NO_OUTBOUND_REQUESTS", "rationale": "Không cần gửi yêu cầu."},
        headers=headers,
    )
    assert disposition.status_code == 201
    assert disposition.json()["gateStatus"] == "SATISFIED"
    assert disposition.json()["stale"] is False

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.G2_GAP_REQUEST_APPROVAL
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A
    # Retick: an ORCHESTRATOR_PLAN task is created, outboxed, and dispatched.
    plan_tasks = [
        c for c in orchestration.created_tasks if c["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert str(batch_id) in str(plan_tasks[0]["idempotency_key"])
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN


def test_rejected_disposition_does_not_satisfy_g2(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeGapRequestRepository(open_gaps=(_open_gap(GAP_A),))
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    batch_id = client.post(_BASE, headers=headers).json()["batchId"]
    disposition = client.post(
        f"{_BASE}/{batch_id}/disposition",
        json={"dispositionType": "REJECTED", "rationale": "Không phê duyệt gửi yêu cầu."},
        headers=headers,
    )
    assert disposition.status_code == 201
    assert disposition.json()["gateStatus"] == "OPEN"
    assert orchestration.ensure_gate_calls == []
    assert queue.sent == []
    assert len(repository.dispositions) == 1


def test_stale_batch_does_not_satisfy_and_reports_staleness(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # Assemble against {GAP_A}; then the open-gap set changes ({GAP_A, GAP_B})
    # before the human disposes.  The batch is now stale: APPROVED_ALL must NOT
    # satisfy G2, and the response flags staleness.
    repository = FakeGapRequestRepository(open_gaps=(_open_gap(GAP_A),))
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    headers = {"Authorization": f"Bearer {token(signing_key)}"}

    batch_id = client.post(_BASE, headers=headers).json()["batchId"]
    repository.set_open_gaps((_open_gap(GAP_A), _open_gap(GAP_B)))  # a new gap appeared

    disposition = client.post(
        f"{_BASE}/{batch_id}/disposition",
        json={"dispositionType": "APPROVED_ALL", "rationale": "Phê duyệt tất cả."},
        headers=headers,
    )
    assert disposition.status_code == 201
    assert disposition.json()["stale"] is True
    assert disposition.json()["gateStatus"] == "OPEN"
    assert orchestration.ensure_gate_calls == []


def test_disposition_rejects_non_intake_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeGapRequestRepository(open_gaps=())
    client = _build_client(signing_key, repository=repository)
    response = client.post(
        f"{_BASE}/{uuid4()}/disposition",
        json={"dispositionType": "NO_OUTBOUND_REQUESTS", "rationale": "không được phép"},
        headers={"Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.dispositions == []


def test_disposition_on_unknown_batch_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeGapRequestRepository(open_gaps=(_open_gap(GAP_A),))
    client = _build_client(signing_key, repository=repository)
    headers = {"Authorization": f"Bearer {token(signing_key)}"}
    client.post(_BASE, headers=headers)

    response = client.post(
        f"{_BASE}/{uuid4()}/disposition",
        json={"dispositionType": "REJECTED", "rationale": "không tồn tại"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "GAP_REQUEST_BATCH_NOT_FOUND"


def test_invalid_disposition_shape_is_422_with_details(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    # NO_OUTBOUND_REQUESTS is invalid for a NON-empty batch.
    repository = FakeGapRequestRepository(open_gaps=(_open_gap(GAP_A),))
    client = _build_client(signing_key, repository=repository)
    headers = {"Authorization": f"Bearer {token(signing_key)}"}
    batch_id = client.post(_BASE, headers=headers).json()["batchId"]

    response = client.post(
        f"{_BASE}/{batch_id}/disposition",
        json={"dispositionType": "NO_OUTBOUND_REQUESTS", "rationale": "sai hình dạng"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DISPOSITION"
    assert "details" in response.json()


# -- read ---------------------------------------------------------------------


def test_get_reports_batch_dispositions_and_gate_status(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeGapRequestRepository(open_gaps=())
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    headers = {"Authorization": f"Bearer {token(signing_key)}"}
    batch_id = client.post(_BASE, headers=headers).json()["batchId"]
    client.post(
        f"{_BASE}/{batch_id}/disposition",
        json={"dispositionType": "NO_OUTBOUND_REQUESTS", "rationale": "Không có gì để gửi."},
        headers=headers,
    )

    status = client.get(_BASE, headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["batch"]["batchId"] == batch_id
    assert body["stale"] is False
    assert body["gateStatus"] == "SATISFIED"
    assert len(body["dispositions"]) == 1
    assert body["dispositions"][0]["dispositionType"] == "NO_OUTBOUND_REQUESTS"


def test_get_is_404_before_any_batch(signing_key: rsa.RSAPrivateKey) -> None:
    client = _build_client(signing_key, repository=FakeGapRequestRepository())
    response = client.get(_BASE, headers={"Authorization": f"Bearer {token(signing_key)}"})
    assert response.status_code == 404
    assert response.json()["code"] == "GAP_REQUEST_BATCH_NOT_AVAILABLE"
