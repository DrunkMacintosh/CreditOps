"""End-to-end synthetic-workflow integration test (master design section 21.2
"complete clean case" + 21.3 non-negotiable acceptance).

This module drives the REAL application layer -- ``CompleteIntake``,
``KickoffOrchestration``, ``DispatchOutbox``, ``worker.main.run_once`` (with the
real ``maybe_retick_after_success``), the real ``ProcessorRegistry`` holding the
real ``OrchestratorPlanProcessor`` wired to the real ``AdvanceCase`` over the
real ``OrchestrationPlanner``/gates/readiness/graph, plus the real gap-request
assembler and the real ``derive_g2_from_batch`` domain rule -- against in-memory
fakes ONLY at the infrastructure ports (no HTTP, no Postgres, no live queue).

The fakes below stand in for exactly three durable seams that in production are
all backed by the same Postgres/Supabase tables:

* ``_Backend`` -- the single in-memory "database" shared by the task table (read
  by both the orchestration repo and the worker's task repo, mirroring the one
  ``processing_tasks`` table) and the handoff-existence flag (read by both the
  intake repo and the orchestration repo).  Every fake below is a thin adapter
  over this backend, so a task created through the orchestration port is
  immediately claimable through the worker's task port -- exactly as the two
  Postgres adapters share one table.
* ``_FakeQueue`` -- an in-memory Supabase-queue double (FIFO, at-least-once,
  visibility flag).
* the fake processors for the maker task types persist nothing but succeed (the
  real maker processors require a benchmark-gated FPT route, which is out of
  scope here); the ORCHESTRATOR_PLAN processor is the REAL one.

Contract fidelity the fakes preserve (per the task brief):

* ``create_task`` deduplicates on the idempotency key AND commits a TASK_READY
  outbox row atomically (mirroring the Postgres outbox);
* ``ensure_gate`` is insert-if-absent-then-satisfy-only-if-OPEN, so a gate is
  immutable once SATISFIED and a satisfy transition is recorded exactly once;
* the worker task repo honours claim/checkpoint/succeed and the single global
  worker slot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from creditops.application.gaps.assembler import assemble_gap_request_batch
from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.gates import (
    INTAKE_DISPOSITION_REF,
    derive_effective_gates,
)
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    OrchestratorPlanProcessor,
    ProcessorRegistry,
)
from creditops.application.ports.intake import (
    CurrentHandoff,
    IntakeAuditEvent,
    IntakeEvidenceView,
    PersistedHandoff,
)
from creditops.application.ports.orchestration import (
    AuditEventRow,
    CreatedTask,
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.ports.queue import (
    QueueMessage,
    RetryDecision,
    TaskCheckpoint,
    TaskRecord,
)
from creditops.application.use_cases.complete_intake import CompleteIntake
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.application.use_cases.run_worker_once import StageResult, WorkerOutcome
from creditops.domain.enums import FactDisposition, TaskStatus
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    FactConfirmation,
    PageRegion,
)
from creditops.domain.gap_request_batches import (
    BatchDispositionType,
    GapRequestBatchDisposition,
    assert_disposition_matches_batch,
    compute_open_gap_snapshot_hash,
    derive_g2_from_batch,
)
from creditops.domain.handoffs import HANDOFF_READY_STATE, HandoffArtifact
from creditops.domain.orchestration import (
    GateStatus,
    GateType,
    TaskReadiness,
    TaskType,
)
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.worker.main import run_once as worker_run_once

# --- Dữ liệu tổng hợp (synthetic) cho công ty demo -----------------------------
# Toàn bộ kịch bản chạy ở case version 1 của một hồ sơ tín dụng KHDN giả lập.
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
CASE_ID = UUID("a0000000-0000-0000-0000-000000000001")
CASE_VERSION = 1
OFFICER_ID = UUID("b0000000-0000-0000-0000-000000000001")  # cán bộ tiếp nhận
DOC_VERSION_ID = UUID("c0000000-0000-0000-0000-000000000001")
COMPANY_NAME_VI = "Cong ty TNHH Thuc Pham Sach Demo"
TEMPLATE = DependencyTemplate.canonical()


# =============================================================================
# In-memory infrastructure fakes (the only fakes; everything above the port is
# the real application/domain code).
# =============================================================================


@dataclass
class _StoredTask:
    """One row of the shared in-memory ``processing_tasks`` table.

    Exposes both the orchestration-side view (``OrchestrationTaskRow``) and the
    worker-side view (``TaskRecord``) so a task created through the
    orchestration port is claimable through the worker port -- the two Postgres
    adapters share exactly this one table.
    """

    task_id: UUID
    task_type: TaskType
    case_id: UUID
    case_version: int
    idempotency_key: str
    status: TaskStatus
    input_payload: Mapping[str, object]
    depends_on: tuple[UUID, ...] = ()
    document_version_id: UUID | None = None
    lease_token: UUID | None = None
    lease_until: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    available_at: datetime = NOW
    checkpoints: list[TaskCheckpoint] = field(default_factory=list)

    def to_orchestration_row(self) -> OrchestrationTaskRow:
        return OrchestrationTaskRow(
            self.task_id, self.task_type, self.case_version, self.status
        )

    def to_task_record(self) -> TaskRecord:
        return TaskRecord(
            id=self.task_id,
            case_id=self.case_id,
            case_version=self.case_version,
            document_version_id=self.document_version_id,
            status=self.status,
            attempt_count=self.attempt_count,
            max_attempts=self.max_attempts,
            available_at=self.available_at,
            lease_token=self.lease_token,
            lease_until=self.lease_until,
            input_schema_version="1",
            input_payload=self.input_payload,
            idempotency_key=self.idempotency_key,
            task_type=self.task_type,
        )


class _Backend:
    """The single shared in-memory durable store (one "database")."""

    def __init__(self) -> None:
        # Task table shared by the orchestration repo and the worker task repo.
        self.tasks: dict[UUID, _StoredTask] = {}
        self.task_id_by_key: dict[str, UUID] = {}
        # Human gates (immutable once SATISFIED) + the append-only record of
        # every OPEN->SATISFIED flip and the disposition_ref that caused it.
        self.gates: dict[tuple[int, GateType], GateRecord] = {}
        self.satisfy_transitions: list[tuple[GateType, str | None]] = []
        # Transactional outbox + audit/proposal trails.
        self.outbox: list[OutboxEventRow] = []
        self.orchestration_audit: list[OrchestrationAuditEvent] = []
        self.intake_audit: list[IntakeAuditEvent] = []
        self.proposals: list[dict[str, object]] = []
        # Handoff-existence flag shared by the intake repo and the orch repo.
        self.handoff_by_case: dict[UUID, CurrentHandoff] = {}
        # Single global worker slot.
        self.slot_owner: UUID | None = None


class _FakeIntakeRepository:
    """In-memory ``IntakeRepository`` over one confirmed synthetic fact set."""

    def __init__(self, backend: _Backend, view: IntakeEvidenceView) -> None:
        self._backend = backend
        self._view = view
        self._handoffs: dict[tuple[UUID, int], HandoffArtifact] = {}

    async def load_intake_evidence(
        self, case_id: UUID, case_version: int
    ) -> IntakeEvidenceView:
        assert case_id == CASE_ID and case_version == CASE_VERSION
        return self._view

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None:
        existing = self._backend.handoff_by_case.get(case_id)
        if existing is not None and existing.case_version == case_version:
            return existing
        return None

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool:
        return await self.load_current_handoff(case_id, case_version) is not None

    async def persist_handoff(
        self, handoff: HandoffArtifact, *, actor_id: UUID
    ) -> PersistedHandoff:
        del actor_id
        key = (handoff.case_id, handoff.case_version)
        existing = self._handoffs.get(key)
        if existing is not None:
            return PersistedHandoff(handoff_id=existing.id, created=False)
        self._handoffs[key] = handoff
        self._backend.handoff_by_case[handoff.case_id] = CurrentHandoff(
            id=handoff.id,
            case_id=handoff.case_id,
            case_version=handoff.case_version,
            state=handoff.state,
            created_at=NOW,
        )
        return PersistedHandoff(handoff_id=handoff.id, created=True)

    async def append_audit(self, event: IntakeAuditEvent) -> None:
        self._backend.intake_audit.append(event)


class _FakeOrchestrationRepository:
    """In-memory ``OrchestrationRepository``: dedupe by idempotency key,
    atomic TASK_READY outbox on create, immutable gates."""

    def __init__(self, backend: _Backend) -> None:
        self._backend = backend

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        tasks = tuple(
            stored.to_orchestration_row()
            for stored in self._backend.tasks.values()
            if stored.case_id == case_id
        )
        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=CASE_VERSION,
            has_intake_handoff=case_id in self._backend.handoff_by_case,
            tasks=tasks,
            gates=tuple(self._backend.gates.values()),
            blocking_gaps=(),  # kịch bản sạch: không có gap chặn
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
        existing = self._backend.gates.get(key)
        if existing is None:
            existing = GateRecord(gate_type, case_version, GateStatus.OPEN)
            self._backend.gates[key] = existing
        # Immutable once SATISFIED: only OPEN -> SATISFIED flips, and that flip
        # is recorded exactly once with the disposition_ref that caused it.
        if existing.status is GateStatus.OPEN and status is GateStatus.SATISFIED:
            existing = GateRecord(
                gate_type,
                case_version,
                GateStatus.SATISFIED,
                satisfied_by_actor_id=satisfied_by_actor_id,
                disposition_ref=disposition_ref,
                satisfied_at=NOW,
            )
            self._backend.gates[key] = existing
            self._backend.satisfy_transitions.append((gate_type, disposition_ref))
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
        existing_id = self._backend.task_id_by_key.get(idempotency_key)
        if existing_id is not None:
            return CreatedTask(
                row=self._backend.tasks[existing_id].to_orchestration_row(),
                created=False,
            )
        stored = _StoredTask(
            task_id=task_id,
            task_type=task_type,
            case_id=case_id,
            case_version=case_version,
            idempotency_key=idempotency_key,
            status=TaskStatus.PENDING,
            input_payload=dict(input_payload),
            depends_on=depends_on,
        )
        self._backend.tasks[task_id] = stored
        self._backend.task_id_by_key[idempotency_key] = task_id
        # Mirrors the Postgres adapter: the TASK_READY outbox event commits
        # atomically with the created task row (never a document body/secret).
        envelope = TaskEnvelopeV1(
            task_id=task_id,
            case_id=case_id,
            case_version=case_version,
            task_type=task_type,
            document_version_id=None,
        )
        self._backend.outbox.append(
            OutboxEventRow(
                event_id=uuid4(),
                case_id=case_id,
                case_version=case_version,
                event_type="TASK_READY",
                payload=envelope.model_dump(mode="json"),
            )
        )
        return CreatedTask(row=stored.to_orchestration_row(), created=True)

    async def record_proposal(self, **kwargs: object) -> None:
        self._backend.proposals.append(dict(kwargs))

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self._backend.orchestration_audit.append(event)

    async def load_undispatched_outbox(
        self, *, limit: int
    ) -> tuple[OutboxEventRow, ...]:
        return tuple(
            event for event in self._backend.outbox if event.dispatched_at is None
        )[:limit]

    async def mark_outbox_dispatched(self, event_id: UUID) -> None:
        from dataclasses import replace

        for index, event in enumerate(self._backend.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self._backend.outbox[index] = replace(event, dispatched_at=NOW)

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None:
        from dataclasses import replace

        for index, event in enumerate(self._backend.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self._backend.outbox[index] = replace(
                    event, dispatch_attempts=event.dispatch_attempts + 1
                )

    async def list_audit_events(
        self, case_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]:
        del case_id, cursor, limit
        return ((), None)


class _FakeTaskRepository:
    """In-memory ``TaskRepository`` (worker side) over the shared task table."""

    def __init__(self, backend: _Backend) -> None:
        self._backend = backend

    async def acquire_worker_slot(
        self, *, lease_owner: UUID, lease_token: UUID, lease_until: datetime
    ) -> bool:
        del lease_token, lease_until
        if self._backend.slot_owner is not None:
            return False
        self._backend.slot_owner = lease_owner
        return True

    async def release_worker_slot(self, *, lease_owner: UUID, lease_token: UUID) -> None:
        del lease_owner, lease_token
        self._backend.slot_owner = None

    # NB: intentionally NO ``extend_worker_slot`` -- RunWorkerOnce only calls the
    # heartbeat slot-extension when the concrete adapter defines it in its own
    # class dict, so omitting it keeps the fake on the Protocol's optional path.

    async def reclaim_stranded(self, *, now: datetime) -> tuple[UUID, ...]:
        del now
        return ()  # kịch bản sạch: không có task RUNNING quá hạn lease

    async def claim(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        lease_until: datetime,
    ) -> TaskRecord | None:
        del case_id, case_version, document_version_id
        stored = self._backend.tasks.get(task_id)
        if stored is None or stored.status not in (
            TaskStatus.PENDING,
            TaskStatus.RETRY_WAIT,
        ):
            return None
        stored.status = TaskStatus.RUNNING
        stored.lease_token = lease_token
        stored.lease_until = lease_until
        return stored.to_task_record()

    async def get(
        self, task_id: UUID, *, case_id: UUID | None = None, actor_id: UUID | None = None
    ) -> TaskRecord | None:
        del case_id, actor_id
        stored = self._backend.tasks.get(task_id)
        return stored.to_task_record() if stored is not None else None

    async def latest_checkpoint(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
    ) -> TaskCheckpoint | None:
        del case_id, case_version, document_version_id
        stored = self._backend.tasks.get(task_id)
        if stored is None or not stored.checkpoints:
            return None
        return stored.checkpoints[-1]

    async def checkpoint(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        sequence_no: int,
        checkpoint_type: str,
        checkpoint_schema_version: str,
        checkpoint_data: Mapping[str, object],
    ) -> TaskCheckpoint:
        del lease_token
        checkpoint = TaskCheckpoint(
            task_id=task_id,
            case_id=case_id,
            case_version=case_version,
            document_version_id=document_version_id,
            sequence_no=sequence_no,
            checkpoint_type=checkpoint_type,
            checkpoint_schema_version=checkpoint_schema_version,
            checkpoint_data=dict(checkpoint_data),
            created_at=NOW,
        )
        self._backend.tasks[task_id].checkpoints.append(checkpoint)
        return checkpoint

    async def succeed(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
    ) -> None:
        del case_id, case_version, document_version_id, lease_token
        stored = self._backend.tasks[task_id]
        stored.status = TaskStatus.SUCCEEDED
        stored.lease_token = None

    async def mark_superseded(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        reason: str,
    ) -> None:
        del case_id, case_version, document_version_id, lease_token, reason
        self._backend.tasks[task_id].status = TaskStatus.SUPERSEDED

    async def retry_or_fail(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        reason: str,
        now: datetime,
        base_delay_seconds: int,
    ) -> RetryDecision:
        del case_id, case_version, document_version_id, lease_token
        stored = self._backend.tasks[task_id]
        stored.attempt_count += 1
        stored.status = TaskStatus.RETRY_WAIT
        return RetryDecision(
            TaskStatus.RETRY_WAIT,
            stored.attempt_count,
            now + timedelta(seconds=base_delay_seconds),
            reason,
        )


@dataclass
class _Msg:
    message_id: int
    envelope: TaskEnvelopeV1
    visible: bool = True
    archived: bool = False


class _FakeQueue:
    """In-memory Supabase-queue double: FIFO, single-reader, visibility flag."""

    def __init__(self) -> None:
        self._messages: list[_Msg] = []
        self._counter = 0
        self.sent: list[TaskEnvelopeV1] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del delay_seconds
        self._counter += 1
        self._messages.append(_Msg(self._counter, envelope))
        self.sent.append(envelope)
        return self._counter

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None:
        del visibility_timeout_seconds
        for msg in self._messages:
            if msg.visible and not msg.archived:
                msg.visible = False  # in-flight until archived
                return QueueMessage(msg.message_id, 1, NOW, NOW, msg.envelope)
        return None

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        for msg in self._messages:
            if msg.message_id == message_id:
                msg.archived = True

    def sent_types(self) -> list[TaskType]:
        return [envelope.task_type for envelope in self.sent]


class _SucceedingMakerProcessor:
    """A maker (CREDIT_UNDERWRITING / LEGAL_COMPLIANCE_COLLATERAL) processor that
    persists nothing but reports success -- the real specialist processors need a
    benchmark-gated FPT route, which is out of scope for this clean-case test.
    The worker's durable ``succeed`` transition is the only effect."""

    def __init__(self) -> None:
        self.calls = 0

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: object,
    ) -> StageResult:
        del task, checkpoint, save_checkpoint
        self.calls += 1
        return StageResult()  # WorkerOutcome.SUCCEEDED, persists nothing


