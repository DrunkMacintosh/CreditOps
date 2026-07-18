"""Role-gated API tests for the stage-10 disbursement ConditionLedger surfaces.

Human-only authority: opening a condition requires ``OPS_OFFICER`` + a
permitting credit decision; verification / waiver / not-applicable and the final
confirmation require the independent ``OPS_CHECKER`` role; the confirming checker
must differ from every actor who VERIFIED a condition.  The routers are mounted
onto the app built by ``create_app`` here (``main.py`` wiring is a deferred lead
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
from creditops.api.conditions import OPS_CHECKER_ROLE
from creditops.api.conditions import router as conditions_router
from creditops.application.orchestration.roles import OPS_OFFICER_ROLE
from creditops.application.ports.conditions import (
    ConditionNotFound,
    ForbiddenConditionTransition,
    RecordedCondition,
)
from creditops.application.ports.credit_decisions import RecordedDecision
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.config import Settings
from creditops.domain.conditions import ConditionStatus, is_transition_allowed
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.tasks import TaskEnvelopeV1
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


class FakeConditionLedgerRepository:
    def __init__(self) -> None:
        self.conditions: dict[UUID, RecordedCondition] = {}
        self.verified_events: list[tuple[UUID, int, UUID]] = []
        self.audit_events: list[Any] = []
        self.create_calls = 0
        self.transition_calls = 0

    async def create_condition(
        self, *, condition: Any, actor_id: UUID, actor_role: str
    ) -> RecordedCondition:
        self.create_calls += 1
        record = RecordedCondition(
            id=condition.id,
            case_id=condition.case_id,
            case_version=condition.case_version,
            decision_id=condition.decision_id,
            condition_text_vi=condition.condition_text_vi,
            owner_vi=condition.owner_vi,
            due_date=condition.due_date,
            status=condition.status,
            evidence_refs=condition.evidence_refs,
            created_at=NOW,
        )
        self.conditions[condition.id] = record
        return record

    async def list_conditions(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCondition, ...]:
        return tuple(
            c
            for c in self.conditions.values()
            if c.case_id == case_id and c.case_version == case_version
        )

    async def load_condition(
        self, condition_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCondition | None:
        c = self.conditions.get(condition_id)
        if c is None or c.case_id != case_id or c.case_version != case_version:
            return None
        return c

    async def transition_condition(
        self,
        *,
        condition_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: ConditionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str | None,
        evidence_refs: tuple[str, ...] | None,
    ) -> RecordedCondition:
        self.transition_calls += 1
        current = self.conditions.get(condition_id)
        if current is None or current.case_id != case_id:
            raise ConditionNotFound(str(condition_id))
        if not is_transition_allowed(current.status, to_status):
            raise ForbiddenConditionTransition(
                f"{current.status.value} -> {to_status.value}"
            )
        new_refs = current.evidence_refs if evidence_refs is None else evidence_refs
        updated = replace(current, status=to_status, evidence_refs=new_refs)
        self.conditions[condition_id] = updated
        if to_status is ConditionStatus.VERIFIED:
            self.verified_events.append((case_id, case_version, actor_id))
        return updated

    async def list_verifying_actor_ids(
        self, case_id: UUID, case_version: int
    ) -> frozenset[UUID]:
        return frozenset(
            actor
            for (cid, version, actor) in self.verified_events
            if cid == case_id and version == case_version
        )

    async def append_audit(self, event: Any) -> None:
        self.audit_events.append(event)


class FakeCreditDecisionRepository:
    def __init__(self, decision: str | None = "APPROVED_WITH_CONDITIONS") -> None:
        self.decision = decision

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None:
        if self.decision is None or case_id != CASE_ID:
            return None
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
            snapshot=None,
            created=False,
        )

    async def load_decision_binding(self, case_id: UUID) -> None:
        return None

    async def record_decision(self, **kwargs: Any) -> None:
        raise AssertionError("not used by the conditions API")


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
        raise AssertionError("not used by the conditions API")

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
    repository: FakeConditionLedgerRepository,
    decision_repository: FakeCreditDecisionRepository | None = None,
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
    application.include_router(conditions_router)
    application.state.condition_ledger_repository = repository
    application.state.credit_decision_repository = (
        decision_repository or FakeCreditDecisionRepository()
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
            "roles": roles or [OPS_OFFICER_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _url() -> str:
    return f"/api/v1/cases/{CASE_ID}/conditions"


def _officer(signing_key: rsa.RSAPrivateKey) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(signing_key, roles=[OPS_OFFICER_ROLE])}"}


def _checker(signing_key: rsa.RSAPrivateKey, *, subject: UUID) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {token(signing_key, subject=subject, roles=[OPS_CHECKER_ROLE])}"
        )
    }


def _create_condition(client: TestClient, signing_key: rsa.RSAPrivateKey) -> str:
    response = client.post(
        _url(),
        json={"conditionText": "Hợp đồng bảo đảm đã ký.", "owner": "CV vận hành"},
        headers=_officer(signing_key),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _transition(
    client: TestClient,
    headers: dict[str, str],
    condition_id: str,
    to_status: str,
    **extra: Any,
) -> Any:
    return client.post(
        f"{_url()}/{condition_id}/transition",
        json={"toStatus": to_status, **extra},
        headers=headers,
    )


def _verify_one(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    *,
    verifier: UUID = CHECKER_1,
) -> str:
    """Drive one condition PENDING -> EVIDENCE_SUBMITTED -> VERIFIED."""

    condition_id = _create_condition(client, signing_key)
    submitted = _transition(
        client,
        _officer(signing_key),
        condition_id,
        "EVIDENCE_SUBMITTED",
        evidenceRefs=["doc://hop-dong"],
    )
    assert submitted.status_code == 200, submitted.text
    verified = _transition(
        client, _checker(signing_key, subject=verifier), condition_id, "VERIFIED"
    )
    assert verified.status_code == 200, verified.text
    return condition_id


# -- creation -----------------------------------------------------------------


def test_create_requires_permitting_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        decision_repository=FakeCreditDecisionRepository(decision=None),
    )

    response = client.post(
        _url(),
        json={"conditionText": "Chưa có quyết định."},
        headers=_officer(signing_key),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONDITIONS_REQUIRE_APPROVAL_DECISION"
    assert repository.create_calls == 0


def test_create_rejects_non_permitting_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(
        signing_key,
        repository=repository,
        decision_repository=FakeCreditDecisionRepository(decision="DECLINED_BY_HUMAN"),
    )

    response = client.post(
        _url(),
        json={"conditionText": "Quyết định từ chối."},
        headers=_officer(signing_key),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONDITIONS_REQUIRE_APPROVAL_DECISION"
    assert repository.create_calls == 0


def test_create_succeeds_with_approval_decision(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={"conditionText": "Hợp đồng bảo đảm đã ký.", "owner": "CV vận hành"},
        headers=_officer(signing_key),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["decisionId"] == str(DECISION_ID)
    assert body["conditionText"] == "Hợp đồng bảo đảm đã ký."


def test_create_rejects_non_officer(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={"conditionText": "Không đủ thẩm quyền."},
        headers=_checker(signing_key, subject=CHECKER_1),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert repository.create_calls == 0


def test_create_unassigned_actor_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = client.post(
        _url(),
        json={"conditionText": "Không được phân công."},
        headers={
            "Authorization": (
                f"Bearer {token(signing_key, subject=uuid4(), roles=[OPS_OFFICER_ROLE])}"
            )
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_ACCESSIBLE"
    assert repository.create_calls == 0


# -- transitions --------------------------------------------------------------


def test_forbidden_transition_is_422(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    condition_id = _create_condition(client, signing_key)

    # PENDING -> VERIFIED is forbidden (no verification without evidence).
    response = _transition(
        client, _checker(signing_key, subject=CHECKER_1), condition_id, "VERIFIED"
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "FORBIDDEN_CONDITION_TRANSITION"
    assert body["details"]["fromStatus"] == "PENDING"
    assert body["details"]["toStatus"] == "VERIFIED"


def test_verified_requires_checker_role(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    condition_id = _create_condition(client, signing_key)
    _transition(
        client,
        _officer(signing_key),
        condition_id,
        "EVIDENCE_SUBMITTED",
        evidenceRefs=["doc://x"],
    )

    # An ops officer cannot VERIFY -- that is the independent checker's act.
    response = _transition(
        client, _officer(signing_key), condition_id, "VERIFIED"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"


def test_waiver_requires_rationale(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    condition_id = _create_condition(client, signing_key)
    _transition(client, _officer(signing_key), condition_id, "WAIVER_REQUESTED")

    # A waiver is a human authority act; the rationale is mandatory.
    response = _transition(
        client, _checker(signing_key, subject=CHECKER_1), condition_id, "WAIVED_BY_HUMAN"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "RATIONALE_REQUIRED"


def test_waiver_with_rationale_succeeds(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    condition_id = _create_condition(client, signing_key)
    _transition(client, _officer(signing_key), condition_id, "WAIVER_REQUESTED")

    response = _transition(
        client,
        _checker(signing_key, subject=CHECKER_1),
        condition_id,
        "WAIVED_BY_HUMAN",
        rationale="Miễn trừ có thẩm quyền (mô phỏng).",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "WAIVED_BY_HUMAN"


def test_transition_on_missing_condition_is_404(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)

    response = _transition(
        client, _officer(signing_key), str(uuid4()), "EVIDENCE_SUBMITTED"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CONDITION_NOT_FOUND"


# -- confirmation -------------------------------------------------------------


def test_confirm_satisfies_gate_and_reticks(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    _verify_one(client, signing_key, verifier=CHECKER_1)

    # An INDEPENDENT checker (not the verifier) confirms.
    response = client.post(
        f"{_url()}/confirm", headers=_checker(signing_key, subject=CHECKER_2)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gateType"] == "HG_DISBURSEMENT_CONDITIONS_CONFIRMED"
    assert body["status"] == "SATISFIED"
    assert body["dispositionRef"] == "disbursement-conditions:1"

    assert len(orchestration.ensure_gate_calls) == 1
    call = orchestration.ensure_gate_calls[0]
    assert call["gate_type"] == GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED
    assert call["status"] == GateStatus.SATISFIED
    assert call["satisfied_by_actor_id"] == CHECKER_2

    # The confirmation self-fires an idempotent orchestration tick.
    plan_tasks = [
        c
        for c in orchestration.created_tasks
        if c["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert len(queue.sent) == 1
    assert any(
        e.event_type == "DISBURSEMENT_CONDITIONS_CONFIRMED"
        for e in repository.audit_events
    )


def test_confirm_blocked_with_pending_condition(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    condition_id = _create_condition(client, signing_key)  # stays PENDING

    response = client.post(
        f"{_url()}/confirm", headers=_checker(signing_key, subject=CHECKER_2)
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONDITIONS_NOT_SATISFIED"
    assert body["details"]["blockingConditionIds"] == [condition_id]
    assert orchestration.ensure_gate_calls == []


def test_confirm_blocked_for_verifying_actor(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )
    _verify_one(client, signing_key, verifier=CHECKER_1)

    # The SAME checker who verified may not self-confirm (separation of duty).
    response = client.post(
        f"{_url()}/confirm", headers=_checker(signing_key, subject=CHECKER_1)
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SAME_ACTOR_FORBIDDEN"
    assert orchestration.ensure_gate_calls == []


def test_confirm_empty_ledger_is_not_confirmable(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(
        f"{_url()}/confirm", headers=_checker(signing_key, subject=CHECKER_2)
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONDITIONS_NOT_SATISFIED"
    assert body["details"]["empty"] is True
    assert body["details"]["blockingConditionIds"] == []
    assert orchestration.ensure_gate_calls == []


def test_confirm_on_not_applicable_only_ledger(signing_key: rsa.RSAPrivateKey) -> None:
    # The synthetic 'no conditions' entry path: a lone NOT_APPLICABLE_BY_HUMAN
    # ledger is confirmable, and separation-of-duty only excludes VERIFIED
    # actors, so the checker who ruled it NA MAY also confirm.
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    queue = RecordingAgentQueue()
    client = _build_client(
        signing_key,
        repository=repository,
        orchestration_repository=orchestration,
        agent_queue=queue,
    )
    condition_id = _create_condition(client, signing_key)
    na = _transition(
        client,
        _checker(signing_key, subject=CHECKER_1),
        condition_id,
        "NOT_APPLICABLE_BY_HUMAN",
        rationale="Không có điều kiện giải ngân áp dụng (mô phỏng).",
    )
    assert na.status_code == 200

    response = client.post(
        f"{_url()}/confirm", headers=_checker(signing_key, subject=CHECKER_1)
    )

    assert response.status_code == 200
    assert len(orchestration.ensure_gate_calls) == 1


def test_confirm_rejects_non_checker(signing_key: rsa.RSAPrivateKey) -> None:
    repository = FakeConditionLedgerRepository()
    orchestration = FakeOrchestrationRepository()
    client = _build_client(
        signing_key, repository=repository, orchestration_repository=orchestration
    )

    response = client.post(f"{_url()}/confirm", headers=_officer(signing_key))

    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_ROLE"
    assert orchestration.ensure_gate_calls == []


def test_list_reports_conditions_and_confirmable_flag(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    repository = FakeConditionLedgerRepository()
    client = _build_client(signing_key, repository=repository)
    _create_condition(client, signing_key)

    response = client.get(_url(), headers=_officer(signing_key))

    assert response.status_code == 200
    body = response.json()
    assert len(body["conditions"]) == 1
    assert body["caseVersion"] == 1
    assert body["confirmable"] is False
