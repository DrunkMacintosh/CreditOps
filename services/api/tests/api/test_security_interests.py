"""Role-gated API tests for the stage-9 security-perfection ledger surfaces.

Authority (PROPOSED synthetic): the ``LEGAL_REVIEWER``/``OPS_OFFICER`` write roles
and the independent ``OPS_CHECKER`` confirm role, each plus a case assignment,
all fail closed.  ``main.py`` wiring is out of scope, so the router is included
into the app built by ``create_app`` and the fakes are injected directly.

All customer data is synthetic and created solely for demonstration; the fixture
case belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
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
from creditops.api.security_interests import (
    LEGAL_REVIEWER_ROLE,
    OPS_CHECKER_ROLE,
)
from creditops.api.security_interests import (
    router as security_interests_router,
)
from creditops.application.orchestration.roles import (
    OPS_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.security_interests import (
    ForbiddenTransitionError,
    InterestNotAccessibleError,
    InvalidTransitionInputError,
    ItemNotAccessibleError,
    RecordedInterest,
    RecordedInterestWithItems,
    RecordedItem,
)
from creditops.config import Settings
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.security_interests import (
    PerfectionStatus,
    is_allowed_item_transition,
)
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000f09")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeSecurityInterestRepository:
    def __init__(self) -> None:
        self.interests: dict[UUID, RecordedInterest] = {}
        self.items: dict[UUID, RecordedItem] = {}
        self.audit_events: list[Any] = []

    async def create_interest(
        self, *, interest: Any, actor_role: str
    ) -> RecordedInterest:
        record = RecordedInterest(
            id=interest.id,
            case_id=interest.case_id,
            case_version=interest.case_version,
            asset_description_vi=interest.asset_description_vi,
            asset_kind=interest.asset_kind.value,
            owner_name_vi=interest.owner_name_vi,
            valuation_reference=interest.valuation_reference,
            notes_vi=interest.notes_vi,
            created_by=interest.created_by,
            created_at=NOW,
        )
        self.interests[record.id] = record
        return record

    async def add_item(
        self, *, case_id: UUID, item: Any, actor_id: UUID, actor_role: str
    ) -> RecordedItem:
        interest = self.interests.get(item.interest_id)
        if interest is None or interest.case_id != case_id:
            raise InterestNotAccessibleError(str(item.interest_id))
        record = RecordedItem(
            id=item.id,
            interest_id=item.interest_id,
            requirement_vi=item.requirement_vi,
            status=item.status.value,
            evidence_refs=item.evidence_refs,
            filing_reference=item.filing_reference,
            effective_date=item.effective_date,
            expiry_date=item.expiry_date,
            completed_by=item.completed_by,
            completed_at=item.completed_at,
            created_at=NOW,
        )
        self.items[record.id] = record
        return record

    async def transition_item(
        self,
        *,
        case_id: UUID,
        item_id: UUID,
        to_status: PerfectionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale: str | None,
        evidence_refs: tuple[str, ...],
        filing_reference: str | None,
        effective_date: Any,
        expiry_date: Any,
    ) -> RecordedItem:
        current = self.items.get(item_id)
        if current is None or self.interests[current.interest_id].case_id != case_id:
            raise ItemNotAccessibleError(str(item_id))
        current_status = PerfectionStatus(current.status)
        if not is_allowed_item_transition(current_status, to_status):
            raise ForbiddenTransitionError(current_status, to_status)
        merged = current.evidence_refs + tuple(evidence_refs)
        if to_status is PerfectionStatus.COMPLETED and not merged:
            raise InvalidTransitionInputError("COMPLETED requires evidence")
        if to_status is PerfectionStatus.NOT_REQUIRED_BY_HUMAN and not (
            rationale or ""
        ).strip():
            raise InvalidTransitionInputError("NOT_REQUIRED_BY_HUMAN requires rationale")
        completed = to_status is PerfectionStatus.COMPLETED
        updated = replace(
            current,
            status=to_status.value,
            evidence_refs=merged,
            filing_reference=filing_reference or current.filing_reference,
            effective_date=effective_date or current.effective_date,
            expiry_date=expiry_date or current.expiry_date,
            completed_by=actor_id if completed else current.completed_by,
            completed_at=NOW if completed else current.completed_at,
        )
        self.items[item_id] = updated
        return updated

    async def list_interests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedInterestWithItems, ...]:
        entries = []
        for interest in self.interests.values():
            if interest.case_id != case_id or interest.case_version != case_version:
                continue
            items = tuple(
                item
                for item in self.items.values()
                if item.interest_id == interest.id
            )
            entries.append(RecordedInterestWithItems(interest=interest, items=items))
        return tuple(entries)

    async def append_audit(
        self, event: Any, *, actor_id: UUID, actor_role: str
    ) -> None:
        self.audit_events.append(event)


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
        raise AssertionError("not used by the security-interest API")

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
        if case_id != CASE_ID or actor_id != OFFICER_A:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
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
    repository: FakeSecurityInterestRepository,
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
    application.include_router(security_interests_router)
    application.state.security_interest_repository = repository
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
            "roles": roles or [LEGAL_REVIEWER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _base() -> str:
    return f"/api/v1/cases/{CASE_ID}/security-interests"


def _auth(signing_key: rsa.RSAPrivateKey, roles: list[str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, roles=roles)}"}


def _create_interest(client: TestClient, signing_key: rsa.RSAPrivateKey) -> Any:
    return client.post(
        _base(),
        json={
            "assetDescription": "Quyền sử dụng đất tại Vĩnh Phúc (mô phỏng).",
            "assetKind": "REAL_ESTATE",
            "ownerName": "Công ty TNHH Nông Sản Sạch Vĩnh Phúc Demo",
            "valuationReference": "valuation-adapter://demo/asset-1",
        },
        headers=_auth(signing_key),
    )


def _add_item(
    client: TestClient, signing_key: rsa.RSAPrivateKey, interest_id: str
) -> Any:
    return client.post(
        f"{_base()}/{interest_id}/items",
        json={"requirement": "Đăng ký biện pháp bảo đảm (mô phỏng)."},
        headers=_auth(signing_key),
    )


def _transition(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    item_id: str,
    *,
    to_status: str,
    body: dict[str, Any] | None = None,
) -> Any:
    payload = {"toStatus": to_status}
    payload.update(body or {})
    return client.post(
        f"{_base()}/items/{item_id}/transition",
        json=payload,
        headers=_auth(signing_key),
    )


def _complete_item(
    client: TestClient, signing_key: rsa.RSAPrivateKey, item_id: str
) -> None:
    assert (
        _transition(
            client,
            signing_key,
            item_id,
            to_status="EVIDENCE_ATTACHED",
            body={"evidenceRefs": ["storage://demo/receipt-1"]},
        ).status_code
        == 200
    )
    assert (
        _transition(client, signing_key, item_id, to_status="COMPLETED").status_code
        == 200
    )


# -- per-asset creation -------------------------------------------------------


def test_creates_per_asset_interest(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create_interest(client, signing_key)

    assert response.status_code == 201
    body = response.json()
    assert body["assetKind"] == "REAL_ESTATE"
    assert body["caseVersion"] == 1
    assert body["valuationReference"] == "valuation-adapter://demo/asset-1"
    assert body["createdBy"] == str(OFFICER_A)
    assert len(repository.interests) == 1


def test_ops_officer_may_also_write(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _base(),
        json={"assetDescription": "Xe tải (mô phỏng).", "assetKind": "VEHICLE"},
        headers=_auth(signing_key, [OPS_OFFICER_ROLE]),
    )
    assert response.status_code == 201


def test_invalid_asset_kind_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _base(),
        json={"assetDescription": "X.", "assetKind": "SPACESHIP"},
        headers=_auth(signing_key),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_ASSET_KIND"


def test_add_item_to_unknown_interest_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = _add_item(client, signing_key, str(uuid4()))
    assert response.status_code == 404
    assert response.json()["code"] == "INTEREST_NOT_ACCESSIBLE"


# -- transitions enforced -----------------------------------------------------


def test_valid_transition_chain(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]

    attached = _transition(
        client,
        signing_key,
        item_id,
        to_status="EVIDENCE_ATTACHED",
        body={"evidenceRefs": ["storage://demo/receipt-1"]},
    )
    assert attached.status_code == 200
    assert attached.json()["status"] == "EVIDENCE_ATTACHED"
    assert attached.json()["evidenceRefs"] == ["storage://demo/receipt-1"]

    completed = _transition(client, signing_key, item_id, to_status="COMPLETED")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["completedBy"] == str(OFFICER_A)


def test_forbidden_transition_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]

    # PENDING -> COMPLETED is not in the graph.
    response = _transition(
        client,
        signing_key,
        item_id,
        to_status="COMPLETED",
        body={"evidenceRefs": ["storage://demo/receipt-1"]},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "FORBIDDEN_PERFECTION_TRANSITION"
    assert body["details"]["fromStatus"] == "PENDING"
    assert body["details"]["toStatus"] == "COMPLETED"


def test_completed_requires_evidence_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]
    # Advance to EVIDENCE_ATTACHED WITHOUT any evidence ref, then try COMPLETED.
    _transition(client, signing_key, item_id, to_status="EVIDENCE_ATTACHED")

    response = _transition(client, signing_key, item_id, to_status="COMPLETED")
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TRANSITION_INPUT"


def test_not_required_needs_rationale_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]

    response = _transition(
        client, signing_key, item_id, to_status="NOT_REQUIRED_BY_HUMAN"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TRANSITION_INPUT"


# -- confirmation gate --------------------------------------------------------


def test_confirm_blocked_with_zero_interests_is_409(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Xác nhận hoàn thiện."},
        headers=_auth(signing_key, [OPS_CHECKER_ROLE]),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PERFECTION_NOT_SATISFIED"
    assert body["details"]["hasInterests"] is False
    assert orchestration.ensure_gate_calls == []


def test_confirm_blocked_with_interest_without_items_is_409(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    interest_id = _create_interest(client, signing_key).json()["id"]

    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Xác nhận hoàn thiện."},
        headers=_auth(signing_key, [OPS_CHECKER_ROLE]),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PERFECTION_NOT_SATISFIED"
    assert body["details"]["interestsWithoutItems"] == [interest_id]
    assert orchestration.ensure_gate_calls == []


def test_confirm_blocked_with_pending_item_is_409(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]

    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Xác nhận hoàn thiện."},
        headers=_auth(signing_key, [OPS_CHECKER_ROLE]),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PERFECTION_NOT_SATISFIED"
    assert body["details"]["blockingItemIds"] == [item_id]
    assert orchestration.ensure_gate_calls == []


def test_confirm_when_clean_satisfies_gate_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]
    _complete_item(client, signing_key, item_id)

    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Mọi yêu cầu hoàn thiện đã hoàn tất."},
        headers=_auth(signing_key, [OPS_CHECKER_ROLE]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == "HG_SECURITY_PERFECTION_CONFIRMED"
    assert body["status"] == "SATISFIED"
    assert body["dispositionRef"] == "security-perfection:1"

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_SECURITY_PERFECTION_CONFIRMED
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A

    # The confirmation self-fires an idempotent orchestration tick.
    plan_tasks = [
        c
        for c in orchestration.created_tasks
        if c["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert any(
        e.event_type == "SECURITY_PERFECTION_CONFIRMED"
        for e in repository.audit_events
    )


def test_confirm_accepts_not_required_terminal(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    interest_id = _create_interest(client, signing_key).json()["id"]
    item_id = _add_item(client, signing_key, interest_id).json()["id"]
    assert (
        _transition(
            client,
            signing_key,
            item_id,
            to_status="NOT_REQUIRED_BY_HUMAN",
            body={"rationale": "Không bắt buộc với tài sản này (mô phỏng)."},
        ).status_code
        == 200
    )

    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Đã xử lý toàn bộ yêu cầu."},
        headers=_auth(signing_key, [OPS_CHECKER_ROLE]),
    )
    assert response.status_code == 200
    assert len(orchestration.ensure_gate_calls) == 1


# -- authority ----------------------------------------------------------------


def test_create_wrong_role_is_403(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _base(),
        json={"assetDescription": "X.", "assetKind": "OTHER"},
        headers=_auth(signing_key, [RISK_REVIEWER_ROLE]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.interests == {}


def test_confirm_wrong_role_is_403(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    # A writer role is NOT a checker role: the maker cannot self-confirm.
    response = client.post(
        f"{_base()}/confirm",
        json={"rationale": "Không đủ thẩm quyền."},
        headers=_auth(signing_key, [LEGAL_REVIEWER_ROLE]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert orchestration.ensure_gate_calls == []


def test_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _base(),
        json={"assetDescription": "X.", "assetKind": "OTHER"},
        headers={
            "Authorization": f"Bearer {token(signing_key, subject=uuid4())}"
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_participant_reads_ledger(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSecurityInterestRepository()
    client = _build_client(signing_key, repository=repository)
    interest_id = _create_interest(client, signing_key).json()["id"]
    _add_item(client, signing_key, interest_id)

    response = client.get(_base(), headers=_auth(signing_key, [OPS_OFFICER_ROLE]))
    assert response.status_code == 200
    body = response.json()
    assert len(body["interests"]) == 1
    assert body["interests"][0]["interest"]["id"] == interest_id
    assert len(body["interests"][0]["items"]) == 1


def test_domain_transition_helper_is_wired() -> None:
    # Guard: the API's forbidden-transition mapping relies on this domain map.
    assert is_allowed_item_transition(
        PerfectionStatus.PENDING, PerfectionStatus.EVIDENCE_ATTACHED
    )
    assert not is_allowed_item_transition(
        PerfectionStatus.PENDING, PerfectionStatus.COMPLETED
    )
