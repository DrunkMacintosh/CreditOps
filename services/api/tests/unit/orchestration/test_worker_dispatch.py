from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from test_advance import FakeOrchestrationRepository

from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    ProcessorRegistry,
)
from creditops.application.ports.queue import (
    QueueMessage,
    RetryDecision,
    TaskCheckpoint,
    TaskRecord,
)
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
CASE = UUID("10000000-0000-0000-0000-000000000001")
TASK = UUID("30000000-0000-0000-0000-000000000001")


def agent_envelope(task_type: TaskType) -> TaskEnvelopeV1:
    return TaskEnvelopeV1(
        task_id=TASK,
        case_id=CASE,
        case_version=1,
        task_type=task_type,
        document_version_id=None,
    )


class SingleMessageQueue:
    def __init__(self, envelope: TaskEnvelopeV1) -> None:
        self.message: QueueMessage | None = QueueMessage(7, 1, NOW, NOW, envelope)
        self.archived: list[int] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del envelope, delay_seconds
        return 8

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None:
        del visibility_timeout_seconds
        return self.message

    async def extend_visibility(self, message_id: int, *, visibility_timeout_seconds: int) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        self.archived.append(message_id)
        self.message = None


class AgentTaskStore:
    def __init__(self, task_type: TaskType) -> None:
        self.record = TaskRecord(
            id=TASK,
            case_id=CASE,
            case_version=1,
            document_version_id=None,
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
            available_at=NOW,
            lease_token=None,
            lease_until=None,
            input_schema_version="1",
            input_payload={},
            idempotency_key="ORCH:agent-task",
            task_type=task_type,
        )
        self.checkpoints: list[TaskCheckpoint] = []
        self.slot_taken = False

    async def acquire_worker_slot(self, **kwargs: object) -> bool:
        del kwargs
        if self.slot_taken:
            return False
        self.slot_taken = True
        return True

    async def release_worker_slot(self, **kwargs: object) -> None:
        del kwargs
        self.slot_taken = False

    async def claim(self, **kwargs: object) -> TaskRecord | None:
        if kwargs["task_id"] != self.record.id:
            return None
        if self.record.status not in (TaskStatus.PENDING, TaskStatus.RETRY_WAIT):
            return None
        token = kwargs["lease_token"]
        assert isinstance(token, UUID)
        self.record = replace(self.record, status=TaskStatus.RUNNING, lease_token=token)
        return self.record

    async def get(
        self,
        task_id: UUID,
        *,
        case_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> TaskRecord | None:
        del case_id, actor_id
        return self.record if task_id == self.record.id else None

    async def latest_checkpoint(self, **kwargs: object) -> TaskCheckpoint | None:
        del kwargs
        return self.checkpoints[-1] if self.checkpoints else None

    async def checkpoint(self, **kwargs: object) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            task_id=kwargs["task_id"],
            case_id=kwargs["case_id"],
            case_version=kwargs["case_version"],
            document_version_id=kwargs["document_version_id"],
            sequence_no=kwargs["sequence_no"],
            checkpoint_type=kwargs["checkpoint_type"],
            checkpoint_schema_version=kwargs["checkpoint_schema_version"],
            checkpoint_data=kwargs["checkpoint_data"],
            created_at=NOW,
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    async def succeed(self, **kwargs: object) -> None:
        del kwargs
        self.record = replace(self.record, status=TaskStatus.SUCCEEDED, lease_token=None)

    async def mark_superseded(self, **kwargs: object) -> None:
        del kwargs
        self.record = replace(self.record, status=TaskStatus.SUPERSEDED, lease_token=None)

    async def retry_or_fail(self, **kwargs: object) -> RetryDecision:
        failed = self.record.attempt_count >= self.record.max_attempts
        status = TaskStatus.FAILED_MANUAL_REVIEW if failed else TaskStatus.RETRY_WAIT
        self.record = replace(self.record, status=status, lease_token=None)
        return RetryDecision(
            status,
            self.record.attempt_count,
            None if failed else NOW + timedelta(seconds=30),
            str(kwargs["reason"]),
        )


class RecordingProcessor:
    def __init__(self) -> None:
        self.seen_types: list[TaskType] = []

    async def process(self, task, checkpoint, save_checkpoint):  # type: ignore[no-untyped-def]
        del checkpoint
        self.seen_types.append(task.task_type)
        await save_checkpoint("HANDLED", {"taskType": task.task_type.value})
        return StageResult()


@pytest.mark.asyncio
async def test_worker_routes_an_agent_envelope_to_its_registered_processor() -> None:
    store = AgentTaskStore(TaskType.ORCHESTRATOR_PLAN)
    queue = SingleMessageQueue(agent_envelope(TaskType.ORCHESTRATOR_PLAN))
    plan_processor = RecordingProcessor()
    ingestion_processor = RecordingProcessor()
    registry = ProcessorRegistry(
        {
            TaskType.DOCUMENT_INGESTION: ingestion_processor,
            TaskType.ORCHESTRATOR_PLAN: plan_processor,
        },
        fallback=ManualReviewProcessor(FakeOrchestrationRepository(), clock=lambda: NOW),
    )
    worker = RunWorkerOnce(store, queue, registry, clock=lambda: NOW)

    result = await worker.run_once()

    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert plan_processor.seen_types == [TaskType.ORCHESTRATOR_PLAN]
    assert ingestion_processor.seen_types == []
    assert store.record.status is TaskStatus.SUCCEEDED
    assert queue.archived == [7]
    # The checkpoint path still works for document-less agent tasks.
    assert store.checkpoints[0].document_version_id is None


@pytest.mark.asyncio
async def test_unregistered_task_type_fails_closed_to_manual_review() -> None:
    store = AgentTaskStore(TaskType.CREDIT_OPERATIONS)
    # Exhausted attempts: retry_or_fail resolves straight to manual review.
    store.record = replace(store.record, attempt_count=3)
    queue = SingleMessageQueue(agent_envelope(TaskType.CREDIT_OPERATIONS))
    repository = FakeOrchestrationRepository()
    registry = ProcessorRegistry(
        {TaskType.ORCHESTRATOR_PLAN: RecordingProcessor()},
        fallback=ManualReviewProcessor(repository, clock=lambda: NOW),
    )
    worker = RunWorkerOnce(store, queue, registry, clock=lambda: NOW)

    result = await worker.run_once()

    assert result.outcome == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert store.record.status is TaskStatus.FAILED_MANUAL_REVIEW
    assert queue.archived == [7]
    assert any(
        event.event_type == "ORCHESTRATION_UNKNOWN_TASK_TYPE"
        for event in repository.audit_events
    )
