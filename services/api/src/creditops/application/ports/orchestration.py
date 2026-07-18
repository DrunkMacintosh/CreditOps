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


class StaleCaseVersionError(RuntimeError):
    """A version-bump lost the optimistic race: the case is no longer at the
    expected version.

    Raised by ``bump_case_version`` when the ``WHERE case_version = expected``
    guard matches no row -- another writer already advanced the case (e.g. a
    concurrent revision).  The maker-revision forward path treats this as a
    fail-closed no-op: the disposition is already durable, and nothing is
    bumped, re-issued, or scheduled on a version the caller never observed.
    """


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


@dataclass(frozen=True, slots=True)
class AuditEventRow:
    """One row of the immutable, append-only case audit trail (read side).

    Mirrors ``public.audit_events`` (see
    supabase/migrations/202607170002_cases_assignments_audit.sql). Rows span
    every case version -- the audit timeline is a whole-case history, not
    scoped to the latest version.  ``event_data`` passes through as-is: the
    writers only ever store metadata there, never secrets or prompts.
    """

    id: UUID
    case_id: UUID
    case_version: int
    event_type: str
    actor_type: str
    actor_id: UUID | None
    artifact_type: str
    artifact_id: UUID
    event_data: Mapping[str, object]
    created_at: datetime


class OrchestrationRepository(Protocol):
    async def load_snapshot(self, case_id: UUID) -> OrchestrationSnapshot | None: ...

    async def bump_case_version(
        self,
        case_id: UUID,
        *,
        expected_version: int,
        reason: str,
        disposition_ref: str,
        actor_id: UUID | None = None,
    ) -> int:
        """Optimistically bump the case version and re-issue the intake handoff.

        Atomic (single transaction, master design section 9's revision loop):

        1. ``update ... set case_version = case_version + 1
           where id = %s and case_version = %s`` -- if the guard matches no row
           the case moved on, so raise ``StaleCaseVersionError`` and do nothing
           else (fail closed);
        2. append one immutable ``CASE_VERSION_BUMPED`` audit row carrying
           ``reason`` and ``disposition_ref`` provenance at the NEW version;
        3. re-issue the intake handoff at the new version by cloning the latest
           ``READY_FOR_SPECIALIST_REVIEW`` handoff -- the evidence base is
           unchanged, so a revision invalidates the maker/risk analysis, not
           intake; without the re-issue G1 would be OPEN at the new version and
           nothing would schedule.

        Returns the new case version.  The bump automatically invalidates the
        old version's work: readiness fences every old-version task as
        superseded, and the version-bound gap-batch hash reopens G2 for the new
        version (re-disposition required, fail closed -- deliberate).
        """
        ...

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

    async def list_audit_events(
        self, case_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]: ...

    async def list_audit_events_all(
        self,
        *,
        cursor: UUID | None,
        limit: int,
        event_type: str | None = None,
    ) -> tuple[tuple[AuditEventRow, ...], UUID | None]:
        """Cross-case audit timeline (the read-only auditor surface, spec 17.3).

        The unscoped sibling of ``list_audit_events``: SAME keyset pagination
        contract (newest-first by ``(created_at, id)``, ``limit + 1`` next-page
        detection, cursor position resolved via a subselect in the SAME query),
        but NOT scoped to a single case -- it spans every case.  ``event_type``,
        when given, is an exact-match filter (its shape is validated at the API
        boundary against a conservative regex).  Read-only: it appends nothing.
        """
        ...
