from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

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
from creditops.domain.tasks import TaskEnvelopeV1

NOW = datetime(2026, 7, 18, 6, 0, tzinfo=UTC)
CASE = UUID("10000000-0000-0000-0000-000000000001")
DOC = UUID("20000000-0000-0000-0000-000000000001")
TASK = UUID("30000000-0000-0000-0000-000000000001")


class RedeliveringQueue:
    def __init__(self) -> None:
        self.message = QueueMessage(
            9,
            1,
            NOW,
            NOW,
            TaskEnvelopeV1(task_id=TASK, case_id=CASE, case_version=1, document_version_id=DOC),
        )
        self.archived = False

    async def send(self, envelope, *, delay_seconds=0):
        del envelope, delay_seconds
        return 10

    async def read_one(self, *, visibility_timeout_seconds):
        del visibility_timeout_seconds
        return None if self.archived else self.message

    async def extend_visibility(self, message_id, *, visibility_timeout_seconds):
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id):
        assert message_id == self.message.message_id
        self.archived = True


class ResumableTasks:
    def __init__(self) -> None:
        self.record = TaskRecord(
            id=TASK,
            case_id=CASE,
            case_version=1,
            document_version_id=DOC,
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
            available_at=NOW,
            lease_token=None,
            lease_until=None,
            input_schema_version="1",
            input_payload={"storageBucket": "creditops-originals"},
            idempotency_key="UPLOAD:resume",
        )
        self.slot = False
        self.checkpoints: list[TaskCheckpoint] = []

    async def acquire_worker_slot(self, **kwargs):
        del kwargs
        if self.slot:
            return False
        self.slot = True
        return True

    async def release_worker_slot(self, **kwargs):
        del kwargs
        self.slot = False

    async def reclaim_stranded(self, **kwargs):
        del kwargs
        return ()

    async def claim(self, *, lease_token, **kwargs):
        del kwargs
        if self.record.status not in (TaskStatus.PENDING, TaskStatus.RETRY_WAIT):
            return None
        self.record = replace(self.record, status=TaskStatus.RUNNING, lease_token=lease_token)
        return self.record

    async def get(self, task_id, *, case_id=None, actor_id=None):
        del case_id, actor_id
        return self.record if task_id == TASK else None

    async def latest_checkpoint(self, **kwargs):
        del kwargs
        return self.checkpoints[-1] if self.checkpoints else None

    async def checkpoint(self, **kwargs):
        checkpoint = TaskCheckpoint(
            task_id=kwargs["task_id"], case_id=kwargs["case_id"],
            case_version=kwargs["case_version"],
            document_version_id=kwargs["document_version_id"],
            sequence_no=kwargs["sequence_no"], checkpoint_type=kwargs["checkpoint_type"],
            checkpoint_schema_version=kwargs["checkpoint_schema_version"],
            checkpoint_data=kwargs["checkpoint_data"], created_at=NOW,
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    async def succeed(self, **kwargs):
        del kwargs
        self.record = replace(self.record, status=TaskStatus.SUCCEEDED)

    async def mark_superseded(self, **kwargs):
        del kwargs

    async def retry_or_fail(self, **kwargs):
        del kwargs
        self.record = replace(self.record, status=TaskStatus.RETRY_WAIT)
        return RetryDecision(
            TaskStatus.RETRY_WAIT,
            self.record.attempt_count,
            NOW + timedelta(seconds=30),
            "retry",
        )


class ResumeProcessor:
    def __init__(self) -> None:
        self.calls = 0

    async def process(self, task, checkpoint, save_checkpoint):
        del task
        self.calls += 1
        if checkpoint is None:
            await save_checkpoint("PARSED", {"stage": "parsed"})
            return StageResult(WorkerOutcome.RETRY_WAIT, "transient provider failure")
        assert checkpoint.checkpoint_type == "PARSED"
        return StageResult()


@pytest.mark.asyncio
async def test_redelivery_resumes_from_durable_checkpoint() -> None:
    tasks = ResumableTasks()
    queue = RedeliveringQueue()
    processor = ResumeProcessor()
    worker = RunWorkerOnce(tasks, queue, processor, clock=lambda: NOW)

    first = await worker.run_once()
    assert first.outcome == WorkerOutcome.RETRY_WAIT
    assert queue.archived is False
    assert len(tasks.checkpoints) == 1

    second = await worker.run_once()
    assert second.outcome == WorkerOutcome.SUCCEEDED
    assert processor.calls == 2
    assert queue.archived is True