# =============================================================================
# Synthetic intake evidence builders (real domain models).
# =============================================================================


def _authority() -> ConfirmationAuthority:
    return ConfirmationAuthority(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        actor_id=OFFICER_ID,
        assigned_officer_id=OFFICER_ID,
        granted_at=NOW,
        source="intake-confirmation",
    )


def _confirmed_fact(field_key: str, value: str, page: int) -> tuple[
    CandidateFact, FactConfirmation, ConfirmedFact
]:
    """Build one ACCEPTED (candidate -> confirmation -> confirmed fact) triple.

    Mỗi Candidate Fact được cán bộ tiếp nhận xác nhận đúng một lần (ACCEPTED),
    tạo ra đúng một Confirmed Fact khớp toàn bộ trường/nguồn/thẩm quyền -- điều
    kiện cần để ``HandoffArtifact`` được coi là đủ điều kiện bàn giao.
    """

    region = PageRegion(page=page, x=0.1, y=0.1, width=0.5, height=0.05)
    candidate = CandidateFact(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        document_version_id=DOC_VERSION_ID,
        field_key=field_key,
        proposed_value=value,
        confidence=0.97,
        source=region,
    )
    confirmation = FactConfirmation(
        id=uuid4(),
        candidate_id=candidate.id,
        disposition=FactDisposition.ACCEPTED,
        authority=_authority(),
        confirmed_at=NOW,
    )
    confirmed = ConfirmedFact.from_confirmation(
        id=uuid4(), candidate=candidate, confirmation=confirmation
    )
    return candidate, confirmation, confirmed


