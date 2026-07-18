"""Role-gated API tests for the stage-2 FinancingRequest surfaces.

All customer data in this project is synthetic and created solely for
demonstration.  The financing request belongs to the invented SME "Cong ty TNHH
Nong San Sach Vinh Phuc Demo".

``main.py`` is intentionally out of scope for stage 2, so these tests include the
financing router into the app directly -- the production wiring is a separate
change.
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
from creditops.api.financing import router as financing_router
from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
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
from creditops.config import Settings
from creditops.domain.enums import TaskStatus
from creditops.domain.financing_requests import (
    FinancingRequestDraft,
    FinancingRequestVersion,
)
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_ID = UUID("10000000-0000-0000-0000-000000000007")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeFinancingRepository:
    def __init__(self) -> None:
        self.versions: list[FinancingRequestVersion] = []
        self.audit_events: list[Any] = []

    async def list_versions(
        self, case_id: UUID
    ) -> tuple[FinancingRequestVersion, ...]:
        return tuple(v for v in self.versions if v.case_id == case_id)

    async def latest_version(
        self, case_id: UUID
    ) -> FinancingRequestVersion | None:
        matches = [v for v in self.versions if v.case_id == case_id]
        return matches[-1] if matches else None

    async def append_version(
        self,
        *,
        case_id: UUID,
        case_version: int,
        fields: FinancingRequestDraft,
        actor_id: UUID,
    ) -> FinancingRequestVersion:
        next_version = 1 + max(
            (v.request_version for v in self.versions if v.case_id == case_id),
            default=0,
        )
        version = FinancingRequestVersion(
            id=uuid4(),
            case_id=case_id,
            case_version=case_version,
            request_version=next_version,
            requested_amount=fields.requested_amount,
            purpose_vi=fields.purpose_vi,
            currency=fields.currency,
            product_vi=fields.product_vi,
            term_months=fields.term_months,
            expected_use_date=fields.expected_use_date,
            repayment_source_vi=fields.repayment_source_vi,
            repayment_plan_vi=fields.repayment_plan_vi,
            proposed_security_vi=fields.proposed_security_vi,
            customer_own_funds=fields.customer_own_funds,
            connected_trade_products_vi=fields.connected_trade_products_vi,
            working_capital_cycle_vi=fields.working_capital_cycle_vi,
            key_suppliers_customers_vi=fields.key_suppliers_customers_vi,
            proposed_cash_flow_controls_vi=fields.proposed_cash_flow_controls_vi,
            created_by=actor_id,
            created_at=NOW,
        )
        self.versions.append(version)
        return version

    async def append_audit(self, event: Any) -> None:
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
        raise AssertionError("not used by the financing API")

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
    repository: FakeFinancingRepository,
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
    # main.py is out of scope for stage 2; wire the router into the test app.
    application.include_router(financing_router)
    application.state.financing_repository = repository
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


def _append(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    *,
    amount: str = "5000000000",
    purpose: str = "Bổ sung vốn lưu động",
) -> Any:
    return client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/versions",
        json={"requestedAmount": amount, "purpose": purpose, "currency": "VND"},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )


def test_append_creates_version_1_then_2(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeFinancingRepository()
    client = _build_client(signing_key, repository=repository)

    first = _append(client, signing_key)
    assert first.status_code == 201
    assert first.json()["requestVersion"] == 1
    assert first.json()["currency"] == "VND"

    second = _append(client, signing_key, purpose="Điều chỉnh nhu cầu")
    assert second.status_code == 201
    assert second.json()["requestVersion"] == 2

    # Both versions persist append-only; nothing is overwritten.
    assert [v.request_version for v in repository.versions] == [1, 2]
    assert len(repository.audit_events) == 2
    assert repository.audit_events[0].event_type == "FINANCING_REQUEST_VERSION_APPENDED"


def test_list_versions_returns_history_and_latest(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeFinancingRepository()
    client = _build_client(signing_key, repository=repository)
    _append(client, signing_key)
    _append(client, signing_key)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/financing-request/versions",
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["latestVersion"] == 2
    assert [v["requestVersion"] for v in body["versions"]] == [1, 2]


def test_confirm_on_latest_satisfies_gate_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeFinancingRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    _append(client, signing_key)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/confirm",
        json={"version": 1, "rationale": "Nhu cầu tài trợ đã được xác nhận."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == "HG_FINANCING_NEED_CONFIRMED"
    assert body["status"] == "SATISFIED"
    assert body["dispositionRef"] == "financing-request:1"

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_FINANCING_NEED_CONFIRMED
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == OFFICER_A
    assert call["disposition_ref"] == "financing-request:1"

    # The confirmation self-fires an idempotent orchestration tick.
    plan_tasks = [
        c
        for c in orchestration.created_tasks
        if c["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN
    # The confirmation is audited.
    assert any(
        e.event_type == "FINANCING_NEED_CONFIRMED" for e in repository.audit_events
    )


def test_confirm_on_stale_version_is_409_and_writes_no_gate(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeFinancingRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _append(client, signing_key)
    _append(client, signing_key)  # latest is now version 2

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/confirm",
        json={"version": 1, "rationale": "Xác nhận phiên bản cũ."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_FINANCING_VERSION"
    assert body["details"]["expectedVersion"] == 2
    # No gate is written for a stale confirmation.
    assert orchestration.ensure_gate_calls == []


def test_confirm_without_any_version_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeFinancingRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/confirm",
        json={"version": 1, "rationale": "Chưa có phiên bản nào."},
        headers={"Authorization": f"Bearer {token(signing_key)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "FINANCING_REQUEST_NOT_AVAILABLE"
    assert orchestration.ensure_gate_calls == []


def test_append_rejects_non_intake_actor(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeFinancingRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/versions",
        json={"requestedAmount": "1000000000", "purpose": "Không được phép"},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.versions == []


def test_confirm_rejects_non_intake_actor(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeFinancingRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"/api/v1/cases/{CASE_ID}/financing-request/confirm",
        json={"version": 1, "rationale": "Không được phép"},
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=[RISK_REVIEWER_ROLE])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert orchestration.ensure_gate_calls == []


def test_unassigned_actor_gets_indistinguishable_404(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeFinancingRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        f"/api/v1/cases/{CASE_ID}/financing-request/versions",
        headers={"Authorization": f"Bearer {token(signing_key, subject=uuid4())}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
