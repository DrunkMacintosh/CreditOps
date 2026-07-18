"""Task-success re-tick (master design section 9, P0 #6).

After a specialist or document task SUCCEEDS, the worker self-fires an
idempotent orchestration tick so downstream nodes get scheduled without a
manual advance.  A succeeding ORCHESTRATOR_PLAN task never re-ticks (it IS
the tick — re-ticking would chain plan tasks forever), and non-success
outcomes never re-tick.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.orchestration import (
    CreatedTask,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
    OutboxEventRow,
)
from creditops.application.use_cases.run_worker_once import (
    WorkerOutcome,
    WorkerRunResult,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.worker.main import maybe_retick_after_success

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class FakeOrchestration:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.outbox: list[OutboxEventRow] = []
        self.audit_events: list[Any] = []

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        if case_id != CASE_ID:
            return None
        return OrchestrationSnapshot(
            case_id=case_id, case_version=1, has_intake_handoff=True
        )

    async def create_task(self, **kwargs: Any) -> CreatedTask:
        for existing in self.created:
            if existing["idempotency_key"] == kwargs["idempotency_key"]:
                return CreatedTask(
                    row=OrchestrationTaskRow(
                        existing["task_id"], existing["task_type"], 1, TaskStatus.PENDING
                    ),
                    created=False,
                )
        self.created.append(dict(kwargs))
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
                kwargs["task_id"], kwargs["task_type"], 1, TaskStatus.PENDING
            ),
            created=True,
        )

    async def append_audit(self, event: Any) -> None:
        self.audit_events.append(event)

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]:
        return tuple(e for e in self.outbox if e.dispatched_at is None)[:limit]

    async def mark_outbox_dispatched(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = dc_replace(event, dispatched_at=NOW)

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None:
        for index, event in enumerate(self.outbox):
            if event.event_id == event_id and event.dispatched_at is None:
                self.outbox[index] = dc_replace(
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


def _success(task_type: TaskType) -> WorkerRunResult:
    return WorkerRunResult(
        WorkerOutcome.SUCCEEDED,
        uuid4(),
        1,
        case_id=CASE_ID,
        task_type=task_type,
    )


@pytest.mark.asyncio
async def test_specialist_success_reticks_and_dispatches() -> None:
    orchestration = FakeOrchestration()
    queue = RecordingQueue()
    result = _success(TaskType.CREDIT_UNDERWRITING)

    await maybe_retick_after_success(result, orchestration, queue)

    assert len(orchestration.created) == 1
    assert f"TASK:{result.task_id}" in str(orchestration.created[0]["idempotency_key"])
    assert len(queue.sent) == 1
    assert queue.sent[0].task_type is TaskType.ORCHESTRATOR_PLAN


@pytest.mark.asyncio
async def test_plan_task_success_never_reticks_itself() -> None:
    orchestration = FakeOrchestration()
    queue = RecordingQueue()

    await maybe_retick_after_success(
        _success(TaskType.ORCHESTRATOR_PLAN), orchestration, queue
    )

    assert orchestration.created == []
    assert queue.sent == []


@pytest.mark.asyncio
async def test_non_success_outcomes_never_retick() -> None:
    orchestration = FakeOrchestration()
    queue = RecordingQueue()
    result = WorkerRunResult(
        WorkerOutcome.RETRY_WAIT,
        uuid4(),
        1,
        case_id=CASE_ID,
        task_type=TaskType.CREDIT_UNDERWRITING,
    )

    await maybe_retick_after_success(result, orchestration, queue)

    assert orchestration.created == []
    assert queue.sent == []


@pytest.mark.asyncio
async def test_missing_case_context_is_a_no_op() -> None:
    orchestration = FakeOrchestration()
    queue = RecordingQueue()
    result = WorkerRunResult(WorkerOutcome.SUCCEEDED, uuid4(), 1)

    await maybe_retick_after_success(result, orchestration, queue)

    assert orchestration.created == []