def _intake_view() -> IntakeEvidenceView:
    # Hai dữ kiện tổng hợp của công ty demo: tên doanh nghiệp và mã số thuế.
    name_c, name_conf, name_fact = _confirmed_fact("ten_doanh_nghiep", COMPANY_NAME_VI, 1)
    tax_c, tax_conf, tax_fact = _confirmed_fact("ma_so_thue", "0312345678", 2)
    return IntakeEvidenceView(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        candidates=(name_c, tax_c),
        confirmations=(name_conf, tax_conf),
        confirmed_facts=(name_fact, tax_fact),
        conflict_ids=(),
        gap_ids=(),
    )


# =============================================================================
# Wiring helpers (real use cases over the fakes).
# =============================================================================


def _advance(orchestration: _FakeOrchestrationRepository) -> AdvanceCase:
    return AdvanceCase(
        orchestration,
        OrchestrationPlanner(TEMPLATE, gateway=None),  # deterministic default plan
        template=TEMPLATE,
        clock=lambda: NOW,
    )


def _registry(
    orchestration: _FakeOrchestrationRepository,
    queue: _FakeQueue,
    makers: dict[TaskType, _SucceedingMakerProcessor],
) -> ProcessorRegistry:
    return ProcessorRegistry(
        {
            TaskType.ORCHESTRATOR_PLAN: OrchestratorPlanProcessor(
                _advance(orchestration),
                dispatch=DispatchOutbox(orchestration, queue),
            ),
            TaskType.CREDIT_UNDERWRITING: makers[TaskType.CREDIT_UNDERWRITING],
            TaskType.LEGAL_COMPLIANCE_COLLATERAL: makers[
                TaskType.LEGAL_COMPLIANCE_COLLATERAL
            ],
        },
        fallback=ManualReviewProcessor(orchestration),
    )


