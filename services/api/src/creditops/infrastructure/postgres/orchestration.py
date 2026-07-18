"""Durable Postgres adapter for the Case Orchestrator.

Every write is bounded: task inserts are deduplicated by the case-scoped
idempotency key, gates are keyed by (case, version, gate type) and immutable
once satisfied, planner proposals and agent audit events are append-only.
Nothing here can write a fact, finding, or specialist conclusion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.orchestration.roles import CASE_ORCHESTRATOR_ROLE
from creditops.application.ports.orchestration import (
    BlockingGap,
    CreatedTask,
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_AGENT_TASK_TYPES = tuple(
    task_type.value
    for task_type in TaskType
    if task_type is not TaskType.DOCUMENT_INGESTION
)


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[DatabaseConnection]: ...


class PostgresOrchestrationRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "select case_version from public.credit_cases where id = %s",
                (case_id,),
            )
            case_row = await cursor.fetchone()
            if case_row is None:
                return None
            case_version = int(case_row[0])

            cursor = await connection.execute(
                """
                select exists (
                  select 1 from public.handoffs
                  where case_id = %s and case_version = %s
                    and state = 'READY_FOR_SPECIALIST_REVIEW'
                    and stale_at is null
                )
                """,
                (case_id, case_version),
            )
            handoff_row = await cursor.fetchone()
            has_handoff = bool(handoff_row and handoff_row[0])

            cursor = await connection.execute(
                """
                select id, task_type, case_version, status
                from public.processing_tasks
                where case_id = %s and task_type = any(%s)
                order by created_at
                """,
                (case_id, list(_AGENT_TASK_TYPES)),
            )
            tasks = tuple(
                OrchestrationTaskRow(
                    task_id=cast(UUID, row[0]),
                    task_type=TaskType(str(row[1])),
                    case_version=int(row[2]),
                    status=TaskStatus(str(row[3])),
                )
                for row in await cursor.fetchall()
            )

            cursor = await connection.execute(
                """
                select gate_type, case_version, status, satisfied_by_actor_id,
                       disposition_ref, satisfied_at
                from public.human_gates
                where case_id = %s and case_version = %s
                """,
                (case_id, case_version),
            )
            gates = tuple(_gate(row) for row in await cursor.fetchall())

            cursor = await connection.execute(
                """
                select id, affected_task_id, blocking_level
                from public.evidence_gaps
                where case_id = %s and case_version = %s
                  and status in ('PROVISIONAL', 'FORMAL')
                """,
                (case_id, case_version),
            )
            gaps = tuple(
                BlockingGap(
                    gap_id=cast(UUID, row[0]),
                    affected_task_id=cast(UUID, row[1]),
                    blocking_level=str(row[2]),
                )
                for row in await cursor.fetchall()
            )

        return OrchestrationSnapshot(
            case_id=case_id,
            case_version=case_version,
            has_intake_handoff=has_handoff,
            tasks=tasks,
            gates=gates,
            blocking_gaps=gaps,
        )

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
        async with self._connection_factory() as connection:
            async with connection.transaction():
                # Insert-if-absent, then satisfy only an OPEN gate.  A SATISFIED
                # gate is immutable (trigger-enforced); the engine may derive a
                # satisfaction only for G1 from the intake handoff.
                await connection.execute(
                    """
                    insert into public.human_gates (
                      case_id, case_version, gate_type, status
                    ) values (%s, %s, %s, 'OPEN')
                    on conflict (case_id, case_version, gate_type) do nothing
                    """,
                    (case_id, case_version, gate_type.value),
                )
                if status is GateStatus.SATISFIED:
                    await connection.execute(
                        """
                        update public.human_gates
                        set status = 'SATISFIED',
                            satisfied_by_actor_id = %s,
                            disposition_ref = %s,
                            satisfied_at = clock_timestamp()
                        where case_id = %s and case_version = %s
                          and gate_type = %s and status = 'OPEN'
                        """,
                        (
                            satisfied_by_actor_id,
                            disposition_ref,
                            case_id,
                            case_version,
                            gate_type.value,
                        ),
                    )
                cursor = await connection.execute(
                    """
                    select gate_type, case_version, status, satisfied_by_actor_id,
                           disposition_ref, satisfied_at
                    from public.human_gates
                    where case_id = %s and case_version = %s and gate_type = %s
                    """,
                    (case_id, case_version, gate_type.value),
                )
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("human gate row disappeared after upsert")
        return _gate(row)

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
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.processing_tasks (
                      id, case_id, case_version, document_version_id, task_type,
                      status, max_attempts, input_schema_version, input_payload,
                      idempotency_key
                    ) values (%s, %s, %s, null, %s, 'PENDING', 3, '1', %s, %s)
                    on conflict (case_id, idempotency_key) do nothing
                    returning id
                    """,
                    (
                        task_id,
                        case_id,
                        case_version,
                        task_type.value,
                        Jsonb(dict(input_payload)),
                        idempotency_key,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    cursor = await connection.execute(
                        """
                        select id, task_type, case_version, status
                        from public.processing_tasks
                        where case_id = %s and idempotency_key = %s
                        """,
                        (case_id, idempotency_key),
                    )
                    existing = await cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("task idempotency row disappeared")
                    return CreatedTask(
                        row=OrchestrationTaskRow(
                            task_id=cast(UUID, existing[0]),
                            task_type=TaskType(str(existing[1])),
                            case_version=int(existing[2]),
                            status=TaskStatus(str(existing[3])),
                        ),
                        created=False,
                    )
                for dependency_id in depends_on:
                    await connection.execute(
                        """
                        insert into public.task_dependencies (
                          case_id, case_version, task_id, depends_on_task_id
                        ) values (%s, %s, %s, %s)
                        on conflict (task_id, depends_on_task_id) do nothing
                        """,
                        (case_id, case_version, task_id, dependency_id),
                    )
        return CreatedTask(
            row=OrchestrationTaskRow(
                task_id=task_id,
                task_type=task_type,
                case_version=case_version,
                status=TaskStatus.PENDING,
            ),
            created=True,
        )

    async def record_proposal(
        self,
        *,
        proposal_id: UUID,
        case_id: UUID,
        case_version: int,
        execution_id: UUID,
        proposal: Mapping[str, object],
        status: str,
        validation_errors: tuple[str, ...],
        prompt_version: str,
        schema_version: str,
        model_version: str | None,
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into public.planner_proposals (
                      id, case_id, case_version, execution_id, proposal, status,
                      validation_errors, prompt_version, schema_version, model_version
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        proposal_id,
                        case_id,
                        case_version,
                        execution_id,
                        Jsonb(dict(proposal)),
                        status,
                        Jsonb(list(validation_errors)),
                        prompt_version,
                        schema_version,
                        model_version,
                    ),
                )

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        event_data = dict(event.event_data)
        event_data["executionId"] = str(event.execution_id)
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into public.audit_events (
                      case_id, case_version, event_type, actor_type, actor_id,
                      artifact_type, artifact_id, event_data
                    ) values (%s, %s, %s, %s, null, %s, %s, %s)
                    """,
                    (
                        event.case_id,
                        event.case_version,
                        event.event_type,
                        f"AGENT:{CASE_ORCHESTRATOR_ROLE}",
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(event_data),
                    ),
                )


def _gate(row: Sequence[Any]) -> GateRecord:
    return GateRecord(
        gate_type=GateType(str(row[0])),
        case_version=int(row[1]),
        status=GateStatus(str(row[2])),
        satisfied_by_actor_id=cast("UUID | None", row[3]),
        disposition_ref=cast("str | None", row[4]),
        satisfied_at=cast("datetime | None", row[5]),
    )
