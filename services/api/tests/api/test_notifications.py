"""Role-gated API tests for the stage-7 credit notification lifecycle.

POST/GET /cases/{id}/notifications: deterministic draft (only from a permitting
decision), the HG_CREDIT_NOTIFICATION_APPROVED human gate write, and the LABELLED
MOCK delivery with separation-of-actor enforcement.  The router is mounted onto
the app built by ``create_app`` here (its ``main.py`` wiring is deferred), and the
repositories are injected directly onto ``app.state``.

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
from creditops.api.notifications import router as notifications_router
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.notifications import (
    DecisionDoesNotPermitNotificationError,
    GateNotSatisfiedError,
    RecordedCommunicationReceipt,
    RecordedNotificationDraft,
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
from creditops.domain.notifications import (
    MOCK_DELIVERY_CHANNEL,
    NOT_A_DISBURSEMENT_NOTICE_VI,
    compute_content_hash,
)
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")  # maker / draft creator
OFFICER_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")  # checker / deliverer
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f7")
DECISION_ID = UUID("d0000000-0000-0000-0000-0000000000f7")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

_DRAFT_CONTENT_VI = (
    "THÔNG BÁO TÍN DỤNG (DỮ LIỆU MÔ PHỎNG)\n\n"
    f"{NOT_A_DISBURSEMENT_NOTICE_VI}\n\n{SYNTHETIC_NOTICE_VI}"
)


class FakeNotificationRepository:
    def __init__(self, *, permits: bool = True) -> None:
        self.permits = permits
        self.drafts: dict[UUID, RecordedNotificationDraft] = {}
        self.receipts: dict[UUID, RecordedCommunicationReceipt] = {}
        self.create_calls = 0
        self.delivery_calls = 0

    async def create_draft(
        self, *, draft_id: UUID, case_id: UUID, created_by: UUID
    ) -> RecordedNotificationDraft:
        self.create_calls += 1
        if not self.permits:
            raise DecisionDoesNotPermitNotificationError("no permitting decision")
        existing = self.drafts.get(case_id)
        if existing is not None:
            return replace(existing, created=False)
        draft = RecordedNotificationDraft(
            id=draft_id,
            case_id=case_id,
            case_version=1,
            decision_id=DECISION_ID,
            content_vi=_DRAFT_CONTENT_VI,
            content_hash=compute_content_hash(_DRAFT_CONTENT_VI),
            created_by=created_by,
            created_at=NOW,
            created=True,
        )
        self.drafts[case_id] = draft
        return draft

    async def load_draft(self, case_id: UUID) -> RecordedNotificationDraft | None:
        draft = self.drafts.get(case_id)
        return replace(draft, created=False) if draft is not None else None

    async def load_receipt(
        self, draft_id: UUID
    ) -> RecordedCommunicationReceipt | None:
        return self.receipts.get(draft_id)

    async def record_mock_delivery(
        self,
        *,
        receipt_id: UUID,
        draft_id: UUID,
        content_hash: str,
        receipt_note_vi: str | None,
        recorded_by: UUID,
        gate_satisfied: bool,
    ) -> RecordedCommunicationReceipt:
        self.delivery_calls += 1
        if not gate_satisfied:
            raise GateNotSatisfiedError("gate not satisfied")
        existing = self.receipts.get(draft_id)
        if existing is not None:
            return existing
        receipt = RecordedCommunicationReceipt(
            id=receipt_id,
            draft_id=draft_id,
            delivered_via=MOCK_DELIVERY_CHANNEL,
            content_hash=content_hash,
            receipt_note_vi=receipt_note_vi,
            recorded_by=recorded_by,
            created_at=NOW,
        )
        self.receipts[draft_id] = receipt
        return receipt


class FakeOrchestrationRepository:
    """Copied from tests/api/test_underwriting.py (outbox + queue), with
    ``load_snapshot`` reflecting the gates written via ``ensure_gate`` so the
    deliver gate precondition reads them back."""

    def __init__(self) -> None:
        self.ensure_gate_calls: list[dict[str, object]] = []
        self.gates: list[GateRecord] = []
        self.created_tasks: list[dict[str, object]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[object] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=1,
            has_intake_handoff=True,
            gates=tuple(self.gates),
        )

    async def ensure_gate(self, **kwargs: object) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        record = GateRecord(
            gate_type=kwargs["gate_type"],  # type: ignore[arg-type]
            case_version=kwargs["case_version"],  # type: ignore[arg-type]
            status=kwargs["status"],  # type: ignore[arg-type]
            satisfied_by_actor_id=kwargs.get("satisfied_by_actor_id"),  # type: ignore[arg-type]
            disposition_ref=kwargs.get("disposition_ref"),  # type: ignore[arg-type]
        )
        for existing in self.gates:
            if (
                existing.gate_type == record.gate_type
                and existing.case_version == record.case_version
            ):
                return existing
        self.gates.append(record)
        return record

    async def create_task(self, **kwargs: object) -> CreatedTask:
        for existing in self.created_tasks:
            if existing["idempotency_key"] == kwargs["idempotency_key"]:
                return CreatedTask(
                    row=OrchestrationTaskRow(
                        task_id=existing["task_id"],  # type: ignore[arg-type]
                        task_type=existing["task_type"],  # type: ignore[arg-type]
                        case_version=int(existing["case_version"]),  # type: ignore[call-overload]
                        status=TaskStatus.PENDING,
                    ),
                    created=False,
                )
        self.created_tasks.append(dict(kwargs))
        envelope = TaskEnvelopeV1(
            task_id=kwargs["task_id"],  # type: ignore[arg-type]
            case_id=kwargs["case_id"],  # type: ignore[arg-type]
            case_version=int(kwargs["case_version"]),  # type: ignore[call-overload]
            task_type=kwargs["task_type"],  # type: ignore[arg-type]
            document_version_id=None,
        )
        self.outbox.append(
            OutboxEventRow(
                event_id=uuid4(),
                case_id=kwargs["case_id"],  # type: ignore[arg-type]
                case_version=int(kwargs["case_version"]),  # type: ignore[call-overload]
                event_type="TASK_READY",
                payload=envelope.model_dump(mode="json"),
            )
        )
        return CreatedTask(
            row=OrchestrationTaskRow(
                task_id=kwargs["task_id"],  # type: ignore[arg-type]
                task_type=kwargs["task_type"],  # type: ignore[arg-type]
                case_version=int(kwargs["case_version"]),  # type: ignore[call-overload]
                status=TaskStatus.PENDING,
            ),
            created=True,
        )

    async def record_proposal(self, **kwargs: object) -> None:
        raise AssertionError("not used by the notification API")

    async def append_audit(self, event: object) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(
        self, *, limit: int
    ) -> tuple[OutboxEventRow, ...]:
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
        if case_id != CASE_ID or actor_id not in {OFFICER_A, OFFICER_B}:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=actor_id,
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
    repository: FakeNotificationRepository,
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
    application.include_router(notifications_router)
    application.state.notification_repository = repository
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
            "roles": roles or [OPS_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _url(suffix: str = "") -> str:
    return f"/api/v1/cases/{CASE_ID}/notifications{suffix}"


def _auth(signing_key: rsa.RSAPrivateKey, **kwargs: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, **kwargs)}"}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Draft creation
# ---------------------------------------------------------------------------


def test_create_draft_requires_permitting_decision(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeNotificationRepository(permits=False)
    client = _build_client(signing_key, repository=repository)

    response = client.post(_url(), headers=_auth(signing_key))

    assert response.status_code == 409
    assert response.json()["code"] == "DECISION_DOES_NOT_PERMIT_NOTIFICATION"
    assert repository.drafts == {}


def test_create_draft_is_idempotent(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    client = _build_client(signing_key, repository=repository)

    first = client.post(_url(), headers=_auth(signing_key))
    second = client.post(_url(), headers=_auth(signing_key))

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    body = first.json()
    assert body["decisionId"] == str(DECISION_ID)
    assert len(body["contentHash"]) == 64
    # The mandatory disclaimer + synthetic notice are part of the content.
    assert NOT_A_DISBURSEMENT_NOTICE_VI in body["content"]
    assert SYNTHETIC_NOTICE_VI in body["content"]


def test_create_draft_rejects_non_ops_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(), headers=_auth(signing_key, roles=[INTAKE_OFFICER_ROLE])
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.create_calls == 0


def test_create_draft_by_unassigned_actor_is_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeNotificationRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(_url(), headers=_auth(signing_key, subject=uuid4()))

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.create_calls == 0


# ---------------------------------------------------------------------------
# Approval gate write
# ---------------------------------------------------------------------------


def test_approve_satisfies_gate_and_reticks(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    created = client.post(_url(), headers=_auth(signing_key)).json()

    response = client.post(
        _url("/approve"),
        json={"draftId": created["id"], "rationale": "Đã phê duyệt thông báo."},
        headers=_auth(signing_key),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == GateType.HG_CREDIT_NOTIFICATION_APPROVED.value
    assert body["status"] == GateStatus.SATISFIED.value
    assert body["dispositionRef"] == f"notification-draft:{created['id']}"

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_CREDIT_NOTIFICATION_APPROVED
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A
    assert call["disposition_ref"] == f"notification-draft:{created['id']}"
    # Retick created + dispatched a fresh ORCHESTRATOR_PLAN task.
    plan_tasks = [
        t
        for t in orchestration.created_tasks
        if t["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN
    assert any(
        getattr(e, "event_type", None) == "CREDIT_NOTIFICATION_APPROVED"
        for e in orchestration.audit_events
    )


def test_approve_stale_draft_id_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    client.post(_url(), headers=_auth(signing_key))

    response = client.post(
        _url("/approve"),
        json={"draftId": str(uuid4()), "rationale": "Bản nháp không còn mới nhất."},
        headers=_auth(signing_key),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STALE_NOTIFICATION_DRAFT"
    assert orchestration.ensure_gate_calls == []


def test_approve_without_draft_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        _url("/approve"),
        json={"draftId": str(uuid4()), "rationale": "Chưa có bản nháp."},
        headers=_auth(signing_key),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOTIFICATION_DRAFT_NOT_AVAILABLE"
    assert orchestration.ensure_gate_calls == []


# ---------------------------------------------------------------------------
# Mock delivery: gate + separation of actor
# ---------------------------------------------------------------------------


def test_deliver_blocked_before_gate(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    client.post(_url(), headers=_auth(signing_key))  # drafted by OFFICER_A

    # OFFICER_B (a different, valid actor) delivers before approval.
    response = client.post(
        _url("/deliver"), json={}, headers=_auth(signing_key, subject=OFFICER_B)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "GATE_NOT_SATISFIED"
    assert repository.delivery_calls == 0


def test_deliver_blocked_for_same_actor(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    created = client.post(_url(), headers=_auth(signing_key)).json()  # OFFICER_A
    client.post(
        _url("/approve"),
        json={"draftId": created["id"], "rationale": "Đã phê duyệt."},
        headers=_auth(signing_key),
    )

    # OFFICER_A (the draft creator) tries to deliver despite a satisfied gate.
    response = client.post(_url("/deliver"), json={}, headers=_auth(signing_key))

    assert response.status_code == 409
    assert response.json()["code"] == "SAME_ACTOR_FORBIDDEN"
    assert repository.delivery_calls == 0


def test_deliver_writes_receipt_with_matching_hash(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    created = client.post(_url(), headers=_auth(signing_key)).json()  # OFFICER_A
    client.post(
        _url("/approve"),
        json={"draftId": created["id"], "rationale": "Đã phê duyệt."},
        headers=_auth(signing_key),
    )

    # OFFICER_B (a different actor) delivers the approved draft.
    response = client.post(
        _url("/deliver"), json={}, headers=_auth(signing_key, subject=OFFICER_B)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["deliveredVia"] == MOCK_DELIVERY_CHANNEL
    assert body["contentHash"] == created["contentHash"]
    assert body["recordedBy"] == str(OFFICER_B)
    assert repository.delivery_calls == 1


def test_deliver_rejects_non_ops_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    client.post(_url(), headers=_auth(signing_key))

    response = client.post(
        _url("/deliver"),
        json={},
        headers=_auth(signing_key, subject=OFFICER_B, roles=["AUDITOR"]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.delivery_calls == 0


# ---------------------------------------------------------------------------
# Participant read
# ---------------------------------------------------------------------------


def test_get_returns_draft_receipt_and_gate_status(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    created = client.post(_url(), headers=_auth(signing_key)).json()
    client.post(
        _url("/approve"),
        json={"draftId": created["id"], "rationale": "Đã phê duyệt."},
        headers=_auth(signing_key),
    )
    client.post(
        _url("/deliver"), json={}, headers=_auth(signing_key, subject=OFFICER_B)
    )

    response = client.get(
        _url(), headers=_auth(signing_key, roles=[INTAKE_OFFICER_ROLE])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["id"] == created["id"]
    assert body["receipt"]["deliveredVia"] == MOCK_DELIVERY_CHANNEL
    assert body["approvalGateStatus"] == GateStatus.SATISFIED.value


def test_get_without_draft_reports_open_gate(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeNotificationRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.get(_url(), headers=_auth(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert body["draft"] is None
    assert body["receipt"] is None
    assert body["approvalGateStatus"] == GateStatus.OPEN.value
