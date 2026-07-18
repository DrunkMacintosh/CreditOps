from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from creditops.application.ports.queue import (
    QueueMessage,
    RetryDecision,
    TaskCheckpoint,
    TaskRecord,
    TaskRepository,
)
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.tasks import TaskEnvelopeV1

NOW = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
DOC_ID = UUID("20000000-0000-0000-0000-000000000001")
TASK_ID = UUID("30000000-0000-0000-0000-000000000001")


def envelope() -> TaskEnvelopeV1:
    return TaskEnvelopeV1(
        task_id=TASK_ID,
        case_id=CASE_ID,
        case_version=1,
        document_version_id=DOC_ID,
    )


def task() -> TaskRecord:
    return TaskRecord(
        id=TASK_ID,
        case_id=CASE_ID,
        case_version=1,
        document_version_id=DOC_ID,
        status=TaskStatus.PENDING,
        attempt_count=0,
        max_attempts=2,
        available_at=NOW,
        lease_token=None,
        lease_until=None,
        input_schema_version="1",
        input_payload={"storageBucket": "creditops-originals"},
        idempotency_key="UPLOAD:task-1",
    )


class FakeQueue:
    def __init__(self) -> None:
        self.messages = [
            QueueMessage(1, 1, NOW, NOW, envelope()),
        ]
        self.archived: list[int] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del envelope, delay_seconds
        return 2

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None:
        del visibility_timeout_seconds
        await asyncio.sleep(0)
        return self.messages.pop(0) if self.messages else None

    async def extend_visibility(self, message_id: int, *, visibility_timeout_seconds: int) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        self.archived.append(message_id)


class FakeTasks(TaskRepository):
    def __init__(self) -> None:
        self.record = task()
        self.slot_taken = False
        self.checkpoints: list[TaskCheckpoint] = []
        self.succeeded = False
        self.retry_calls = 0
        self.reclaim_calls = 0

    async def acquire_worker_slot(self, **kwargs: object) -> bool:
        del kwargs
        if self.slot_taken:
            return False
        self.slot_taken = True
        return True

    async def release_worker_slot(self, **kwargs: object) -> None:
        del kwargs
        self.slot_taken = False

    async def reclaim_stranded(self, **kwargs: object) -> tuple[UUID, ...]:
        self.reclaim_calls += 1
        return ()

    async def claim(self, **kwargs: object) -> TaskRecord | None:
        if kwargs["task_id"] != self.record.id:
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
        self.succeeded = True
        self.record = replace(self.record, status=TaskStatus.SUCCEEDED, lease_token=None)

    async def mark_superseded(self, **kwargs: object) -> None:
        del kwargs

    async def retry_or_fail(self, **kwargs: object) -> RetryDecision:
        del kwargs
        self.retry_calls += 1
        return RetryDecision(
            TaskStatus.RETRY_WAIT,
            self.retry_calls,
            NOW + timedelta(seconds=30),
            "retry",
        )


class Processor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.checkpoint: TaskCheckpoint | None = None

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint,
    ) -> StageResult:
        del task
        self.checkpoint = checkpoint or await save_checkpoint("PARSED", {"ok": True})
        if self.fail:
            return StageResult(WorkerOutcome.RETRY_WAIT, "provider unavailable")
        return StageResult()


@pytest.mark.asyncio
async def test_two_executions_allow_only_one_active_slot() -> None:
    tasks = FakeTasks()
    queue = FakeQueue()
    processor = Processor()
    worker = RunWorkerOnce(tasks, queue, processor, clock=lambda: NOW)
    first, second = await asyncio.gather(worker.run_once(), worker.run_once())
    assert sorted([first.outcome, second.outcome]) == [
        WorkerOutcome.NO_SLOT,
        WorkerOutcome.SUCCEEDED,
    ]
    assert tasks.succeeded is True
    assert queue.archived == [1]
    assert len(tasks.checkpoints) == 1
    # The recovery sweep runs once, for the worker that won the slot only;
    # the slot-less execution returns before sweeping.
    assert tasks.reclaim_calls == 1


@pytest.mark.asyncio
async def test_retry_keeps_delivery_unarchived_for_redelivery() -> None:
    tasks = FakeTasks()
    queue = FakeQueue()
    processor = Processor(fail=True)
    result = await RunWorkerOnce(tasks, queue, processor, clock=lambda: NOW).run_once()
    assert result.outcome == WorkerOutcome.RETRY_WAIT
    assert queue.archived == []
    assert tasks.retry_calls == 1
    assert processor.checkpoint is not None
