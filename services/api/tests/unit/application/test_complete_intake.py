"""Unit tests for the ``CompleteIntake`` use case.

All evidence here is synthetic and created solely for demonstration.  The
completeness verdict is the domain validator's alone -- these tests prove the
use case surfaces its reasons unchanged, is idempotent, and only audits/kicks
off orchestration when it actually writes a new handoff.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.intake import (
    CurrentHandoff,
    IntakeAuditEvent,
    IntakeEvidenceView,
    PersistedHandoff,
)
from creditops.application.ports.orchestration import (
    CreatedTask,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
)
from creditops.application.use_cases.complete_intake import (
    CompleteIntake,
    IntakeIncompleteError,
)
from creditops.domain.enums import FactDisposition, TaskStatus
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    FactConfirmation,
    PageRegion,
)
from creditops.domain.handoffs import HandoffArtifact
from creditops.domain.orchestration import TaskType

CASE_ID = UUID("10000000-0000-0000-0000-000000000004")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_VERSION = 3
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
GRANTED_AT = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CONFIRMED_AT = GRANTED_AT + timedelta(minutes=5)


def _bundle(
    *, disposition: FactDisposition = FactDisposition.ACCEPTED
) -> tuple[CandidateFact, FactConfirmation, ConfirmedFact]:
    candidate = CandidateFact(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        document_version_id=uuid4(),
        field_key="requested_amount",
        proposed_value="5000000000",
        confidence=0.9,
        source=PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04),
    )
    confirmation = FactConfirmation(
        id=uuid4(),
        candidate_id=candidate.id,
        disposition=disposition,
        authority=ConfirmationAuthority(
            case_id=CASE_ID,
            case_version=CASE_VERSION,
            actor_id=OFFICER,
            assigned_officer_id=OFFICER,
            granted_at=GRANTED_AT,
            source="CASE_ASSIGNMENT",
        ),
        confirmed_at=CONFIRMED_AT,
    )
    fact = ConfirmedFact.from_confirmation(
        id=uuid4(), candidate=candidate, confirmation=confirmation
    )
    return candidate, confirmation, fact


def _complete_view() -> IntakeEvidenceView:
    candidate, confirmation, fact = _bundle()
    return IntakeEvidenceView(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        candidates=(candidate,),
        confirmations=(confirmation,),
        confirmed_facts=(fact,),
    )


def _incomplete_view() -> IntakeEvidenceView:
    # A candidate with no disposition -- the domain validator must reject it.
    candidate, _, _ = _bundle()
    return IntakeEvidenceView(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        candidates=(candidate,),
    )


class FakeIntakeRepository:
    def __init__(
        self,
        *,
        view: IntakeEvidenceView,
        current: CurrentHandoff | None = None,
    ) -> None:
        self._view = view
        self.current = current
        self.persisted: list[HandoffArtifact] = []
        self.persist_actor_ids: list[UUID] = []
        self.audit_events: list[IntakeAuditEvent] = []

    async def load_intake_evidence(
        self, case_id: UUID, case_version: int
    ) -> IntakeEvidenceView:
        return self._view

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None:
        return self.current

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool:
        return self.current is not None

    async def persist_handoff(
        self, handoff: HandoffArtifact, *, actor_id: UUID
    ) -> PersistedHandoff:
        if self.current is not None:
            return PersistedHandoff(handoff_id=self.current.id, created=False)
        self.persisted.append(handoff)
        self.persist_actor_ids.append(actor_id)
        self.current = CurrentHandoff(
            id=handoff.id,
            case_id=handoff.case_id,
            case_version=handoff.case_version,
            state=handoff.state,
            created_at=NOW,
        )
        return PersistedHandoff(handoff_id=handoff.id, created=True)

    async def append_audit(self, event: IntakeAuditEvent) -> None:
        self.audit_events.append(event)


class FakeOrchestrationRepository:
    def __init__(self) -> None:
        self.created_tasks: list[dict[str, Any]] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        return OrchestrationSnapshot(
            case_id=case_id, case_version=CASE_VERSION, has_intake_handoff=True
        )

    async def create_task(self, **kwargs: Any) -> CreatedTask:
        self.created_tasks.append(dict(kwargs))
        return CreatedTask(
            row=OrchestrationTaskRow(
                task_id=kwargs["task_id"],
                task_type=kwargs["task_type"],
                case_version=int(kwargs["case_version"]),
                status=TaskStatus.PENDING,
            ),
            created=True,
        )

    async def append_audit(self, event: Any) -> None:
        self.audit_events.append(event)


@pytest.mark.asyncio
async def test_complete_intake_persists_audits_and_kicks_off() -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    orchestration = FakeOrchestrationRepository()

    result = await CompleteIntake(repository, orchestration).execute(
        CASE_ID, CASE_VERSION, OFFICER
    )

    assert result.created is True
    assert result.case_version == CASE_VERSION
    assert result.state == "READY_FOR_SPECIALIST_REVIEW"
    assert len(repository.persisted) == 1
    assert repository.persist_actor_ids == [OFFICER]
    # A human audit event is written for the created handoff.
    assert len(repository.audit_events) == 1
    audit = repository.audit_events[0]
    assert audit.event_type == "INTAKE_HANDOFF_CREATED"
    assert audit.actor_id == OFFICER
    assert audit.artifact_id == result.handoff_id
    # Orchestration is kicked off with a HANDOFF-scoped idempotency key.
    plan_tasks = [
        call
        for call in orchestration.created_tasks
        if call["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1
    assert f"HANDOFF:{result.handoff_id}" in str(plan_tasks[0]["idempotency_key"])


@pytest.mark.asyncio
async def test_incomplete_evidence_surfaces_domain_reasons_and_persists_nothing() -> None:
    repository = FakeIntakeRepository(view=_incomplete_view())
    orchestration = FakeOrchestrationRepository()

    with pytest.raises(IntakeIncompleteError) as excinfo:
        await CompleteIntake(repository, orchestration).execute(
            CASE_ID, CASE_VERSION, OFFICER
        )

    # The reason comes straight from the domain validator, not a re-implementation.
    assert any("missing confirmation" in reason for reason in excinfo.value.reasons)
    assert repository.persisted == []
    assert repository.audit_events == []
    assert orchestration.created_tasks == []


@pytest.mark.asyncio
async def test_repeat_completion_is_idempotent() -> None:
    repository = FakeIntakeRepository(view=_complete_view())
    orchestration = FakeOrchestrationRepository()
    use_case = CompleteIntake(repository, orchestration)

    first = await use_case.execute(CASE_ID, CASE_VERSION, OFFICER)
    second = await use_case.execute(CASE_ID, CASE_VERSION, OFFICER)

    assert first.created is True
    assert second.created is False
    assert second.handoff_id == first.handoff_id
    assert len(repository.persisted) == 1
    assert len(repository.audit_events) == 1
    # No second kick-off for the idempotent repeat.
    plan_tasks = [
        call
        for call in orchestration.created_tasks
        if call["task_type"] is TaskType.ORCHESTRATOR_PLAN
    ]
    assert len(plan_tasks) == 1


@pytest.mark.asyncio
async def test_existing_handoff_short_circuits_without_loading_evidence() -> None:
    existing = CurrentHandoff(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        state="READY_FOR_SPECIALIST_REVIEW",
        created_at=NOW,
    )
    repository = FakeIntakeRepository(view=_complete_view(), current=existing)

    result = await CompleteIntake(repository, None).execute(
        CASE_ID, CASE_VERSION, OFFICER
    )

    assert result.created is False
    assert result.handoff_id == existing.id
    assert repository.persisted == []
    assert repository.audit_events == []


@pytest.mark.asyncio
async def test_completes_without_orchestration_repository() -> None:
    repository = FakeIntakeRepository(view=_complete_view())

    result = await CompleteIntake(repository, None).execute(
        CASE_ID, CASE_VERSION, OFFICER
    )

    assert result.created is True
    assert len(repository.persisted) == 1
    assert len(repository.audit_events) == 1
