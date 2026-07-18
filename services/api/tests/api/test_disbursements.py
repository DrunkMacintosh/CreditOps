"""Role-gated API tests for the stage-11 proposed disbursement surfaces.

Human-only authority with a maker-checker / authority split: the OPS_OFFICER
(maker) creates the action; two SEPARATE OPS_CHECKER gates (validate then
authorize) MUST be satisfied by DIFFERENT actors; execution runs the labelled
mock adapter only after BOTH gates and only by an actor different from the
creator.  A timeout / ambiguous result records EXECUTION_UNKNOWN and is NEVER
auto-retried -- a second execute is refused until a human reconciliation, and
only a CONFIRMED_NOT_EXECUTED reconciliation re-opens a new attempt.

The routers are mounted onto the app built by ``create_app`` and the
repositories are injected directly (``main.py`` wiring is a deferred lead
decision).  All customer data is synthetic; the fixture case belongs to the
invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
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
from creditops.api.conditions import OPS_CHECKER_ROLE
from creditops.api.disbursements import router as disbursements_router
from creditops.application.orchestration.roles import OPS_OFFICER_ROLE
from creditops.application.ports.credit_decisions import (
    RecordedDecision,
    RecordedTermSnapshot,
)
from creditops.application.ports.disbursements import (
    AlreadyExecutedError,
    DisbursementActionNotFound,
    NotReconcilableError,
    ReconciliationRequiredError,
    RecordedDisbursementAction,
    RecordedExecutionReceipt,
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
from creditops.domain.disbursements import (
    REATTEMPTABLE_STATUSES,
    RECONCILABLE_STATUSES,
    ExecutionStatus,
    ProposedDisbursementAction,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.infrastructure.mock.disbursement_adapter import (
    MockDisbursementExecutionAdapter,
)
from creditops.main import create_app

ISSUER = "https://identity.test.example"
AUDIENCE = "creditops-api"
KEY_ID = "test-rs256-key"
OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CHECKER_1 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHECKER_2 = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CASE_ID = UUID("10000000-0000-0000-0000-0000000000f1")
DECISION_ID = UUID("d0000000-0000-0000-0000-0000000000f1")
ASSIGNED = frozenset({OFFICER_A, CHECKER_1, CHECKER_2})
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
APPROVED_AMOUNT = "5000000000"
APPROVED_CURRENCY = "VND"


# -- fakes --------------------------------------------------------------------


class FakeDisbursementRepository:
    def __init__(self) -> None:
        self.actions: dict[UUID, RecordedDisbursementAction] = {}
        self.by_version: dict[tuple[UUID, int], UUID] = {}
        self.receipts: dict[UUID, list[RecordedExecutionReceipt]] = {}
        self.create_calls = 0

    async def create_action(
        self, *, action: ProposedDisbursementAction
    ) -> RecordedDisbursementAction:
        self.create_calls += 1
        key = (action.case_id, action.case_version)
        if key in self.by_version:
            existing = self.actions[self.by_version[key]]
            return replace(existing, created=False)
        record = RecordedDisbursementAction(
            id=action.id,
            case_id=action.case_id,
            case_version=action.case_version,
            decision_id=action.decision_id,
            amount_text=action.amount_text,
            currency=action.currency,
            beneficiary_ref_vi=action.beneficiary_ref_vi,
            account_ref_vi=action.account_ref_vi,
            status=action.status,
            created_by=action.created_by,
            created_at=NOW,
            created=True,
        )
        self.actions[action.id] = record
        self.by_version[key] = action.id
        return record

    async def load_action(
        self, action_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedDisbursementAction | None:
        action = self.actions.get(action_id)
        if (
            action is None
            or action.case_id != case_id
            or action.case_version != case_version
        ):
            return None
        return action

    async def list_actions(
        self, case_id: UUID
    ) -> tuple[RecordedDisbursementAction, ...]:
        return tuple(a for a in self.actions.values() if a.case_id == case_id)

    async def list_receipts(
        self, action_id: UUID
    ) -> tuple[RecordedExecutionReceipt, ...]:
        return tuple(self.receipts.get(action_id, ()))

    async def execute_action(
        self,
        *,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
        adapter: Any,
        idempotency_key: str,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedDisbursementAction, RecordedExecutionReceipt]:
        action = self.actions.get(action_id)
        if action is None:
            raise DisbursementActionNotFound(str(action_id))
        if action.status in RECONCILABLE_STATUSES:
            raise ReconciliationRequiredError(action.status.value)
        if action.status is ExecutionStatus.CONFIRMED_EXECUTED:
            raise AlreadyExecutedError(str(action_id))
        if action.status not in REATTEMPTABLE_STATUSES:
            raise ReconciliationRequiredError(action.status.value)
        receipt = adapter.execute(action_id=action_id, idempotency_key=idempotency_key)
        updated = replace(action, status=receipt.result_status)
        self.actions[action_id] = updated
        recorded = RecordedExecutionReceipt(
            id=receipt.id,
            action_id=action_id,
            idempotency_key=receipt.idempotency_key,
            adapter_label=receipt.adapter_label,
            result_status=receipt.result_status,
            receipt_ref=receipt.receipt_ref,
            recorded_by=actor_id,
            created_at=NOW,
        )
        self.receipts.setdefault(action_id, []).append(recorded)
        return updated, recorded

    async def reconcile_action(
        self,
        *,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
        outcome: ExecutionStatus,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedDisbursementAction:
        action = self.actions.get(action_id)
        if action is None:
            raise DisbursementActionNotFound(str(action_id))
        if action.status not in RECONCILABLE_STATUSES:
            raise NotReconcilableError(action.status.value)
        updated = replace(action, status=outcome)
        self.actions[action_id] = updated
        return updated


class FakeCreditDecisionRepository:
    def __init__(
        self,
        decision: str | None = "APPROVED_AS_PROPOSED",
        *,
        amount: str | None = APPROVED_AMOUNT,
        currency: str | None = APPROVED_CURRENCY,
    ) -> None:
        self.decision = decision
        self.amount = amount
        self.currency = currency

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None:
        if self.decision is None or case_id != CASE_ID:
            return None
        terms: dict[str, object] = {}
        if self.amount is not None:
            terms["amount"] = self.amount
        if self.currency is not None:
            terms["currency"] = self.currency
        snapshot = (
            RecordedTermSnapshot(
                id=uuid4(),
                decision_id=DECISION_ID,
                case_id=case_id,
                case_version=case_version,
                terms=terms,
                snapshot_hash="0" * 64,
                created_at=NOW,
            )
            if terms
            else None
        )
        return RecordedDecision(
            id=DECISION_ID,
            case_id=case_id,
            case_version=case_version,
            decision=self.decision,
            rationale_vi="Phê duyệt (mô phỏng).",
            decided_by=OFFICER_A,
            decided_by_role="CREDIT_APPROVER",
            memo_artifact_id=None,
            risk_assessment_id=None,
            underwriting_assessment_id=None,
            conditions=(),
            created_at=NOW,
            snapshot=snapshot,
            created=False,
        )

    async def load_decision_binding(self, case_id: UUID) -> None:
        return None

    async def record_decision(self, **kwargs: Any) -> None:
        raise AssertionError("not used by the disbursement API")


class FakeOrchestrationRepository:
    def __init__(self, *, conditions_confirmed: bool = True) -> None:
        self.gates: dict[tuple[GateType, int], GateRecord] = {}
        self.created_tasks: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []
        self.ensure_gate_calls: list[dict[str, Any]] = []
        if conditions_confirmed:
            self.gates[(GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED, 1)] = GateRecord(
                gate_type=GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED,
                case_version=1,
                status=GateStatus.SATISFIED,
                satisfied_by_actor_id=CHECKER_1,
            )

    async def load_snapshot(self, case_id: UUID) -> Any:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=1,
            has_intake_handoff=True,
            gates=tuple(self.gates.values()),
        )

    async def ensure_gate(self, **kwargs: Any) -> GateRecord:
        self.ensure_gate_calls.append(kwargs)
        key = (kwargs["gate_type"], kwargs["case_version"])
        existing = self.gates.get(key)
        if existing is not None and existing.status is GateStatus.SATISFIED:
            return existing
        record = GateRecord(
            gate_type=kwargs["gate_type"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
            satisfied_by_actor_id=kwargs.get("satisfied_by_actor_id"),
            disposition_ref=kwargs.get("disposition_ref"),
        )
        self.gates[key] = record
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
        raise AssertionError("not used by the disbursement API")

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


# -- harness ------------------------------------------------------------------


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_client(
    signing_key: rsa.RSAPrivateKey,
    *,
    repository: FakeDisbursementRepository,
    decision_repository: FakeCreditDecisionRepository | None = None,
    orchestration_repository: FakeOrchestrationRepository | None = None,
    adapter: Any | None = None,
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
    application.include_router(disbursements_router)
    application.state.disbursement_repository = repository
    application.state.credit_decision_repository = (
        decision_repository or FakeCreditDecisionRepository()
    )
    application.state.orchestration_repository = (
        orchestration_repository or FakeOrchestrationRepository()
    )
    application.state.disbursement_execution_adapter = (
        adapter or MockDisbursementExecutionAdapter()
    )
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


def _url() -> str:
    return f"/api/v1/cases/{CASE_ID}/proposed-disbursements"


def _officer(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"}


def _checker(signing_key: rsa.RSAPrivateKey, *, subject: UUID) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {token(signing_key, subject=subject, roles=[OPS_CHECKER_ROLE])}"
        )
    }


def _create(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    *,
    body: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        _url(),
        json=body
        or {
            "beneficiaryRef": "Nhà cung cấp (mô phỏng)",
            "accountRef": "TK-BENEFICIARY-DEMO",
        },
        headers=_officer(signing_key),
    )


def _create_action_id(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    response = _create(client, signing_key)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _validate(
    client: TestClient, signing_key: rsa.RSAPrivateKey, action_id: str, *, subject: UUID
) -> Any:
    return client.post(
        f"{_url()}/{action_id}/validate", headers=_checker(signing_key, subject=subject)
    )


def _authorize(
    client: TestClient, signing_key: rsa.RSAPrivateKey, action_id: str, *, subject: UUID
) -> Any:
    return client.post(
        f"{_url()}/{action_id}/authorize",
        headers=_checker(signing_key, subject=subject),
    )


def _execute(
    client: TestClient, signing_key: rsa.RSAPrivateKey, action_id: str, *, subject: UUID
) -> Any:
    return client.post(
        f"{_url()}/{action_id}/execute", headers=_checker(signing_key, subject=subject)
    )


def _both_gates(
    client: TestClient, signing_key: rsa.RSAPrivateKey, action_id: str
) -> None:
    assert _validate(client, signing_key, action_id, subject=CHECKER_1).status_code == 200
    assert (
        _authorize(client, signing_key, action_id, subject=CHECKER_2).status_code == 200
    )


# -- create -------------------------------------------------------------------


def test_create_requires_permitting_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        decision_repository=FakeCreditDecisionRepository(decision=None),
    )

    response = _create(client, signing_key)

    assert response.status_code == 409
    assert response.json()["code"] == "DISBURSEMENT_REQUIRES_APPROVAL_DECISION"
    assert repository.create_calls == 0


def test_create_requires_conditions_gate(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=FakeOrchestrationRepository(conditions_confirmed=False),
    )

    response = _create(client, signing_key)

    assert response.status_code == 409
    assert response.json()["code"] == "DISBURSEMENT_CONDITIONS_NOT_CONFIRMED"
    assert repository.create_calls == 0


def test_create_succeeds_and_defaults_to_approved_amount(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(client, signing_key)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    assert body["amount"] == APPROVED_AMOUNT
    assert body["currency"] == APPROVED_CURRENCY
    assert body["decisionId"] == str(DECISION_ID)


def test_create_currency_mismatch_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(
        client,
        signing_key,
        body={
            "amount": APPROVED_AMOUNT,
            "currency": "USD",
            "beneficiaryRef": "Nhà cung cấp",
            "accountRef": "TK-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "CURRENCY_MISMATCH"
    assert repository.create_calls == 0


def test_create_amount_exceeding_approved_is_422(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(
        client,
        signing_key,
        body={
            "amount": "6000000000",
            "beneficiaryRef": "Nhà cung cấp",
            "accountRef": "TK-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "AMOUNT_EXCEEDS_APPROVED"


def test_create_malformed_amount_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(
        client,
        signing_key,
        body={
            "amount": "not-a-number",
            "beneficiaryRef": "Nhà cung cấp",
            "accountRef": "TK-1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_AMOUNT"


def test_create_partial_amount_is_allowed(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = _create(
        client,
        signing_key,
        body={
            "amount": "1000000000",
            "beneficiaryRef": "Nhà cung cấp",
            "accountRef": "TK-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["amount"] == "1000000000"


def test_create_rejects_non_officer(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={"beneficiaryRef": "x", "accountRef": "y"},
        headers=_checker(signing_key, subject=CHECKER_1),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.create_calls == 0


def test_create_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={"beneficiaryRef": "x", "accountRef": "y"},
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=uuid4(), roles=[OPS_OFFICER_ROLE])}"
            )
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"


# -- dual gate ordering + separation of duty ----------------------------------


def test_execute_blocked_before_gates(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)
    action_id = _create_action_id(client, signing_key)

    response = _execute(client, signing_key, action_id, subject=CHECKER_2)

    assert response.status_code == 409
    assert response.json()["code"] == "DISBURSEMENT_NOT_AUTHORIZED"


def test_authorize_before_validate_is_409(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)
    action_id = _create_action_id(client, signing_key)

    response = _authorize(client, signing_key, action_id, subject=CHECKER_2)

    assert response.status_code == 409
    assert response.json()["code"] == "VALIDATION_REQUIRED"


def test_authorize_same_actor_as_validator_is_forbidden(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    action_id = _create_action_id(client, signing_key)
    assert _validate(client, signing_key, action_id, subject=CHECKER_1).status_code == 200

    # The SAME checker who validated may not authorize (maker-checker split).
    response = _authorize(client, signing_key, action_id, subject=CHECKER_1)

    assert response.status_code == 409
    assert response.json()["code"] == "SAME_ACTOR_FORBIDDEN"


def test_validate_requires_checker_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)
    action_id = _create_action_id(client, signing_key)

    response = client.post(
        f"{_url()}/{action_id}/validate", headers=_officer(signing_key)
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_dual_gate_then_execute_succeeds_and_reticks(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)

    # The executor differs from the creator (OFFICER_A); CHECKER_2 is allowed.
    response = _execute(client, signing_key, action_id, subject=CHECKER_2)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"]["status"] == "CONFIRMED_EXECUTED"
    assert body["receipt"]["resultStatus"] == "CONFIRMED_EXECUTED"
    assert body["receipt"]["adapterLabel"] == "MOCK_DISBURSEMENT_EXECUTION_ADAPTER"
    assert body["receipt"]["receiptRef"] is not None

    # Both gates were written to the registry with different actors, and each
    # satisfaction self-fired an orchestration retick.
    validated = [
        c
        for c in orchestration.ensure_gate_calls
        if c["gate_type"] is GateType.HG_DISBURSEMENT_VALIDATED
    ]
    authorized = [
        c
        for c in orchestration.ensure_gate_calls
        if c["gate_type"] is GateType.HG_DISBURSEMENT_AUTHORIZED
    ]
    assert validated and validated[0]["satisfied_by_actor_id"] == CHECKER_1
    assert authorized and authorized[0]["satisfied_by_actor_id"] == CHECKER_2
    assert len(queue.sent) == 2  # one retick per gate


def test_execute_same_as_creator_is_forbidden(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)

    # OFFICER_A created the action but holds no OPS_CHECKER role; give the creator
    # the checker role to isolate the different-from-creator rule.
    creator_as_checker = {
        "Authorization": (
            f"Bearer {token(signing_key, subject=OFFICER_A, roles=[OPS_CHECKER_ROLE])}"
        )
    }
    response = client.post(f"{_url()}/{action_id}/execute", headers=creator_as_checker)

    assert response.status_code == 409
    assert response.json()["code"] == "SAME_ACTOR_FORBIDDEN"


# -- EXECUTION_UNKNOWN + reconciliation ---------------------------------------


def test_execute_unknown_then_second_execute_requires_reconciliation(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        adapter=MockDisbursementExecutionAdapter(simulate_unknown=True),
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)

    first = _execute(client, signing_key, action_id, subject=CHECKER_2)
    assert first.status_code == 200
    assert first.json()["action"]["status"] == "EXECUTION_UNKNOWN"
    assert first.json()["receipt"]["receiptRef"] is None

    # A second execute on an unresolved action is REFUSED (never auto-retried).
    second = _execute(client, signing_key, action_id, subject=CHECKER_2)
    assert second.status_code == 409
    assert second.json()["code"] == "RECONCILIATION_REQUIRED"


def test_reconcile_not_executed_reopens_a_new_attempt(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        adapter=MockDisbursementExecutionAdapter(simulate_unknown=True),
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)
    assert _execute(client, signing_key, action_id, subject=CHECKER_2).status_code == 200

    reconcile = client.post(
        f"{_url()}/{action_id}/reconcile",
        json={"outcome": "CONFIRMED_NOT_EXECUTED", "rationale": "Chưa chuyển tiền."},
        headers=_checker(signing_key, subject=CHECKER_1),
    )
    assert reconcile.status_code == 200, reconcile.text
    assert reconcile.json()["status"] == "CONFIRMED_NOT_EXECUTED"

    # A CONFIRMED_NOT_EXECUTED reconciliation re-opens a new execution attempt.
    retry = _execute(client, signing_key, action_id, subject=CHECKER_2)
    assert retry.status_code == 200
    assert retry.json()["action"]["status"] == "EXECUTION_UNKNOWN"


def test_reconcile_executed_then_execute_is_already_executed(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        adapter=MockDisbursementExecutionAdapter(simulate_unknown=True),
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)
    assert _execute(client, signing_key, action_id, subject=CHECKER_2).status_code == 200

    reconcile = client.post(
        f"{_url()}/{action_id}/reconcile",
        json={"outcome": "CONFIRMED_EXECUTED", "rationale": "Ngân hàng xác nhận đã chuyển."},
        headers=_checker(signing_key, subject=CHECKER_1),
    )
    assert reconcile.status_code == 200
    assert reconcile.json()["status"] == "CONFIRMED_EXECUTED"

    # No new attempt after a confirmed execution.
    retry = _execute(client, signing_key, action_id, subject=CHECKER_2)
    assert retry.status_code == 409
    assert retry.json()["code"] == "ALREADY_EXECUTED"


def test_reconcile_on_proposed_is_not_reconcilable(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)
    action_id = _create_action_id(client, signing_key)

    response = client.post(
        f"{_url()}/{action_id}/reconcile",
        json={"outcome": "CONFIRMED_EXECUTED", "rationale": "Không hợp lệ."},
        headers=_checker(signing_key, subject=CHECKER_1),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_RECONCILABLE"


def test_reconcile_invalid_outcome_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        adapter=MockDisbursementExecutionAdapter(simulate_unknown=True),
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)
    assert _execute(client, signing_key, action_id, subject=CHECKER_2).status_code == 200

    response = client.post(
        f"{_url()}/{action_id}/reconcile",
        json={"outcome": "EXECUTION_REQUESTED", "rationale": "Sai kết quả."},
        headers=_checker(signing_key, subject=CHECKER_1),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_OUTCOME"


# -- list ---------------------------------------------------------------------


def test_list_reports_actions_and_gate_status(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    action_id = _create_action_id(client, signing_key)
    _both_gates(client, signing_key, action_id)

    response = client.get(_url(), headers=_officer(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert body["caseVersion"] == 1
    assert len(body["actions"]) == 1
    detail = body["actions"][0]
    assert detail["action"]["id"] == action_id
    assert detail["validatedGateStatus"] == "SATISFIED"
    assert detail["authorizedGateStatus"] == "SATISFIED"


def test_list_rejects_non_participant(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeDisbursementRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.get(
        _url(),
        headers={
            "Authorization": f"Bearer {token(signing_key, roles=['SOME_OTHER_ROLE'])}"
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
