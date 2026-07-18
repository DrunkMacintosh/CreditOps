"""Execute at most one identifier-only queue message with durable state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID, uuid4

from creditops.application.ports.queue import (
    QueueMessage,
    QueuePort,
    StaleTaskError,
    TaskCheckpoint,
    TaskLeaseLost,
    TaskRecord,
    TaskRepository,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType


class WorkerOutcome(StrEnum):
    NO_SLOT = "NO_SLOT"
    NO_MESSAGE = "NO_MESSAGE"
    SUCCEEDED = "SUCCEEDED"
    SUPERSEDED = "SUPERSEDED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED_MANUAL_REVIEW = "FAILED_MANUAL_REVIEW"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class StageResult:
    status: WorkerOutcome = WorkerOutcome.SUCCEEDED
    reason: str = ""


CheckpointCallback = Callable[
    [str, Mapping[str, object]], Awaitable[TaskCheckpoint]
]


class TaskProcessor(Protocol):
    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult: ...


class TaskProcessorRegistry(Protocol):
    """Resolve the processor for a task type, one processor per finite type.

    A registry must always return a processor.  A type with no registered
    processor resolves to a manual-review processor so an unknown task fails
    closed (FAILED_MANUAL_REVIEW with audit) rather than crashing the worker.
    """

    def processor_for(self, task_type: TaskType) -> TaskProcessor: ...


class _SingleProcessorRegistry:
    """Backward-compatible adapter: one processor handles every task type.

    Existing callers that inject a single ``TaskProcessor`` keep their exact
    behaviour; the worker still dispatches through the registry seam.
    """

    def __init__(self, processor: TaskProcessor) -> None:
        self._processor = processor

    def processor_for(self, task_type: TaskType) -> TaskProcessor:
        del task_type
        return self._processor


def _as_registry(
    processor: TaskProcessor | TaskProcessorRegistry,
) -> TaskProcessorRegistry:
    if hasattr(processor, "processor_for"):
        return cast(TaskProcessorRegistry, processor)
    return _SingleProcessorRegistry(processor)


class WorkerRunError(RuntimeError):
    """Unexpected worker failure; durable retry policy still applies."""


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    outcome: WorkerOutcome
    task_id: UUID | None = None
    message_id: int | None = None
    reason: str | None = None


class RunWorkerOnce:
    """Claim the single global slot, read one message, and release it safely.

    The queue message is never archived before the corresponding durable task
    transition succeeds.  A redelivery therefore resumes from the latest
    checkpoint and cannot silently duplicate a completed effect.
    """

    def __init__(
        self,
        tasks: TaskRepository,
        queue: QueuePort,
        processor: TaskProcessor | TaskProcessorRegistry,
        *,
        worker_id: UUID | None = None,
        clock: Callable[[], datetime] | None = None,
        slot_lease_seconds: int = 300,
        visibility_timeout_seconds: int = 300,
        retry_base_delay_seconds: int = 30,
    ) -> None:
        if slot_lease_seconds <= 0 or visibility_timeout_seconds <= 0:
            raise ValueError("worker lease durations must be positive")
        if retry_base_delay_seconds <= 0:
            raise ValueError("retry base delay must be positive")
        self._tasks = tasks
        self._queue = queue
        self._registry = _as_registry(processor)
        self._worker_id = worker_id or uuid4()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._slot_lease_seconds = slot_lease_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._retry_base_delay_seconds = retry_base_delay_seconds

    async def run_once(self) -> WorkerRunResult:
        lease_token = uuid4()
        now = self._clock()
        acquired = await self._tasks.acquire_worker_slot(
            lease_owner=self._worker_id,
            lease_token=lease_token,
            lease_until=now + timedelta(seconds=self._slot_lease_seconds),
        )
        if not acquired:
            return WorkerRunResult(WorkerOutcome.NO_SLOT)

        message: QueueMessage | None = None
        try:
            message = await self._queue.read_one(
                visibility_timeout_seconds=self._visibility_timeout_seconds
            )
            if message is None:
                return WorkerRunResult(WorkerOutcome.NO_MESSAGE)
            envelope = message.envelope
            task = await self._tasks.claim(
                task_id=envelope.task_id,
                case_id=envelope.case_id,
                case_version=envelope.case_version,
                document_version_id=envelope.document_version_id,
                lease_token=lease_token,
                lease_until=self._clock() + timedelta(seconds=self._slot_lease_seconds),
            )
            if task is None:
                # A missing claim is not proof of durable terminal success:
                # it can also be a stale-version race or another worker's
                # lease.  Archive only when a second delivery observes an
                # already durable terminal state; stale/racing tasks remain
                # recoverable.
                existing = await self._tasks.get(
                    envelope.task_id,
                    case_id=envelope.case_id,
                )
                if existing is not None and existing.status in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.SUPERSEDED,
                    TaskStatus.FAILED_MANUAL_REVIEW,
                }:
                    await self._queue.archive(message.message_id)
                return WorkerRunResult(
                    WorkerOutcome.STALE,
                    task_id=envelope.task_id,
                    message_id=message.message_id,
                    reason="task is no longer claimable",
                )

            claimed_task = task
            async def heartbeat() -> None:
                # Test doubles may inherit the Protocol (whose method body is
                # only an ellipsis); call the heartbeat only when the concrete
                # adapter implements it.
                extend_slot = type(self._tasks).__dict__.get("extend_worker_slot")
                if callable(extend_slot):
                    renewed = await extend_slot(
                        lease_owner=self._worker_id,
                        lease_token=lease_token,
                        lease_until=self._clock() + timedelta(seconds=self._slot_lease_seconds),
                    )
                    if not renewed:
                        raise TaskLeaseLost("worker slot lease is no longer owned")
                await self._queue.extend_visibility(
                    message.message_id,
                    visibility_timeout_seconds=self._visibility_timeout_seconds,
                )

            await heartbeat()
            latest = await self._tasks.latest_checkpoint(
                task_id=claimed_task.id,
                case_id=claimed_task.case_id,
                case_version=claimed_task.case_version,
                document_version_id=claimed_task.document_version_id,
            )

            async def save_checkpoint(
                checkpoint_type: str,
                checkpoint_data: Mapping[str, object],
            ) -> TaskCheckpoint:
                nonlocal latest
                sequence_no = 1 if latest is None else latest.sequence_no + 1
                # Update the local sequence through a closure so processors can
                # persist more than one stage in a single execution.
                latest = await self._tasks.checkpoint(
                    task_id=claimed_task.id,
                    case_id=claimed_task.case_id,
                    case_version=claimed_task.case_version,
                    document_version_id=claimed_task.document_version_id,
                    lease_token=lease_token,
                    sequence_no=sequence_no,
                    checkpoint_type=checkpoint_type,
                    checkpoint_schema_version="1",
                    checkpoint_data=checkpoint_data,
                )
                await heartbeat()
                return latest

            processor = self._registry.processor_for(claimed_task.task_type)
            result = await processor.process(claimed_task, latest, save_checkpoint)
            if result.status == WorkerOutcome.SUPERSEDED:
                await self._tasks.mark_superseded(
                    task_id=claimed_task.id,
                    case_id=claimed_task.case_id,
                    case_version=claimed_task.case_version,
                    document_version_id=claimed_task.document_version_id,
                    lease_token=lease_token,
                    reason=result.reason or "input version superseded",
                )
                await self._queue.archive(message.message_id)
                return WorkerRunResult(
                    WorkerOutcome.SUPERSEDED, claimed_task.id, message.message_id, result.reason
                )
            if result.status != WorkerOutcome.SUCCEEDED:
                decision = await self._tasks.retry_or_fail(
                    task_id=claimed_task.id,
                    case_id=claimed_task.case_id,
                    case_version=claimed_task.case_version,
                    document_version_id=claimed_task.document_version_id,
                    lease_token=lease_token,
                    reason=result.reason or "processor requested retry",
                    now=self._clock(),
                    base_delay_seconds=self._retry_base_delay_seconds,
                )
                outcome = (
                    WorkerOutcome.RETRY_WAIT
                    if decision.status == TaskStatus.RETRY_WAIT
                    else WorkerOutcome.FAILED_MANUAL_REVIEW
                )
                if decision.status == TaskStatus.FAILED_MANUAL_REVIEW:
                    await self._queue.archive(message.message_id)
                return WorkerRunResult(
                    outcome, claimed_task.id, message.message_id, decision.reason
                )

            await self._tasks.succeed(
                task_id=claimed_task.id,
                case_id=claimed_task.case_id,
                case_version=claimed_task.case_version,
                document_version_id=claimed_task.document_version_id,
                lease_token=lease_token,
            )
            await self._queue.archive(message.message_id)
            return WorkerRunResult(WorkerOutcome.SUCCEEDED, claimed_task.id, message.message_id)
        except (StaleTaskError, TaskLeaseLost):
            # The old delivery remains recoverable unless the database marked
            # it terminal.  Never archive a message after a stale write.
            return WorkerRunResult(
                WorkerOutcome.STALE,
                task_id=message.envelope.task_id if message else None,
                message_id=message.message_id if message else None,
                reason="task version or lease is stale",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if message is None:
                raise WorkerRunError("worker failed before receiving a message") from exc
            # Recoverable processor/provider errors are converted into the
            # same bounded retry state as an explicit retry result.  If the
            # lease was lost, the stale-write exception above preserves the
            # redelivery instead of mutating a newer worker's task.
            try:
                task = await self._tasks.get(
                    message.envelope.task_id,
                    case_id=message.envelope.case_id,
                )
                if task is not None and task.lease_token == lease_token:
                    decision = await self._tasks.retry_or_fail(
                        task_id=task.id,
                        case_id=task.case_id,
                        case_version=task.case_version,
                        document_version_id=task.document_version_id,
                        lease_token=lease_token,
                        reason="worker exception",
                        now=self._clock(),
                        base_delay_seconds=self._retry_base_delay_seconds,
                    )
                    if decision.status == TaskStatus.FAILED_MANUAL_REVIEW:
                        await self._queue.archive(message.message_id)
                    return WorkerRunResult(
                        WorkerOutcome.RETRY_WAIT
                        if decision.status == TaskStatus.RETRY_WAIT
                        else WorkerOutcome.FAILED_MANUAL_REVIEW,
                        task.id,
                        message.message_id,
                        "worker exception",
                    )
            except (StaleTaskError, TaskLeaseLost):
                pass
            raise WorkerRunError("worker processing failed") from exc
        finally:
            await self._tasks.release_worker_slot(
                lease_owner=self._worker_id,
                lease_token=lease_token,
            )
