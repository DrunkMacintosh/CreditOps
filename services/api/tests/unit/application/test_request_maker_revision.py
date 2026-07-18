"""Unit tests for the ``RequestMakerRevision`` forward path (master design
section 9; section 5 stage 6).

The fake orchestration repository mirrors ``tests/unit/orchestration/
test_advance.py`` (dedupe by idempotency key, immutable gates) extended with
``bump_case_version`` -- which, like the real adapter, bumps the version and
re-issues the intake handoff at the new version (evidence base unchanged).  One
test then drives the REAL ``AdvanceCase`` over the post-bump fake to prove the
loop semantics end to end: fresh makers scheduled on the new version, old tasks
reported superseded, Risk still blocked on the new-version G2.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.ports.orchestration import (
    CreatedTask,
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
    StaleCaseVersionError,
)
from creditops.application.use_cases.request_maker_revision import RequestMakerRevision
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import (
    GateStatus,
    GateType,
    TaskReadiness,
    TaskType,
)
from creditops.domain.tasks import TaskEnvelopeV1

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
DISPOSITION_ID = UUID("70000000-0000-0000-0000-0000000000aa")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TEMPLATE = DependencyTemplate.canonical()


class FakeOrchestrationRepository:
    """In-memory durable state, extended with an optimistic version bump that
    re-issues the intake handoff at the new version."""

    def __init__(
        self, *, has_intake_handoff: bool = True, case_version: int = 1
    ) -> None:
        self.case_version = case_version
        self.has_intake_handoff = has_intake_handoff
        self.tasks_by_key: dict[str, OrchestrationTaskRow] = {}
        self.gates: dict[tuple[int, GateType], GateRecord] = {}
        self.dependencies: list[tuple[UUID, UUID]] = []
        self.proposals: list[dict[str, object]] = []
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.outbox: list[OutboxEventRow] = []
        self.version_bumps: list[dict[str, object]] = []
        self.reissued_handoff_versions: list[int] = []

    def seed_task(
        self,
        *,
        key: str,
        task_type: TaskType,
        case_version: int,
        status: TaskStatus,
    ) -> UUID:
        task_id = uuid4()
        self.tasks_by_key[key] = OrchestrationTaskRow(
            task_id, task_type, case_version, status
        )
        return task_id

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

    async def bump_case_version(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        reason: str,
        disposition_ref: str,
        actor_id: UUID | None = None,
    ) -> int:
        if expected_version != self.case_version:
            raise StaleCaseVersionError("case version moved on before the bump")
        previous = self.case_version
        self.case_version += 1
        # Re-issue the intake handoff at the new version: the evidence base is
        # unchanged, so G1 stays satisfied and the makers can rerun.
        self.has_intake_handoff = True
        self.reissued_handoff_versions.append(self.case_version)
        self.version_bumps.append(
            {
                "case_id": case_id,
                "previous_version": previous,
                "new_version": self.case_version,
                "reason": reason,
                "disposition_ref": disposition_ref,
                "actor_id": actor_id,
            }
        )
        return self.case_version

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


def _revision(repository: FakeOrchestrationRepository) -> RequestMakerRevision:
    return RequestMakerRevision(repository, clock=lambda: NOW)


def _advance(repository: FakeOrchestrationRepository) -> AdvanceCase:
    return AdvanceCase(
        repository,
        OrchestrationPlanner(TEMPLATE, gateway=None),
        template=TEMPLATE,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_bump_is_optimistic_and_a_stale_version_does_nothing() -> None:
    # The disposition was recorded against version 2 but the case already moved
    # to version 3: the bump loses the optimistic race and NOTHING else happens
    # (no re-issue, no kickoff), so the loop never runs on an unobserved version.
    repository = FakeOrchestrationRepository(case_version=3)

    with pytest.raises(StaleCaseVersionError):
        await _revision(repository).execute(
            case_id=CASE_ID,
            expected_version=2,
            disposition_id=DISPOSITION_ID,
            actor_id=OFFICER,
            reason="Can bo sung can cu.",
        )

    assert repository.version_bumps == []
    assert repository.case_version == 3
    assert repository.tasks_by_key == {}
    assert repository.reissued_handoff_versions == []


@pytest.mark.asyncio
async def test_bump_writes_audit_reissues_handoff_and_kicks_off_revise_tick() -> None:
    repository = FakeOrchestrationRepository(case_version=1)

    result = await _revision(repository).execute(
        case_id=CASE_ID,
        expected_version=1,
        disposition_id=DISPOSITION_ID,
        actor_id=OFFICER,
        reason="Can bo sung can cu.",
    )

    assert result.previous_version == 1
    assert result.new_version == 2
    # The version bump carries the reason + disposition provenance (the audit
    # row the real adapter writes in the same transaction).
    assert len(repository.version_bumps) == 1
    bump = repository.version_bumps[0]
    assert bump["new_version"] == 2
    assert bump["reason"] == "Can bo sung can cu."
    assert str(DISPOSITION_ID) in str(bump["disposition_ref"])
    assert bump["actor_id"] == OFFICER
    # The intake handoff is re-issued at the new version so G1 stays satisfied.
    assert repository.reissued_handoff_versions == [2]
    # A REVISE-keyed ORCHESTRATOR_PLAN task is created at the new version.
    assert result.plan_created is True
    revise_keys = [
        key
        for key, row in repository.tasks_by_key.items()
        if row.task_type is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(revise_keys) == 1
    assert f"REVISE:{DISPOSITION_ID}" in revise_keys[0]
    plan_row = repository.tasks_by_key[revise_keys[0]]
    assert plan_row.case_version == 2
    assert plan_row.task_id == result.plan_task_id


@pytest.mark.asyncio
async def test_post_bump_advance_reruns_only_invalidated_nodes() -> None:
    # Prior cycle at version 1: both makers succeeded and Risk had run.  A
    # MAKER_MUST_REVISE bumps to version 2; advancing then reschedules ONLY the
    # invalidated makers on the new version, fences the version-1 tasks as
    # superseded, and keeps Risk blocked on the new-version G2 (fail closed --
    # the gap batch is re-disposition-required after a version bump).
    repository = FakeOrchestrationRepository(case_version=1)
    stale_uw = repository.seed_task(
        key="ORCH:seed:CU",
        task_type=TaskType.CREDIT_UNDERWRITING,
        case_version=1,
        status=TaskStatus.SUCCEEDED,
    )
    stale_legal = repository.seed_task(
        key="ORCH:seed:LC",
        task_type=TaskType.LEGAL_COMPLIANCE_COLLATERAL,
        case_version=1,
        status=TaskStatus.SUCCEEDED,
    )

    await _revision(repository).execute(
        case_id=CASE_ID,
        expected_version=1,
        disposition_id=DISPOSITION_ID,
        actor_id=OFFICER,
        reason="Can bo sung can cu.",
    )
    assert repository.case_version == 2

    result = await _advance(repository).execute(CASE_ID)

    # Fresh makers scheduled on the NEW version (G1 satisfied by the re-issued
    # handoff); Risk and Credit Operations are not scheduled.
    scheduled = {
        repository.tasks_by_key[key].task_type
        for key in (
            f"ORCH:{CASE_ID}:2:{TaskType.CREDIT_UNDERWRITING.value}",
            f"ORCH:{CASE_ID}:2:{TaskType.LEGAL_COMPLIANCE_COLLATERAL.value}",
        )
    }
    assert scheduled == {
        TaskType.CREDIT_UNDERWRITING,
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    }
    assert all(
        row.case_version == 2
        for row in repository.tasks_by_key.values()
        if row.task_type
        in (TaskType.CREDIT_UNDERWRITING, TaskType.LEGAL_COMPLIANCE_COLLATERAL)
        and row.status is TaskStatus.PENDING
    )
    # The version-1 tasks are reported superseded (stale-task fencing).
    assert str(stale_uw) in result.superseded_task_ids
    assert str(stale_legal) in result.superseded_task_ids
    # Risk stays blocked on the new-version G2; Credit Operations never opens.
    assert (
        result.readiness.by_type(TaskType.INDEPENDENT_RISK_REVIEW).readiness
        is TaskReadiness.BLOCKED
    )
    assert (
        result.readiness.by_type(TaskType.CREDIT_OPERATIONS).readiness
        is TaskReadiness.BLOCKED
    )
    assert repository.gates[(2, GateType.G2_GAP_REQUEST_APPROVAL)].status is GateStatus.OPEN
