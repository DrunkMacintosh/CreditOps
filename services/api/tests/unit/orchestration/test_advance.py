from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from creditops.application.orchestration.advance import AdvanceCase, CaseNotFound
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.domain.tasks import TaskEnvelopeV1

NOW = datetime(2026, 7, 18, 6, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
TEMPLATE = DependencyTemplate.canonical()


class FakeOrchestrationRepository:
    """In-memory durable state: dedupe by idempotency key, immutable gates."""

    def __init__(self, *, has_intake_handoff: bool = True, case_version: int = 1) -> None:
        self.case_version = case_version
        self.has_intake_handoff = has_intake_handoff
        self.tasks_by_key: dict[str, OrchestrationTaskRow] = {}
        self.gates: dict[tuple[int, GateType], GateRecord] = {}
        self.dependencies: list[tuple[UUID, UUID]] = []
        self.proposals: list[dict[str, object]] = []
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.outbox: list[OutboxEventRow] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=self.case_version,
            has_intake_handoff=self.has_intake_handoff,
            tasks=tuple(self.tasks_by_key.values()),
            gates=tuple(self.gates.values()),
        )

    async def ensure_gate(
        self,
        *,
        case_id: UUID,
        case_version: int,
        gate_type: GateType,
        status: GateStatus,
        satisfied_by_actor_id: UUID | None = None,
        disposition_ref: str | None = None,
    ) -> GateRecord:
        del case_id
        key = (case_version, gate_type)
        existing = self.gates.get(key)
        if existing is None:
            existing = GateRecord(gate_type, case_version, GateStatus.OPEN)
            self.gates[key] = existing
        if existing.status is GateStatus.OPEN and status is GateStatus.SATISFIED:
            existing = GateRecord(
                gate_type,
                case_version,
                GateStatus.SATISFIED,
                satisfied_by_actor_id=satisfied_by_actor_id,
                disposition_ref=disposition_ref,
                satisfied_at=NOW,
            )
            self.gates[key] = existing
        return existing

    async def create_task(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        task_type: TaskType,
        idempotency_key: str,
        input_payload: Mapping[str, object],
        depends_on: tuple[UUID, ...] = (),
    ) -> CreatedTask:
        del input_payload
        existing = self.tasks_by_key.get(idempotency_key)
        if existing is not None:
            return CreatedTask(row=existing, created=False)
        row = OrchestrationTaskRow(task_id, task_type, case_version, TaskStatus.PENDING)
        self.tasks_by_key[idempotency_key] = row
        self.dependencies.extend((task_id, dependency) for dependency in depends_on)
        # Mirrors the Postgres adapter: the TASK_READY outbox event commits
        # atomically with the created task row.
        envelope = TaskEnvelopeV1(
            task_id=task_id,
            case_id=case_id,
            case_version=case_version,
            task_type=task_type,
            document_version_id=None,
        )
        self.outbox.append(
            OutboxEventRow(
                event_id=uuid4(),
                case_id=case_id,
                case_version=case_version,
                event_type="TASK_READY",
                payload=envelope.model_dump(mode="json"),
            )
        )
        return CreatedTask(row=row, created=True)

    async def record_proposal(self, **kwargs: object) -> None:
        self.proposals.append(dict(kwargs))

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self.audit_events.append(event)

    def append_outbox_for_test(
        self,
        *,
        event_id: UUID,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        self.outbox.append(
            OutboxEventRow(
                event_id=event_id,
                case_id=CASE_ID,
                case_version=self.case_version,
                event_type=event_type,
                payload=payload,
            )
        )

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]:
        return tuple(
            event for event in self.outbox if event.dispatched_at is None
        )[:limit]

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


