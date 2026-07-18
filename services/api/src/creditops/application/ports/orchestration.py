"""Durable state contracts for the Case Orchestrator.

The repository exposes only what the deterministic engine needs to read case
state and to make its bounded writes: create tasks, create/refresh human gates,
append planner-proposal history, and append agent audit events.  It never
exposes a way to write a fact, finding, or specialist conclusion, or to resolve
a gap, conflict, or challenge.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import GateStatus, GateType, TaskType


@dataclass(frozen=True, slots=True)
class OrchestrationTaskRow:
    task_id: UUID
    task_type: TaskType
    case_version: int
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class GateRecord:
    gate_type: GateType
    case_version: int
    status: GateStatus
    satisfied_by_actor_id: UUID | None = None
    disposition_ref: str | None = None
    satisfied_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BlockingGap:
    """An unresolved evidence gap that blocks the task it is attached to."""

    gap_id: UUID
    affected_task_id: UUID
    blocking_level: str  # BLOCKING | CONDITIONAL | CLARIFICATION


@dataclass(frozen=True, slots=True)
class OrchestrationSnapshot:
    case_id: UUID
    case_version: int
    has_intake_handoff: bool
    tasks: tuple[OrchestrationTaskRow, ...] = ()
    gates: tuple[GateRecord, ...] = ()
    blocking_gaps: tuple[BlockingGap, ...] = ()


@dataclass(frozen=True, slots=True)
class OrchestrationAuditEvent:
    case_id: UUID
    case_version: int
    event_type: str
    execution_id: UUID
    artifact_type: str
    artifact_id: UUID
    event_data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CreatedTask:
    row: OrchestrationTaskRow
    created: bool


@dataclass(frozen=True, slots=True)
class OutboxEventRow:
    """One transactional-outbox event awaiting (or after) queue dispatch.

    Written atomically with its domain mutation (master design section
    14.2); the queue send happens only afterwards, from this row.  The
    payload is the schema-versioned envelope to publish -- identifiers only,
    never a document body or secret.
    """

    event_id: UUID
    case_id: UUID
    case_version: int
    event_type: str
    payload: Mapping[str, object]
    dispatch_attempts: int = 0
    dispatched_at: datetime | None = None


class OrchestrationRepository(Protocol):
    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None: ...

    async def ensure_gate(
        self,
        *,
        case_id: UUID,
        case_version: int,
        gate_type: GateType,
        status: GateStatus,
        satisfied_by_actor_id: UUID | None = None,
        disposition_ref: str | None = None,
    ) -> GateRecord: ...

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
    ) -> CreatedTask: ...

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
    ) -> None: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None: ...

    async def load_undispatched_outbox(self, *, limit: int) -> tuple[OutboxEventRow, ...]: ...

    async def mark_outbox_dispatched(self, event_id: UUID) -> None: ...

    async def record_outbox_dispatch_failure(self, event_id: UUID) -> None: ...
