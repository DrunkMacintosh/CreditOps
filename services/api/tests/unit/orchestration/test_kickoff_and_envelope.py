from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_advance import CASE_ID, FakeOrchestrationRepository

from creditops.application.orchestration.kickoff import (
    KickoffCaseNotFound,
    KickoffOrchestration,
)
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1


@pytest.mark.asyncio
async def test_kickoff_outboxes_exactly_one_plan_task_per_case_version() -> None:
    repository = FakeOrchestrationRepository()
    kickoff = KickoffOrchestration(repository)

    first = await kickoff.execute(CASE_ID)
    second = await kickoff.execute(CASE_ID)

    assert first.created is True
    assert second.created is False
    assert second.task_id == first.task_id
    assert len(repository.outbox) == 1
    envelope = TaskEnvelopeV1.model_validate(dict(repository.outbox[0].payload))
    assert envelope.task_type is TaskType.ORCHESTRATOR_PLAN
    assert envelope.document_version_id is None
    kickoff_events = [
        event
        for event in repository.audit_events
        if event.event_type == "ORCHESTRATION_KICKOFF"
    ]
    assert len(kickoff_events) == 1
    assert kickoff_events[0].event_data["role"] == "CASE_ORCHESTRATOR"


@pytest.mark.asyncio
async def test_kickoff_refuses_an_invisible_case() -> None:
    with pytest.raises(KickoffCaseNotFound):
        await KickoffOrchestration(FakeOrchestrationRepository()).execute(uuid4())


@pytest.mark.asyncio
async def test_event_scoped_kickoffs_reticks_once_per_trigger() -> None:
    # The orchestration tick must fire after EVERY task/gate/handoff/evidence
    # event (master design section 9), so a gate satisfied at the same case
    # version still schedules a fresh plan task -- while the same trigger
    # delivered twice stays deduplicated.
    repository = FakeOrchestrationRepository()
    kickoff = KickoffOrchestration(repository)

    base = await kickoff.execute(CASE_ID)
    gate_tick = await kickoff.execute(CASE_ID, trigger_ref="G3:assessment-1")
    duplicate = await kickoff.execute(CASE_ID, trigger_ref="G3:assessment-1")
    other_gate = await kickoff.execute(CASE_ID, trigger_ref="G2:batch-9")

    assert base.created is True
    assert gate_tick.created is True
    assert gate_tick.task_id != base.task_id
    assert duplicate.created is False
    assert duplicate.task_id == gate_tick.task_id
    assert other_gate.created is True
    assert len(repository.outbox) == 3


def test_legacy_envelope_without_task_type_still_parses_as_ingestion() -> None:
    legacy_message = {
        "schema_version": "1",
        "task_id": "30000000-0000-0000-0000-000000000001",
        "case_id": "10000000-0000-0000-0000-000000000001",
        "case_version": 1,
        "document_version_id": "20000000-0000-0000-0000-000000000001",
    }

    envelope = TaskEnvelopeV1.model_validate(legacy_message)

    assert envelope.task_type is TaskType.DOCUMENT_INGESTION
    assert envelope.document_version_id == UUID("20000000-0000-0000-0000-000000000001")
    # Serialisation stays identifier-only.
    assert "document_body" not in envelope.model_dump()


def test_envelope_document_scope_matches_the_database_constraint() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelopeV1(
            task_id=uuid4(),
            case_id=uuid4(),
            case_version=1,
            task_type=TaskType.DOCUMENT_INGESTION,
            document_version_id=None,
        )
    with pytest.raises(ValidationError):
        TaskEnvelopeV1(
            task_id=uuid4(),
            case_id=uuid4(),
            case_version=1,
            task_type=TaskType.CREDIT_UNDERWRITING,
            document_version_id=uuid4(),
        )
