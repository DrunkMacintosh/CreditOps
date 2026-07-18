"""Role-gated API tests for the stage-14 settlement / recovery surfaces.

Human-only authority: recording the settlement check and confirming settlement,
and opening / approving a recovery case, require the independent ``OPS_CHECKER``
role.  Settlement opens only on a derived zero balance; recovery opens only on a
deterministic sustained-shortfall trigger plus a human escalation rationale, and
the strategy approver must differ from the escalator.  The router is mounted onto
the app built by ``create_app`` here (``main.py`` wiring is a deferred lead
decision), and the repositories are injected directly.

All customer data in this project is synthetic and created solely for
demonstration; the fixture case belongs to the invented SME "Cong ty TNHH Nong
San Sach Vinh Phuc Demo".
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
from creditops.api.settlement_recovery import OPS_CHECKER_ROLE
from creditops.api.settlement_recovery import router as settlement_router
from creditops.application.orchestration.roles import OPS_OFFICER_ROLE
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.settlement_recovery import (
    RecordedRecoveryCase,
    RecordedSettlementCheck,
    RecordedSettlementReceipt,
    RecoveryCaseNotFound,
    RecoveryStrategyConflict,
)
from creditops.config import Settings
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.settlement_recovery import (
    RecoveryStatus,
    SettlementReceiptKind,
)
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
ESCALATOR = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
APPROVER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f1")
ASSIGNED = frozenset({ESCALATOR, APPROVER})
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class FakeSettlementRecoveryRepository:
    def __init__(self) -> None:
        self.checks: dict[UUID, RecordedSettlementCheck] = {}
        self.receipts: dict[UUID, list[RecordedSettlementReceipt]] = {}
        self.recovery: dict[UUID, RecordedRecoveryCase] = {}
        self.audit_events: list[Any] = []
        self.check_calls = 0
        self.recovery_calls = 0

    async def record_settlement_check(
        self, *, check: Any, actor_id: UUID, actor_role: str
    ) -> RecordedSettlementCheck:
        self.check_calls += 1
        record = RecordedSettlementCheck(
            id=check.id,
            case_id=check.case_id,
            case_version=check.case_version,
            outstanding_principal=check.outstanding_principal,
            outstanding_interest=check.outstanding_interest,
            outstanding_fees=check.outstanding_fees,
            open_exception_count=check.open_exception_count,
            zero_balance_confirmed=check.zero_balance_confirmed,
            recorded_by=check.recorded_by,
            created_at=NOW,
        )
        self.checks[check.id] = record
        return record

    async def list_settlement_checks(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedSettlementCheck, ...]:
        return tuple(
            c
            for c in self.checks.values()
            if c.case_id == case_id and c.case_version == case_version
        )

    async def load_latest_settlement_check(
        self, case_id: UUID, case_version: int
    ) -> RecordedSettlementCheck | None:
        matches = [
            c
            for c in self.checks.values()
            if c.case_id == case_id and c.case_version == case_version
        ]
        return matches[-1] if matches else None

    async def record_settlement_receipts(
        self,
        *,
        settlement_check_id: UUID,
        case_id: UUID,
        case_version: int,
        receipts: Any,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedSettlementReceipt, ...]:
        existing = self.receipts.setdefault(settlement_check_id, [])
        present = {r.kind for r in existing}
        for kind, note in receipts:
            if kind not in present:
                existing.append(
                    RecordedSettlementReceipt(
                        id=uuid4(),
                        settlement_check_id=settlement_check_id,
                        kind=kind,
                        note_vi=note,
                        recorded_by=actor_id,
                        created_at=NOW,
                    )
                )
        return tuple(existing)

    async def list_settlement_receipts(
        self, settlement_check_id: UUID
    ) -> tuple[RecordedSettlementReceipt, ...]:
        return tuple(self.receipts.get(settlement_check_id, []))

    async def record_recovery_case(
        self, *, recovery: Any, actor_id: UUID, actor_role: str
    ) -> RecordedRecoveryCase:
        self.recovery_calls += 1
        record = RecordedRecoveryCase(
            id=recovery.id,
            case_id=recovery.case_id,
            case_version=recovery.case_version,
            trigger_summary_vi=recovery.trigger_summary_vi,
            escalated_by=recovery.escalated_by,
            escalation_rationale_vi=recovery.escalation_rationale_vi,
            status=recovery.status,
            evidence_refs=recovery.evidence_refs,
            options=recovery.options,
            approved_by=None,
            created_at=NOW,
        )
        self.recovery[recovery.id] = record
        return record

    async def list_recovery_cases(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedRecoveryCase, ...]:
        return tuple(
            c
            for c in self.recovery.values()
            if c.case_id == case_id and c.case_version == case_version
        )

    async def load_recovery_case(
        self, recovery_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedRecoveryCase | None:
        c = self.recovery.get(recovery_id)
        if c is None or c.case_id != case_id or c.case_version != case_version:
            return None
        return c

    async def approve_recovery_strategy(
        self,
        *,
        recovery_id: UUID,
        case_id: UUID,
        case_version: int,
        approved_by: UUID,
        actor_role: str,
    ) -> RecordedRecoveryCase:
        c = self.recovery.get(recovery_id)
        if c is None or c.case_id != case_id:
            raise RecoveryCaseNotFound(str(recovery_id))
        if c.status is not RecoveryStatus.PREPARING:
            raise RecoveryStrategyConflict(c.status.value)
        updated = replace(
            c, status=RecoveryStatus.STRATEGY_APPROVED, approved_by=approved_by
        )
        self.recovery[recovery_id] = updated
        return updated

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
        raise AssertionError("not used by the settlement/recovery API")

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
        if case_id != CASE_ID or actor_id not in ASSIGNED:
            return None
        return CaseRecord(
            id=CASE_ID,
            version=1,
            assigned_officer_id=actor_id,
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
    repository: FakeSettlementRecoveryRepository,
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
    application.include_router(settlement_router)
    application.state.settlement_recovery_repository = repository
    application.state.orchestration_repository = orchestration_repository
    application.state.agent_task_queue = agent_queue
    return TestClient(application)


def token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: UUID = ESCALATOR,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(subject),
            "roles": roles or [OPS_CHECKER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _checker(signing_key: rsa.RSAPrivateKey, *, subject: UUID = ESCALATOR) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {token(signing_key, subject=subject, roles=[OPS_CHECKER_ROLE])}"
        )
    }


def _base() -> str:
    return f"/api/v1/cases/{CASE_ID}"


def _eligible_body() -> dict[str, Any]:
    return {
        "outstandingPrincipal": "0.00",
        "outstandingInterest": "0",
        "outstandingFees": "0",
        "openExceptionCount": 0,
    }


def _recovery_body() -> dict[str, Any]:
    return {
        "outstandingTotal": "1000000",
        "periodsInShortfall": 3,
        "triggerSummary": "Shortfall kéo dài nhiều kỳ (mô phỏng).",
        "escalationRationale": "Đề nghị chuẩn bị hồ sơ thu hồi (mô phỏng).",
        "evidenceRefs": ["ref://ledger/exception-1", "ref://alert/42"],
        "options": [
            {
                "label": "Cơ cấu lại thời hạn trả nợ (mô phỏng).",
                "description": "Đề xuất phương án cơ cấu (mô phỏng).",
                "consequences": "Kéo dài thời hạn, tăng chi phí lãi (mô phỏng).",
            }
        ],
    }


def _open_recovery(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    response = client.post(
        f"{_base()}/recovery", json=_recovery_body(), headers=_checker(signing_key)
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# -- settlement (14A) ---------------------------------------------------------


def test_settlement_check_eligible_records(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"{_base()}/settlement/check", json=_eligible_body(), headers=_checker(signing_key)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["zeroBalanceConfirmed"] is True
    assert body["outstandingPrincipal"] == "0"  # canonicalized
    assert repository.check_calls == 1


def test_settlement_check_nonzero_balance_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _eligible_body() | {"outstandingInterest": "1500"}
    response = client.post(
        f"{_base()}/settlement/check", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "SETTLEMENT_NOT_ELIGIBLE"
    assert payload["details"]["zeroBalance"] is False
    assert repository.check_calls == 0


def test_settlement_check_open_exception_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _eligible_body() | {"openExceptionCount": 2}
    response = client.post(
        f"{_base()}/settlement/check", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "SETTLEMENT_NOT_ELIGIBLE"
    assert payload["details"]["zeroBalance"] is True
    assert payload["details"]["openExceptionCount"] == 2


def test_settlement_check_invalid_amount_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _eligible_body() | {"outstandingPrincipal": "-5"}
    response = client.post(
        f"{_base()}/settlement/check", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SETTLEMENT_SNAPSHOT"


def test_settlement_check_rejects_non_checker(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"{_base()}/settlement/check",
        json=_eligible_body(),
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"
            )
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_settlement_check_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"{_base()}/settlement/check",
        json=_eligible_body(),
        headers=_checker(signing_key, subject=uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


def test_settlement_confirm_writes_mock_receipts_and_gate(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    # First record an eligible check.
    created = client.post(
        f"{_base()}/settlement/check", json=_eligible_body(), headers=_checker(signing_key)
    )
    assert created.status_code == 201

    response = client.post(
        f"{_base()}/settlement/confirm", headers=_checker(signing_key)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gateType"] == "HG_SETTLEMENT_CONFIRMED"
    assert body["status"] == "SATISFIED"
    assert body["dispositionRef"] == "settlement:1"
    assert {r["kind"] for r in body["receipts"]} == {
        SettlementReceiptKind.MOCK_CLOSURE.value,
        SettlementReceiptKind.MOCK_RELEASE.value,
    }

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_SETTLEMENT_CONFIRMED
    assert call["status"] == GateStatus.SATISFIED
    # Retick fired an ORCHESTRATOR_PLAN task and dispatched it.
    plan_tasks = [
        c
        for c in orchestration.created_tasks
        if c["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert any(e.event_type == "SETTLEMENT_CONFIRMED" for e in repository.audit_events)


def test_settlement_confirm_without_eligible_check_is_409(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"{_base()}/settlement/confirm", headers=_checker(signing_key)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SETTLEMENT_NOT_ELIGIBLE"
    assert orchestration.ensure_gate_calls == []


def test_settlement_get_lists_checks(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)
    client.post(
        f"{_base()}/settlement/check", json=_eligible_body(), headers=_checker(signing_key)
    )

    response = client.get(f"{_base()}/settlement", headers=_checker(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert len(body["checks"]) == 1
    assert body["confirmable"] is True
    assert body["caseVersion"] == 1


# -- recovery (14B) -----------------------------------------------------------


def test_recovery_opens_with_trigger_and_escalation(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"{_base()}/recovery", json=_recovery_body(), headers=_checker(signing_key)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PREPARING"
    assert body["escalatedBy"] == str(ESCALATOR)
    assert len(body["options"]) == 1
    assert body["evidenceRefs"] == ["ref://ledger/exception-1", "ref://alert/42"]
    assert repository.recovery_calls == 1


def test_recovery_below_threshold_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _recovery_body() | {"periodsInShortfall": 1}
    response = client.post(
        f"{_base()}/recovery", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "RECOVERY_NOT_TRIGGERED"
    assert payload["details"]["periodsInShortfall"] == 1
    assert repository.recovery_calls == 0


def test_recovery_zero_balance_never_triggers(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _recovery_body() | {"outstandingTotal": "0", "periodsInShortfall": 12}
    response = client.post(
        f"{_base()}/recovery", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "RECOVERY_NOT_TRIGGERED"


def test_recovery_missing_rationale_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    body = _recovery_body()
    del body["escalationRationale"]
    response = client.post(
        f"{_base()}/recovery", json=body, headers=_checker(signing_key)
    )

    assert response.status_code == 422


def test_recovery_rejects_non_checker(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        f"{_base()}/recovery",
        json=_recovery_body(),
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"
            )
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_recovery_approve_by_different_actor_satisfies_gate(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    recovery_id = _open_recovery(client, signing_key)  # escalated by ESCALATOR

    response = client.post(
        f"{_base()}/recovery/{recovery_id}/approve-strategy",
        headers=_checker(signing_key, subject=APPROVER),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gateType"] == "HG_RECOVERY_STRATEGY_APPROVED"
    assert body["status"] == "SATISFIED"
    assert body["dispositionRef"] == f"recovery-strategy:{recovery_id}"
    assert body["recoveryCase"]["status"] == "STRATEGY_APPROVED"
    assert body["recoveryCase"]["approvedBy"] == str(APPROVER)

    assert len(orchestration.ensure_gate_calls) == 1
    assert (
        orchestration.ensure_gate_calls[0]["gate_type"]
        == GateType.HG_RECOVERY_STRATEGY_APPROVED
    )
    assert len(queue.sent) == 1
    assert any(
        e.event_type == "RECOVERY_STRATEGY_APPROVED" for e in repository.audit_events
    )


def test_recovery_approve_by_escalator_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    recovery_id = _open_recovery(client, signing_key)  # escalated by ESCALATOR

    # The SAME actor who escalated may not approve the strategy.
    response = client.post(
        f"{_base()}/recovery/{recovery_id}/approve-strategy",
        headers=_checker(signing_key, subject=ESCALATOR),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SAME_ACTOR_FORBIDDEN"
    assert orchestration.ensure_gate_calls == []


def test_recovery_approve_already_approved_is_409(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    recovery_id = _open_recovery(client, signing_key)

    first = client.post(
        f"{_base()}/recovery/{recovery_id}/approve-strategy",
        headers=_checker(signing_key, subject=APPROVER),
    )
    assert first.status_code == 200

    second = client.post(
        f"{_base()}/recovery/{recovery_id}/approve-strategy",
        headers=_checker(signing_key, subject=APPROVER),
    )

    assert second.status_code == 409
    assert second.json()["code"] == "RECOVERY_ALREADY_APPROVED"


def test_recovery_approve_missing_case_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"{_base()}/recovery/{uuid4()}/approve-strategy",
        headers=_checker(signing_key, subject=APPROVER),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "RECOVERY_CASE_NOT_FOUND"


def test_recovery_get_lists_cases(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeSettlementRecoveryRepository()
    client = _build_client(signing_key, repository=repository)
    _open_recovery(client, signing_key)

    response = client.get(f"{_base()}/recovery", headers=_checker(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert len(body["recoveryCases"]) == 1
    assert body["caseVersion"] == 1