def _tasks_by_type(backend: _Backend, task_type: TaskType) -> list[_StoredTask]:
    return [t for t in backend.tasks.values() if t.task_type is task_type]


def _plan_key_for(task_id: UUID) -> str:
    return f"ORCH-PLAN:{CASE_ID}:{CASE_VERSION}:TASK:{task_id}"


# =============================================================================
# The end-to-end clean-case scenario.
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_synthetic_clean_case() -> None:
    backend = _Backend()
    intake = _FakeIntakeRepository(backend, _intake_view())
    orchestration = _FakeOrchestrationRepository(backend)
    queue = _FakeQueue()
    makers = {
        TaskType.CREDIT_UNDERWRITING: _SucceedingMakerProcessor(),
        TaskType.LEGAL_COMPLIANCE_COLLATERAL: _SucceedingMakerProcessor(),
    }
    registry = _registry(orchestration, queue, makers)

    async def process_one_message() -> object:
        """Drive one real worker execution (RunWorkerOnce + the real
        maybe_retick_after_success) over the shared fakes."""
        return await worker_run_once(
            tasks=_FakeTaskRepository(backend),
            queue=queue,
            processor=registry,
            orchestration=orchestration,
            agent_queue=queue,
        )

    # -- PHASE 1: intake completion -> immutable handoff -> G1 derivable --------
    # Cán bộ tiếp nhận hoàn tất intake trên bằng chứng đã xác nhận đầy đủ.
    complete_intake = CompleteIntake(intake, orchestration)
    intake_result = await complete_intake.execute(CASE_ID, CASE_VERSION, OFFICER_ID)

    assert intake_result.created is True, "the first intake completion must create a handoff"
    assert intake_result.state == HANDOFF_READY_STATE, (
        "the handoff must be READY_FOR_SPECIALIST_REVIEW"
    )
    handoff_id = intake_result.handoff_id

    # G1 is derivable from the persisted handoff (real gates derivation).
    snapshot = await orchestration.load_snapshot(CASE_ID)
    assert snapshot is not None and snapshot.has_intake_handoff is True
    effective_gates = derive_effective_gates(snapshot)
    assert effective_gates[GateType.G1_INTAKE_COMPLETE].status is GateStatus.SATISFIED, (
        "G1_INTAKE_COMPLETE must be derivable as SATISFIED from the intake handoff"
    )

    # The HANDOFF kickoff created exactly one ORCHESTRATOR_PLAN task + one outbox row.
    plan_tasks = _tasks_by_type(backend, TaskType.ORCHESTRATOR_PLAN)
    assert len(plan_tasks) == 1, "intake handoff must kick off exactly one plan task"
    assert plan_tasks[0].idempotency_key == (
        f"ORCH-PLAN:{CASE_ID}:{CASE_VERSION}:HANDOFF:{handoff_id}"
    )
    assert len(backend.outbox) == 1, "the plan task must be outboxed atomically on create"
    assert backend.outbox[0].event_type == "TASK_READY"

    # Idempotency: a repeat completion is a no-op (created=False, no new task/outbox).
    repeat = await complete_intake.execute(CASE_ID, CASE_VERSION, OFFICER_ID)
    assert repeat.created is False, "a duplicate intake completion must not create a second handoff"
    assert repeat.handoff_id == handoff_id
    assert len(_tasks_by_type(backend, TaskType.ORCHESTRATOR_PLAN)) == 1
    assert len(backend.outbox) == 1

    # Idempotency: a duplicate kickoff with the same trigger creates no second task.
    dup_kickoff = await KickoffOrchestration(orchestration).execute(
        CASE_ID, trigger_ref=f"HANDOFF:{handoff_id}"
    )
    assert dup_kickoff.created is False, "a duplicate kickoff must not create a second plan task"
    assert len(_tasks_by_type(backend, TaskType.ORCHESTRATOR_PLAN)) == 1

    # -- PHASE 2: dispatch outbox -> worker runs the real plan -> maker tasks ---
    first_dispatch = await DispatchOutbox(orchestration, queue).run()
    assert first_dispatch.dispatched == 1, "the plan task's TASK_READY event must be published once"
    # Duplicate dispatch publishes nothing new (the event is already dispatched).
    second_dispatch = await DispatchOutbox(orchestration, queue).run()
    assert second_dispatch.dispatched == 0, "a duplicate dispatch must not re-publish the event"
    assert len(queue.sent) == 1, "duplicate dispatch must not enqueue a duplicate message"

    plan_result = await process_one_message()
    assert plan_result.outcome is WorkerOutcome.SUCCEEDED, "the plan task must succeed"
    assert plan_result.task_type is TaskType.ORCHESTRATOR_PLAN
    assert plan_tasks[0].status is TaskStatus.SUCCEEDED, (
        "the plan task row must be durably SUCCEEDED"
    )

    # The real AdvanceCase created EXACTLY the two parallel maker task types.
    specialist_types = {
        t.task_type
        for t in backend.tasks.values()
        if t.task_type
        in {
            TaskType.CREDIT_UNDERWRITING,
            TaskType.LEGAL_COMPLIANCE_COLLATERAL,
            TaskType.INDEPENDENT_RISK_REVIEW,
            TaskType.CREDIT_OPERATIONS,
        }
    }
    assert specialist_types == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }, "advance must schedule ONLY the two G1-gated makers, in parallel"
    assert set(queue.sent_types()) >= {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }, "both maker tasks must be outboxed and published"

    underwriting_id = _tasks_by_type(backend, TaskType.CREDIT_UNDERWRITING)[0].task_id
    legal_id = _tasks_by_type(backend, TaskType.LEGAL_COMPLIANCE_COLLATERAL)[0].task_id

    # -- PHASE 3: makers SUCCEED -> task-success retick -> Risk still BLOCKED ---
    cu_result = await process_one_message()
    assert cu_result.outcome is WorkerOutcome.SUCCEEDED
    assert cu_result.task_type is TaskType.CREDIT_UNDERWRITING
    # worker.main.maybe_retick_after_success created a TASK:{id} plan task.
    assert _plan_key_for(underwriting_id) in backend.task_id_by_key, (
        "a maker success must re-tick the case via a TASK:{id} plan task"
    )

    lc_result = await process_one_message()
    assert lc_result.outcome is WorkerOutcome.SUCCEEDED
    assert lc_result.task_type is TaskType.LEGAL_COMPLIANCE_COLLATERAL
    assert _plan_key_for(legal_id) in backend.task_id_by_key, (
        "the second maker success must also re-tick via a TASK:{id} plan task"
    )
    assert makers[TaskType.CREDIT_UNDERWRITING].calls == 1
    assert makers[TaskType.LEGAL_COMPLIANCE_COLLATERAL].calls == 1

    # With both makers COMPLETE but G2 still OPEN, Independent Risk Review is
    # BLOCKED and the stall is surfaced (never silent).  advance is idempotent
    # here: it schedules nothing.
    blocked = await _advance(orchestration).execute(CASE_ID)
    risk_assessment = blocked.readiness.by_type(TaskType.INDEPENDENT_RISK_REVIEW)
    assert risk_assessment.readiness is TaskReadiness.BLOCKED, (
        "Independent Risk Review must stay BLOCKED while G2 is OPEN"
    )
    assert "G2_GAP_REQUEST_APPROVAL" in risk_assessment.reason, (
        "the block reason must name the unsatisfied G2 gate"
    )
    assert blocked.created_task_ids == (), "no task may be scheduled while Risk is blocked"
    assert blocked.deadlock is not None, "the gate-blocked stall must be surfaced as a deadlock"
    assert any(
        "G2_GAP_REQUEST_APPROVAL" in reason for reason in blocked.deadlock.reasons
    )
    assert not _tasks_by_type(backend, TaskType.INDEPENDENT_RISK_REVIEW), (
        "no Independent Risk Review task may exist yet"
    )

    # -- PHASE 4: empty gap batch -> NO_OUTBOUND_REQUESTS -> G2 -> Risk READY ---
    # Deterministic assembler over ZERO open gaps -> an empty (but valid) batch.
    open_gaps: tuple[object, ...] = ()
    batch = assemble_gap_request_batch(
        open_gaps, case_id=CASE_ID, case_version=CASE_VERSION
    )
    assert batch.items == (), "an empty open-gap set must assemble an empty batch"

    # Đúng một quyết định của con người: không cần gửi yêu cầu ra ngoài.
    disposition = GapRequestBatchDisposition(
        id=uuid4(),
        batch_id=batch.id,
        disposition_type=BatchDispositionType.NO_OUTBOUND_REQUESTS,
        actor_id=OFFICER_ID,
        actor_role="INTAKE_OFFICER",
        rationale_vi="Khong co khoang trong bang chung nao can gui yeu cau ra ngoai.",
    )
    assert_disposition_matches_batch(batch=batch, disposition=disposition)

    current_hash = compute_open_gap_snapshot_hash(open_gaps)
    g2_status = derive_g2_from_batch(
        batch=batch,
        disposition=disposition,
        current_case_version=CASE_VERSION,
        current_open_gap_hash=current_hash,
    )
    assert g2_status is GateStatus.SATISFIED, (
        "NO_OUTBOUND_REQUESTS on a still-current empty batch must derive G2 SATISFIED"
    )

    # The human-facing writer (api/gap_requests.py::_satisfy_g2) records G2.
    await orchestration.ensure_gate(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        gate_type=GateType.G2_GAP_REQUEST_APPROVAL,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=OFFICER_ID,
        disposition_ref=f"gap-request-batch:{batch.id}",
    )

    # Re-tick: the real deterministic engine now makes Risk READY and schedules
    # its task -- with ZERO credit-ops packages anywhere (the broken cycle).
    reticked = await _advance(orchestration).execute(CASE_ID)
    risk_ready = reticked.readiness.by_type(TaskType.INDEPENDENT_RISK_REVIEW)
    assert risk_ready.readiness is TaskReadiness.READY, (
        "a satisfied G2 must make Independent Risk Review READY"
    )
    risk_tasks = _tasks_by_type(backend, TaskType.INDEPENDENT_RISK_REVIEW)
    assert len(risk_tasks) == 1, "the re-tick must schedule exactly one Risk Review task"
    assert risk_tasks[0].task_id in reticked.created_task_ids
    assert reticked.deadlock is None, "the case must no longer be stalled once G2 is satisfied"

    # THE regression assertion: Risk became ready WITHOUT any credit-ops package.
    assert not _tasks_by_type(backend, TaskType.CREDIT_OPERATIONS), (
        "ZERO credit-ops packages/tasks may exist -- Risk must not wait on Credit Operations"
    )
    creditops_ready = reticked.readiness.by_type(TaskType.CREDIT_OPERATIONS)
    assert creditops_ready.readiness is TaskReadiness.BLOCKED, (
        "Credit Operations must stay BLOCKED behind the OPEN G3 gate"
    )
    assert "G3_RISK_DISPOSITION" in creditops_ready.reason

    # The freshly scheduled Risk task publishes through the outbox -> queue path.
    await DispatchOutbox(orchestration, queue).run()
    assert TaskType.INDEPENDENT_RISK_REVIEW in queue.sent_types(), (
        "the Risk Review task must be published to the queue"
    )

    # -- PHASE 5: non-negotiable acceptance criteria (master design 21.3) ------
    # (a) No gate was ever SATISFIED by engine code except G1.
    engine_flips = [
        gate_type
        for gate_type, ref in backend.satisfy_transitions
        if ref == INTAKE_DISPOSITION_REF
    ]
    assert engine_flips == [GateType.G1_INTAKE_COMPLETE], (
        "the deterministic engine may satisfy ONLY G1_INTAKE_COMPLETE"
    )
    human_flips = [
        (gate_type, ref)
        for gate_type, ref in backend.satisfy_transitions
        if ref != INTAKE_DISPOSITION_REF
    ]
    assert human_flips == [
        (GateType.G2_GAP_REQUEST_APPROVAL, f"gap-request-batch:{batch.id}")
    ], "the only non-engine gate satisfaction is the human G2 batch disposition"
    satisfied_gate_types = {gate_type for gate_type, _ in backend.satisfy_transitions}
    assert GateType.G3_RISK_DISPOSITION not in satisfied_gate_types
    assert GateType.G4_OPS_AUTHORIZATION not in satisfied_gate_types

    # (b) The outbox never contains a non-envelope payload.
    for event in backend.outbox:
        assert event.event_type == "TASK_READY", "every outbox row must be a TASK_READY event"
        # Raises if the payload is not a valid identifier-only task envelope.
        TaskEnvelopeV1.model_validate(dict(event.payload))

    # (c) Every state-changing command is idempotent -- the whole run produced a
    # deterministic, duplicate-free task set (3 plan ticks, 2 makers, 1 Risk).
    assert len(_tasks_by_type(backend, TaskType.ORCHESTRATOR_PLAN)) == 3, (
        "exactly three plan ticks: HANDOFF kickoff + two TASK:{id} maker reticks"
    )
    assert len(_tasks_by_type(backend, TaskType.CREDIT_UNDERWRITING)) == 1
    assert len(_tasks_by_type(backend, TaskType.LEGAL_COMPLIANCE_COLLATERAL)) == 1
    assert len(_tasks_by_type(backend, TaskType.INDEPENDENT_RISK_REVIEW)) == 1

    # (d) The MAKER_MUST_REVISE / risk-disposition path is out of scope for the
    # clean case: absent any human risk disposition, G3 stays OPEN.
    g3 = backend.gates.get((CASE_VERSION, GateType.G3_RISK_DISPOSITION))
    assert g3 is not None and g3.status is GateStatus.OPEN, (
        "G3_RISK_DISPOSITION must remain OPEN absent a human disposition"
    )
