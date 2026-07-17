"""Durable task and worker-slot repository.

Each mutation is a short transaction.  This is intentionally independent of
the case/upload UoW so a worker can recover a leased message after a process
crash without holding a browser transaction open.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.queue import (
    RetryDecision,
    StaleTaskError,
    TaskCheckpoint,
    TaskLeaseLost,
    TaskRecord,
    TaskRepository,
)
from creditops.domain.enums import TaskStatus
from creditops.infrastructure.postgres.repositories import DatabaseConnection


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[DatabaseConnection]: ...


class PostgresTaskRepository(TaskRepository):
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def acquire_worker_slot(
        self,
        *,
        lease_owner: UUID,
        lease_token: UUID,
        lease_until: datetime,
    ) -> bool:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "select public.try_acquire_worker_slot(%s, %s, %s)",
                    (lease_owner, lease_token, lease_until),
                )
                row = await cursor.fetchone()
        return bool(row and row[0])

    async def release_worker_slot(self, *, lease_owner: UUID, lease_token: UUID) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    update public.worker_slots
                    set lease_owner = null, lease_token = null, lease_until = null,
                        updated_at = clock_timestamp()
                    where slot_no = 1 and lease_owner = %s and lease_token = %s
                    """,
                    (lease_owner, lease_token),
                )

    async def extend_worker_slot(
        self,
        *,
        lease_owner: UUID,
        lease_token: UUID,
        lease_until: datetime,
    ) -> bool:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    update public.worker_slots
                    set lease_until = %s, updated_at = clock_timestamp()
                    where slot_no = 1 and lease_owner = %s and lease_token = %s
                      and lease_until > statement_timestamp()
                    returning slot_no
                    """,
                    (lease_until, lease_owner, lease_token),
                )
                row = await cursor.fetchone()
        return row is not None

    async def claim(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        lease_token: UUID,
        lease_until: datetime,
    ) -> TaskRecord | None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    update public.processing_tasks as task
                    set status = 'RUNNING', attempt_count = task.attempt_count + 1,
                        lease_token = %s, lease_until = %s,
                        updated_at = clock_timestamp()
                    where task.id = %s
                      and task.case_id = %s
                      and task.case_version = %s
                      and task.document_version_id = %s
                      and task.status in ('PENDING', 'RETRY_WAIT')
                      and task.available_at <= statement_timestamp()
                      and (task.lease_until is null or task.lease_until <= statement_timestamp())
                      and exists (
                        select 1 from public.credit_cases as current_case
                        where current_case.id = task.case_id
                          and current_case.case_version = task.case_version
                      )
                      and exists (
                        select 1 from public.document_versions as version
                        where version.id = task.document_version_id
                          and version.case_id = task.case_id
                          and version.case_version = task.case_version
                          and version.stale_at is null
                      )
                    returning task.id, task.case_id, task.case_version,
                              task.document_version_id, task.status,
                              task.attempt_count, task.max_attempts,
                              task.available_at, task.lease_token, task.lease_until,
                              task.input_schema_version, task.input_payload,
                              task.idempotency_key
                    """,
                    (
                        lease_token,
                        lease_until,
                        task_id,
                        case_id,
                        case_version,
                        document_version_id,
                    ),
                )
                row = await cursor.fetchone()
        return None if row is None else _task(row)

    async def get(
        self,
        task_id: UUID,
        *,
        case_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> TaskRecord | None:
        predicates = ["task.id = %s"]
        params: list[object] = [task_id]
        if case_id is not None:
            predicates.append("task.case_id = %s")
            params.append(case_id)
        if actor_id is not None:
            predicates.append(
                "exists (select 1 from public.case_assignments assignment "
                "where assignment.case_id = task.case_id and assignment.officer_id = %s "
                "and assignment.revoked_at is null)"
            )
            params.append(actor_id)
        query = f"""
            select task.id, task.case_id, task.case_version,
                   task.document_version_id, task.status,
                   task.attempt_count, task.max_attempts, task.available_at,
                   task.lease_token, task.lease_until, task.input_schema_version,
                   task.input_payload, task.idempotency_key
            from public.processing_tasks as task
            where {' and '.join(predicates)}
        """
        async with self._connection_factory() as connection:
            cursor = await connection.execute(query, tuple(params))
            row = await cursor.fetchone()
        return None if row is None else _task(row)

    async def latest_checkpoint(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
    ) -> TaskCheckpoint | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select checkpoint.task_id, checkpoint.case_id, checkpoint.case_version,
                       checkpoint.document_version_id, checkpoint.sequence_no,
                       checkpoint.checkpoint_type, checkpoint.checkpoint_schema_version,
                       checkpoint.checkpoint_data, checkpoint.created_at
                from public.task_checkpoints as checkpoint
                where checkpoint.task_id = %s and checkpoint.case_id = %s
                  and checkpoint.case_version = %s
                  and checkpoint.document_version_id = %s
                order by checkpoint.sequence_no desc
                limit 1
                """,
                (task_id, case_id, case_version, document_version_id),
            )
            row = await cursor.fetchone()
        return None if row is None else _checkpoint(row)

    async def checkpoint(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        lease_token: UUID,
        sequence_no: int,
        checkpoint_type: str,
        checkpoint_schema_version: str,
        checkpoint_data: Mapping[str, object],
    ) -> TaskCheckpoint:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.task_checkpoints (
                      task_id, case_id, case_version, document_version_id,
                      sequence_no, checkpoint_type, checkpoint_schema_version,
                      checkpoint_data
                    )
                    select task.id, task.case_id, task.case_version,
                           task.document_version_id, %s, %s, %s, %s
                    from public.processing_tasks as task
                    join public.credit_cases as current_case
                      on current_case.id = task.case_id
                     and current_case.case_version = task.case_version
                    join public.document_versions as version
                      on version.id = task.document_version_id
                     and version.case_id = task.case_id
                     and version.case_version = task.case_version
                     and version.stale_at is null
                    where task.id = %s and task.case_id = %s
                      and task.case_version = %s and task.document_version_id = %s
                      and task.status = 'RUNNING'
                      and task.lease_token = %s
                      and task.lease_until > statement_timestamp()
                    returning task_id, case_id, case_version, document_version_id,
                              sequence_no, checkpoint_type,
                              checkpoint_schema_version, checkpoint_data, created_at
                    """,
                    (
                        sequence_no,
                        checkpoint_type,
                        checkpoint_schema_version,
                        Jsonb(dict(checkpoint_data)),
                        task_id,
                        case_id,
                        case_version,
                        document_version_id,
                        lease_token,
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            raise StaleTaskError("task or document version is no longer current")
        return _checkpoint(row)

    async def succeed(self, **kwargs: object) -> None:
        await self._terminal_update("SUCCEEDED", **kwargs)

    async def mark_superseded(self, *, reason: str, **kwargs: object) -> None:
        del reason
        await self._terminal_update("SUPERSEDED", **kwargs)

    async def _terminal_update(self, status: str, **kwargs: object) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    update public.processing_tasks
                    set status = %s, completed_at = clock_timestamp(),
                        lease_token = null, lease_until = null,
                        updated_at = clock_timestamp()
                    where id = %s and case_id = %s and case_version = %s
                      and document_version_id = %s and status = 'RUNNING'
                      and lease_token = %s and lease_until > statement_timestamp()
                    returning id
                    """,
                    (
                        status,
                        kwargs["task_id"],
                        kwargs["case_id"],
                        kwargs["case_version"],
                        kwargs["document_version_id"],
                        kwargs["lease_token"],
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            raise TaskLeaseLost("task lease is no longer owned")

    async def retry_or_fail(
        self,
        *,
        task_id: UUID,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        lease_token: UUID,
        reason: str,
        now: datetime,
        base_delay_seconds: int,
    ) -> RetryDecision:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    update public.processing_tasks
                    set status = case when attempt_count >= max_attempts
                                      then 'FAILED_MANUAL_REVIEW' else 'RETRY_WAIT' end,
                        failure_reason = %s,
                        available_at = case when attempt_count >= max_attempts
                                            then available_at
                                            else %s + (
                                              %s * power(2, greatest(attempt_count - 1, 0))
                                            ) * interval '1 second' end,
                        lease_token = null, lease_until = null,
                        updated_at = clock_timestamp(),
                        completed_at = case when attempt_count >= max_attempts
                                           then clock_timestamp() else null end
                    where id = %s and case_id = %s and case_version = %s
                      and document_version_id = %s and status = 'RUNNING'
                      and lease_token = %s and lease_until > statement_timestamp()
                    returning status, attempt_count, available_at
                    """,
                    (
                        reason,
                        now,
                        base_delay_seconds,
                        task_id,
                        case_id,
                        case_version,
                        document_version_id,
                        lease_token,
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            raise TaskLeaseLost("task lease is no longer owned")
        return RetryDecision(TaskStatus(str(row[0])), int(row[1]), row[2], reason)


def _task(row: Sequence[Any]) -> TaskRecord:
    payload = row[11]
    if not isinstance(payload, dict):
        raise ValueError("task payload is not an object")
    return TaskRecord(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(row[2]),
        document_version_id=cast(UUID, row[3]),
        status=TaskStatus(str(row[4])),
        attempt_count=int(row[5]),
        max_attempts=int(row[6]),
        available_at=cast(datetime, row[7]),
        lease_token=cast(UUID | None, row[8]),
        lease_until=cast(datetime | None, row[9]),
        input_schema_version=str(row[10]),
        input_payload=payload,
        idempotency_key=str(row[12]),
    )


def _checkpoint(row: Sequence[Any]) -> TaskCheckpoint:
    data = row[7]
    if not isinstance(data, dict):
        raise ValueError("checkpoint payload is not an object")
    return TaskCheckpoint(
        task_id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(row[2]),
        document_version_id=cast(UUID, row[3]),
        sequence_no=int(row[4]),
        checkpoint_type=str(row[5]),
        checkpoint_schema_version=str(row[6]),
        checkpoint_data=data,
        created_at=cast(datetime, row[8]),
    )
