"""Durable queue and task-lease contracts.

The queue carries only the immutable identifiers needed to resume a task.  It
is deliberately separate from the stage processor: a provider failure must
be represented by durable task state, never by an in-memory retry loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1


class QueueError(RuntimeError):
    """The queue could not perform a durable operation."""


class QueueNotConfigured(QueueError):
    """The managed queue is intentionally unavailable."""


@dataclass(frozen=True, slots=True)
class QueueMessage:
    message_id: int
    read_count: int
    enqueued_at: datetime
    visible_at: datetime
    envelope: TaskEnvelopeV1


class QueuePort(Protocol):
    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int: ...

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None: ...

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None: ...

    async def archive(self, message_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: UUID
    case_id: UUID
    case_version: int
    document_version_id: UUID | None
    status: TaskStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_token: UUID | None
    lease_until: datetime | None
    input_schema_version: str
    input_payload: Mapping[str, object]
    idempotency_key: str
    task_type: TaskType = TaskType.DOCUMENT_INGESTION


@dataclass(frozen=True, slots=True)
class TaskCheckpoint:
    task_id: UUID
    case_id: UUID
    case_version: int
    document_version_id: UUID | None
    sequence_no: int
    checkpoint_type: str
    checkpoint_schema_version: str
    checkpoint_data: Mapping[str, object]
    created_at: datetime


class StaleTaskError(RuntimeError):
    """The task no longer targets the current case/document version."""


class TaskLeaseLost(RuntimeError):
    """A worker attempted a write after its task lease expired."""


class TaskNotClaimed(RuntimeError):
    """The task is already owned by another worker or is terminal."""


@dataclass(frozen=True, slots=True)
class RetryDecision:
    status: TaskStatus
    attempt_count: int
    available_at: datetime | None
    reason: str


class TaskRepository(Protocol):
    async def acquire_worker_slot(
        self,
        *,
        lease_owner: UUID,
        lease_token: UUID,
        lease_until: datetime,
    ) -> bool: ...

    async def release_worker_slot(self, *, lease_owner: UUID, lease_token: UUID) -> None: ...

    async def extend_worker_slot(
        self,
        *,
        lease_owner: UUID,
        lease_token: UUID,
        lease_until: datetime,
    ) -> bool: ...

    async def claim(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        lease_until: datetime,
    ) -> TaskRecord | None: ...

    async def get(
        self,
        task_id: UUID,
        *,
        case_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> TaskRecord | None: ...

    async def latest_checkpoint(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
    ) -> TaskCheckpoint | None: ...

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
    ) -> TaskCheckpoint: ...

    async def succeed(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
    ) -> None: ...

    async def mark_superseded(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID | None,
        lease_token: UUID,
        reason: str,
    ) -> None: ...

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
    ) -> RetryDecision: ...


@dataclass(frozen=True, slots=True)
class TaskStatusView:
    id: UUID
    case_id: UUID
    case_version: int
    document_version_id: UUID | None
    status: TaskStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    checkpoint: TaskCheckpoint | None