def advance_case(repository: FakeOrchestrationRepository) -> AdvanceCase:
    return AdvanceCase(
        repository,
        OrchestrationPlanner(TEMPLATE, gateway=None),
        template=TEMPLATE,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_advance_creates_and_outboxes_only_ready_specialist_tasks() -> None:
    repository = FakeOrchestrationRepository()

    result = await advance_case(repository).execute(CASE_ID)

    created_types = {
        row.task_type for row in repository.tasks_by_key.values()
    }
    assert created_types == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }
    outboxed = [
        TaskEnvelopeV1.model_validate(dict(event.payload)) for event in repository.outbox
    ]
    assert {envelope.task_type for envelope in outboxed} == created_types
    assert all(envelope.document_version_id is None for envelope in outboxed)
    assert all(envelope.case_version == 1 for envelope in outboxed)
    # G1 was derived from the intake handoff; the human-only gates stay OPEN.
    assert repository.gates[(1, GateType.G1_INTAKE_COMPLETE)].status is GateStatus.SATISFIED
    for human_gate in (
        GateType.G2_GAP_REQUEST_APPROVAL,
        GateType.G3_RISK_DISPOSITION,
        GateType.G4_OPS_AUTHORIZATION,
    ):
        assert repository.gates[(1, human_gate)].status is GateStatus.OPEN
    assert result.deadlock is None


@pytest.mark.asyncio
async def test_duplicate_advance_produces_no_duplicate_tasks_or_events() -> None:
    repository = FakeOrchestrationRepository()
    use_case = advance_case(repository)

    first = await use_case.execute(CASE_ID)
    second = await use_case.execute(CASE_ID)

    assert len(first.created_task_ids) == 2
    assert second.created_task_ids == ()
    assert second.outboxed_task_ids == ()
    assert len(repository.tasks_by_key) == 2
    assert len(repository.outbox) == 2


@pytest.mark.asyncio
async def test_every_orchestrator_output_carries_full_provenance() -> None:
    repository = FakeOrchestrationRepository()

    result = await advance_case(repository).execute(CASE_ID)

    assert repository.audit_events, "advance must append audit events"
    for event in repository.audit_events:
        assert event.case_id == CASE_ID
        assert event.case_version == 1
        assert event.execution_id == result.execution_id
        assert event.event_data["role"] == "CASE_ORCHESTRATOR"
        assert event.event_data["recordedAt"] == NOW.isoformat()
    event_types = [event.event_type for event in repository.audit_events]
    assert "ORCHESTRATION_PLANNER_PROPOSAL" in event_types
    assert "ORCHESTRATION_ADVANCED" in event_types
    assert len(repository.proposals) == 1
    proposal = repository.proposals[0]
    assert proposal["execution_id"] == result.execution_id
    assert proposal["prompt_version"] == "orchestrator-prompt-v1"
    assert proposal["schema_version"] == "orchestrator-proposal-v1"


@pytest.mark.asyncio
async def test_gate_blocked_stall_is_surfaced_as_a_deadlock_audit_event() -> None:
    repository = FakeOrchestrationRepository()
    # Both makers already succeeded; risk review waits on the OPEN G2 gate, so
    # nothing is ready or running: the stall must be surfaced, never silent.
    repository.tasks_by_key["ORCH:seed:CU"] = OrchestrationTaskRow(
        uuid4(), TaskType.CREDIT_UNDERWRITING, 1, TaskStatus.SUCCEEDED
    )
    repository.tasks_by_key["ORCH:seed:LC"] = OrchestrationTaskRow(
        uuid4(), TaskType.LEGAL_COMPLIANCE_COLLATERAL, 1, TaskStatus.SUCCEEDED
    )

    result = await advance_case(repository).execute(CASE_ID)

    assert result.deadlock is not None
    assert any("G2_GAP_REQUEST_APPROVAL" in reason for reason in result.deadlock.reasons)
    assert repository.outbox == []
    deadlock_events = [
        event
        for event in repository.audit_events
        if event.event_type == "ORCHESTRATION_DEADLOCK"
    ]
    assert len(deadlock_events) == 1
    assert deadlock_events[0].event_data["role"] == "CASE_ORCHESTRATOR"


@pytest.mark.asyncio
async def test_without_an_intake_handoff_no_specialist_work_starts() -> None:
    repository = FakeOrchestrationRepository(has_intake_handoff=False)

    result = await advance_case(repository).execute(CASE_ID)

    assert repository.tasks_by_key == {}
    assert repository.outbox == []
    assert repository.gates[(1, GateType.G1_INTAKE_COMPLETE)].status is GateStatus.OPEN
    assert result.deadlock is not None


@pytest.mark.asyncio
async def test_an_invisible_case_raises_instead_of_guessing() -> None:
    repository = FakeOrchestrationRepository()

    with pytest.raises(CaseNotFound):
        await advance_case(repository).execute(uuid4())
